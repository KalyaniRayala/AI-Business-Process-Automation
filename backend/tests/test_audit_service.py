import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError

from app.services import audit_service
from app.services.audit_service import (
    MAX_DETAILS_BYTES,
    AuditEventType,
    add_event,
    list_events,
    record_event,
    sanitize_details,
)
from app.services.database_service import create_workflow_run, get_workflow_run
from app.services.db import engine, session_scope
from app.services.db_models import AuditEvent


def new_thread_id() -> str:

    return f"audit-test-{uuid.uuid4()}"


# ============================================================
# SCHEMA
# ============================================================

def test_audit_events_table_and_indexes_exist():

    inspector = inspect(engine)

    assert "audit_events" in inspector.get_table_names()

    index_names = {
        index["name"] for index in inspector.get_indexes("audit_events")
    }

    assert {
        "ix_audit_events_thread_id_occurred_at",
        "ix_audit_events_event_type_occurred_at",
        "ix_audit_events_entity",
        "ix_audit_events_occurred_at",
    } <= index_names


def test_database_rejects_invalid_status_and_actor_type():

    for field, value in (("status", "bogus"), ("actor_type", "robot")):

        values = {
            "event_type": "workflow_started",
            "message": "m",
            "status": "info",
            "actor_type": "system",
            field: value,
        }

        with pytest.raises(IntegrityError):
            with session_scope() as session:
                session.add(AuditEvent(**values))


# ============================================================
# WRITE
# ============================================================

def test_record_and_list_event_round_trip():

    thread_id = new_thread_id()

    assert record_event(
        event_type=AuditEventType.APPROVAL_REQUESTED,
        message="Approval requested.",
        status="pending",
        thread_id=thread_id,
        node="approval",
        entity_type="employee",
        entity_id="E102",
        details={"expense_amount": 750.0, "approval": "Manager approval required."}
    ) is True

    [event] = list_events(thread_id=thread_id)

    assert event["event_type"] == "approval_requested"
    assert event["status"] == "pending"
    assert event["actor_type"] == "system"
    assert event["actor_id"] is None
    assert event["node"] == "approval"
    assert (event["entity_type"], event["entity_id"]) == ("employee", "E102")
    assert event["details"] == {
        "expense_amount": 750.0,
        "approval": "Manager approval required."
    }
    assert uuid.UUID(event["event_id"])
    assert datetime.fromisoformat(event["occurred_at"])


def test_add_event_commits_and_rolls_back_with_business_change():

    committed = new_thread_id()

    with session_scope() as session:
        add_event(
            session,
            event_type="workflow_started",
            message="Started.",
            thread_id=committed
        )

    assert len(list_events(thread_id=committed)) == 1

    rolled_back = new_thread_id()

    with pytest.raises(RuntimeError):
        with session_scope() as session:
            add_event(
                session,
                event_type="workflow_started",
                message="Started.",
                thread_id=rolled_back
            )
            raise RuntimeError("business change failed")

    assert list_events(thread_id=rolled_back) == []


def test_event_and_business_row_share_one_transaction():
    """A failing business write discards its audit event too."""

    thread_id = new_thread_id()

    create_workflow_run(thread_id, "question")

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            add_event(
                session,
                event_type="business_action_executed",
                message="Recorded.",
                thread_id=thread_id
            )
            # Violates the actor_type check constraint.
            session.add(AuditEvent(
                event_type="workflow_error",
                message="m",
                status="info",
                actor_type="robot"
            ))

    # Only the rolled-back event is checked: creating the run
    # records its own workflow_started event.
    assert list_events(
        thread_id=thread_id,
        event_type="business_action_executed"
    ) == []
    assert get_workflow_run(thread_id) is not None


def test_record_event_is_best_effort_on_database_errors(monkeypatch, caplog):

    def broken_session():
        raise OperationalError("INSERT", {}, Exception("db down"))

    monkeypatch.setattr(audit_service, "session_scope", broken_session)

    assert record_event(
        event_type="workflow_error",
        message="Something failed.",
        status="failure"
    ) is False

    assert "Could not record audit event workflow_error" in caplog.text


@pytest.mark.parametrize(
    "arguments",
    [
        {"event_type": "not_a_real_event"},
        {"event_type": "workflow_started", "status": "done"},
        {"event_type": "workflow_started", "actor_type": "robot"},
    ],
)
def test_invalid_arguments_raise(arguments):

    with pytest.raises(ValueError):
        with session_scope() as session:
            add_event(session, message="m", **arguments)

    with pytest.raises(ValueError):
        record_event(message="m", **arguments)


def test_error_information_is_captured_and_bounded():

    thread_id = new_thread_id()

    record_event(
        event_type="workflow_error",
        message="Graph failed.",
        status="failure",
        thread_id=thread_id,
        error=ValueError("x" * 5000)
    )

    [event] = list_events(thread_id=thread_id)

    assert event["error_type"] == "ValueError"
    assert len(event["error_message"]) == 2000


# ============================================================
# DETAILS SANITIZATION
# ============================================================

def test_details_redact_sensitive_keys_at_any_depth():

    details = sanitize_details({
        "database_password": "p",
        "nested": {"API_KEY": "k", "auth": {"token": "t"}},
        "items": [{"client_secret": "s", "ok": 1}],
        "amount": 750,
    })

    assert details == {
        "database_password": "[redacted]",
        "nested": {"API_KEY": "[redacted]", "auth": {"token": "[redacted]"}},
        "items": [{"client_secret": "[redacted]", "ok": 1}],
        "amount": 750,
    }


def test_details_convert_non_json_values():

    moment = datetime(2026, 9, 30, tzinfo=timezone.utc)

    details = sanitize_details({
        "amount": Decimal("750.00"),
        "at": moment,
    })

    assert details == {"amount": "750.00", "at": str(moment)}


def test_oversized_details_are_summarized():

    details = sanitize_details({"blob": "x" * (MAX_DETAILS_BYTES + 1)})

    assert details["truncated"] is True
    assert details["keys"] == ["blob"]
    assert details["original_size_bytes"] > MAX_DETAILS_BYTES


# ============================================================
# READ
# ============================================================

def test_list_events_orders_filters_and_paginates():

    thread_id = new_thread_id()

    for event_type in (
        "workflow_started",
        "employee_lookup",
        "policy_evaluated",
        "workflow_completed",
    ):
        record_event(
            event_type=event_type,
            message=event_type,
            thread_id=thread_id,
            entity_type="employee" if event_type == "employee_lookup" else None,
            entity_id="E102" if event_type == "employee_lookup" else None
        )

    events = list_events(thread_id=thread_id)

    assert [event["event_type"] for event in events] == [
        "workflow_started",
        "employee_lookup",
        "policy_evaluated",
        "workflow_completed",
    ]

    assert [
        event["event_type"]
        for event in list_events(thread_id=thread_id, event_type="policy_evaluated")
    ] == ["policy_evaluated"]

    assert [
        event["event_type"]
        for event in list_events(
            thread_id=thread_id, entity_type="employee", entity_id="E102"
        )
    ] == ["employee_lookup"]

    first_page = list_events(thread_id=thread_id, limit=2)
    second_page = list_events(
        thread_id=thread_id, after_id=first_page[-1]["id"], limit=2
    )

    assert [e["id"] for e in first_page + second_page] == [
        e["id"] for e in events
    ]

    # Limits are clamped to at least one event.
    assert len(list_events(thread_id=thread_id, limit=0)) == 1


def test_list_events_rejects_unknown_event_type_filter():

    with pytest.raises(ValueError):
        list_events(event_type="not_a_real_event")
