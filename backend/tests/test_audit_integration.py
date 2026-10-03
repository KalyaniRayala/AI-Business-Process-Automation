"""
Audit events recorded by the existing workflow paths.

Each test drives the real API or services (with the stubbed RAG/LLM
from conftest) and checks the exact, ordered audit timeline.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.main import app
from app.services import agent_graph as agent_graph_module
from app.services import audit_service, business_action_service
from app.services.audit_service import list_events
from app.services.business_action_service import (
    find_reimbursement,
    record_expense_reimbursement,
)
from app.services.database_service import record_approval_decision
from app.services.db import session_scope
from app.services.db_models import ApprovalDecision, AuditEvent


QUESTION_400 = "For employee E101, process a $400 expense."
QUESTION_750 = "For employee E102, process a $750 expense."


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


def start(client, question: str) -> dict:

    response = client.post("/workflows", json={"question": question})

    assert response.status_code == 201

    return response.json()


def timeline(thread_id: str) -> list[str]:
    """
    Workflow-level events only; node_* events are covered by
    test_node_audit.py.
    """

    return [
        event["event_type"]
        for event in list_events(thread_id=thread_id)
        if not event["event_type"].startswith("node_")
    ]


def event(thread_id: str, event_type: str) -> dict:

    [found] = list_events(thread_id=thread_id, event_type=event_type)

    return found


# ============================================================
# WORKFLOW PATHS
# ============================================================

def test_no_approval_path(client):

    body = start(client, QUESTION_400)
    thread_id = body["thread_id"]

    assert timeline(thread_id) == [
        "workflow_started",
        "business_action_executed",
        "workflow_completed",
    ]

    executed = event(thread_id, "business_action_executed")

    assert executed["entity_type"] == "reimbursement"
    assert executed["entity_id"] == (
        body["action_result"]["reimbursement"]["transaction_id"]
    )
    assert executed["details"] == {"employee_id": "E101", "amount": 400.0}
    assert executed["actor_type"] == "system"
    assert executed["node"] == "action"

    completed = event(thread_id, "workflow_completed")

    assert completed["status"] == "success"
    assert completed["details"] == {"approval_status": "not_required"}

    started = event(thread_id, "workflow_started")

    assert (started["entity_type"], started["entity_id"]) == (
        "workflow_run", thread_id
    )
    # The question is not copied into the audit trail.
    assert started["details"] is None


def test_approved_path(client):

    thread_id = start(client, QUESTION_750)["thread_id"]

    assert timeline(thread_id) == ["workflow_started", "approval_requested"]

    requested = event(thread_id, "approval_requested")

    assert requested["status"] == "pending"
    assert (requested["entity_type"], requested["entity_id"]) == (
        "employee", "E102"
    )
    assert requested["details"] == {
        "expense_amount": 750.0,
        "required_approval": "Manager approval required."
    }

    body = client.post(
        f"/workflows/{thread_id}/decision",
        json={
            "decision": "approve",
            "approver": "Priya",
            "comment": "Receipt verified."
        }
    ).json()

    assert body["status"] == "success"

    assert timeline(thread_id) == [
        "workflow_started",
        "approval_requested",
        "approval_approved",
        "workflow_resumed",
        "business_action_executed",
        "workflow_completed",
    ]

    approved = event(thread_id, "approval_approved")

    with session_scope() as session:
        decision_id = session.scalar(
            select(ApprovalDecision.id).where(
                ApprovalDecision.thread_id == thread_id
            )
        )

    assert approved["actor_type"] == "human"
    assert approved["actor_id"] == "Priya"
    assert (approved["entity_type"], approved["entity_id"]) == (
        "approval_decision", str(decision_id)
    )
    assert approved["details"] == {
        "decision": "approve",
        "comment_provided": True,
        "approver_authenticated": False
    }

    # The comment text stays on approval_decisions only.
    assert "Receipt verified." not in str(list_events(thread_id=thread_id))

    resumed = event(thread_id, "workflow_resumed")

    assert (resumed["actor_type"], resumed["actor_id"]) == ("human", "Priya")

    assert event(thread_id, "workflow_completed")["details"] == {
        "approval_status": "approved"
    }


def test_rejected_path(client):

    thread_id = start(client, QUESTION_750)["thread_id"]

    client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "reject"}
    )

    assert timeline(thread_id) == [
        "workflow_started",
        "approval_requested",
        "approval_rejected",
        "workflow_resumed",
        "workflow_rejected",
    ]

    rejected = event(thread_id, "approval_rejected")

    assert rejected["actor_type"] == "human"
    assert rejected["actor_id"] is None
    assert rejected["details"]["comment_provided"] is False


def test_recovery_path(client):

    thread_id = start(
        client, "For employee E999, process a $750 expense."
    )["thread_id"]

    assert timeline(thread_id) == [
        "workflow_started",
        "workflow_recovery",
        "workflow_failed",
    ]

    recovery = event(thread_id, "workflow_recovery")

    assert recovery["status"] == "failure"
    assert recovery["node"] == "recovery"
    assert recovery["details"] == {"failed_node": "employee"}
    assert recovery["error_type"] == "tool_execution_error"
    assert "E999" in recovery["error_message"]

    failed = event(thread_id, "workflow_failed")

    assert failed["node"] == "employee"
    assert "E999" in failed["error_message"]


def test_business_action_failure_path(client, monkeypatch):

    thread_id = start(client, QUESTION_750)["thread_id"]

    def broken_session():
        raise OperationalError("INSERT", {}, Exception("db down"))

    # Break only the business action's database access.
    monkeypatch.setattr(
        business_action_service, "session_scope", broken_session
    )

    body = client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve"}
    ).json()

    assert body["status"] == "failed"

    assert timeline(thread_id) == [
        "workflow_started",
        "approval_requested",
        "approval_approved",
        "workflow_resumed",
        "business_action_failed",
        "workflow_failed",
    ]

    failed_action = event(thread_id, "business_action_failed")

    assert failed_action["status"] == "failure"
    assert failed_action["error_type"] == "OperationalError"
    assert failed_action["details"] == {"employee_id": "E102", "amount": 750.0}

    failed = event(thread_id, "workflow_failed")

    assert failed["details"] == {"failed_node": "action"}
    assert failed["error_message"] == (
        "Could not record the expense reimbursement."
    )


def test_unexpected_graph_error_is_audited(monkeypatch):

    # Unique message: the PostgreSQL test database keeps rows
    # between runs.
    message = f"LLM unavailable {uuid4()}"

    def exploding_llm(question: str, context: str) -> str:
        raise RuntimeError(message)

    monkeypatch.setattr(agent_graph_module, "generate_answer", exploding_llm)

    with TestClient(app, raise_server_exceptions=False) as client:

        response = client.post(
            "/workflows", json={"question": QUESTION_400}
        )

    assert response.status_code == 500

    with session_scope() as session:

        errors = [
            audit_service.to_dict(e)
            for e in session.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "workflow_error",
                    AuditEvent.error_message == message
                )
            )
        ]

    assert len(errors) == 1

    thread_id = errors[0]["thread_id"]

    # The action ran before the final LLM step failed.
    assert timeline(thread_id) == [
        "workflow_started",
        "business_action_executed",
        "workflow_error",
    ]
    assert errors[0]["error_type"] == "RuntimeError"


# ============================================================
# NO DUPLICATES
# ============================================================

def test_repeated_business_action_is_audited_once():

    thread_id = str(uuid4())

    for _ in range(2):
        record_expense_reimbursement(
            employee_id="E102",
            employee_name="Rahul",
            amount=750.0,
            department="Finance",
            manager="Priya",
            thread_id=thread_id,
            question=QUESTION_750
        )

    assert timeline(thread_id) == ["business_action_executed"]
    assert event(thread_id, "business_action_executed")["entity_id"] == (
        find_reimbursement(thread_id).transaction_id
    )


def test_second_decision_adds_no_approval_event(client):

    thread_id = start(client, QUESTION_750)["thread_id"]

    assert record_approval_decision(thread_id, "reject") is True
    assert record_approval_decision(thread_id, "approve") is False

    response = client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve"}
    )

    assert response.status_code == 409

    assert timeline(thread_id) == [
        "workflow_started",
        "approval_requested",
        "approval_rejected",
    ]


# ============================================================
# AUDIT NEVER BREAKS THE WORKFLOW
# ============================================================

def test_best_effort_audit_failure_does_not_break_workflow(client, monkeypatch):

    def broken_session():
        raise OperationalError("INSERT", {}, Exception("audit db down"))

    # Best-effort events (API outcome events) fail; transactional
    # events use the business transaction and are unaffected.
    monkeypatch.setattr(audit_service, "session_scope", broken_session)

    thread_id = start(client, QUESTION_750)["thread_id"]

    body = client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve"}
    ).json()

    assert body["status"] == "success"

    monkeypatch.undo()

    assert timeline(thread_id) == [
        "workflow_started",
        "approval_approved",
        "business_action_executed",
    ]
