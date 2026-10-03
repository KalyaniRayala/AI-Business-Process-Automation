"""
Node-level audit events (node_started / node_completed / node_failed)
observed through the API's task stream.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app
from app.services import agent_graph as agent_graph_module
from app.services import business_action_service
from sqlalchemy import select

from app.services.audit_service import list_events, to_dict
from app.services.db import session_scope
from app.services.db_models import AuditEvent
from app.services.workflow_audit import NodeAuditor


QUESTION_400 = "For employee E101, process a $400 expense."
QUESTION_750 = "For employee E102, process a $750 expense."


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


def start(client, question: str) -> str:

    return client.post("/workflows", json={"question": question}).json()["thread_id"]


def full_timeline(thread_id: str) -> list[tuple]:
    """
    (event_type, node) for every event, with the parallel lookup
    nodes sorted inside each group because they run concurrently.
    """

    events = [
        (e["event_type"], e["node"])
        for e in list_events(thread_id=thread_id)
    ]

    parallel = {"employee", "policy", "rag"}
    ordered, group = [], []

    for item in events:

        if item[0].startswith("node_") and item[1] in parallel:
            group.append(item)
            continue

        ordered.extend(sorted(group))
        group = []
        ordered.append(item)

    return ordered + sorted(group)


PARALLEL_STARTED = [
    ("node_started", "employee"),
    ("node_started", "policy"),
    ("node_started", "rag"),
]

PARALLEL_COMPLETED = [
    ("node_completed", "employee"),
    ("node_completed", "policy"),
    ("node_completed", "rag"),
]

LOOKUPS = sorted(PARALLEL_STARTED + PARALLEL_COMPLETED)


def events_with_error(event_type: str, message: str) -> list[dict]:
    """
    Look up by a unique error message across the whole table
    (the PostgreSQL test database keeps rows between runs).
    """

    with session_scope() as session:

        return [
            to_dict(event)
            for event in session.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.error_message == message
                )
            )
        ]


def node_event(thread_id: str, event_type: str, node: str) -> list[dict]:

    return [
        e for e in list_events(thread_id=thread_id, event_type=event_type)
        if e["node"] == node
    ]


# ============================================================
# FULL TIMELINES
# ============================================================

def test_no_approval_timeline(client):

    thread_id = start(client, QUESTION_400)

    assert full_timeline(thread_id) == [
        ("workflow_started", None),
        *LOOKUPS,
        ("node_started", "approval"),
        ("node_completed", "approval"),
        ("node_started", "action"),
        ("business_action_executed", "action"),
        ("node_completed", "action"),
        ("node_started", "final"),
        ("node_completed", "final"),
        ("workflow_completed", "final"),
    ]

    [approval] = node_event(thread_id, "node_completed", "approval")

    # The skipped approval is now recorded before the action runs.
    assert approval["details"] == {
        "approval_status": "not_required",
        "approval_required": False
    }


def test_approval_timeline(client):

    thread_id = start(client, QUESTION_750)

    client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve", "approver": "Priya"}
    )

    assert full_timeline(thread_id) == [
        ("workflow_started", None),
        *LOOKUPS,
        ("node_started", "approval"),
        ("approval_requested", "approval"),
        ("approval_approved", "approval"),
        ("workflow_resumed", "approval"),
        # LangGraph re-runs the paused node when it resumes.
        ("node_started", "approval"),
        ("node_completed", "approval"),
        ("node_started", "action"),
        ("business_action_executed", "action"),
        ("node_completed", "action"),
        ("node_started", "final"),
        ("node_completed", "final"),
        ("workflow_completed", "final"),
    ]

    first_start, resumed_start = node_event(thread_id, "node_started", "approval")

    assert first_start["details"] is None
    assert resumed_start["details"] == {"resumed": True}

    [approval] = node_event(thread_id, "node_completed", "approval")

    assert approval["details"] == {
        "approval_status": "approved",
        "approval_required": True,
        "resumed": True
    }


def test_rejection_timeline(client):

    thread_id = start(client, QUESTION_750)

    client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "reject"}
    )

    assert full_timeline(thread_id)[-7:] == [
        ("node_started", "approval"),
        ("node_completed", "approval"),
        ("node_started", "rejection"),
        ("node_completed", "rejection"),
        ("node_started", "final"),
        ("node_completed", "final"),
        ("workflow_rejected", "final"),
    ]


# ============================================================
# NODE DETAILS
# ============================================================

def test_completed_nodes_reference_entities_without_copying_data(client):

    thread_id = start(client, QUESTION_400)

    [employee] = node_event(thread_id, "node_completed", "employee")

    assert (employee["entity_type"], employee["entity_id"]) == ("employee", "E101")
    assert employee["details"] is None

    [policy] = node_event(thread_id, "node_completed", "policy")

    assert policy["details"] == {
        "expense_amount": 400.0,
        "approval": "No approval required."
    }

    [rag] = node_event(thread_id, "node_completed", "rag")

    assert rag["details"]["context_characters"] > 0

    [action] = node_event(thread_id, "node_completed", "action")

    assert action["entity_type"] == "reimbursement"
    assert action["entity_id"].startswith("EXP-E101-")

    [final] = node_event(thread_id, "node_completed", "final")

    assert final["details"] == {"workflow_status": "success"}

    # No document text, answers or names in the audit trail.
    everything = str(list_events(thread_id=thread_id))

    assert "Stubbed answer." not in everything
    assert "Expenses above $500" not in everything
    assert "Anita" not in everything


# ============================================================
# NODE FAILURES
# ============================================================

def test_lookup_failure_is_node_failed_then_recovery(client):

    thread_id = start(client, "For employee E999, process a $750 expense.")

    [failed] = node_event(thread_id, "node_failed", "employee")

    assert failed["status"] == "failure"
    assert failed["details"] == {"raised": False}
    assert "E999" in failed["error_message"]

    timeline = full_timeline(thread_id)

    assert timeline[1:7] == sorted([
        ("node_started", "employee"),
        ("node_started", "policy"),
        ("node_started", "rag"),
        ("node_failed", "employee"),
        ("node_completed", "policy"),
        ("node_completed", "rag"),
    ])

    assert timeline[7:] == [
        ("node_started", "recovery"),
        ("node_completed", "recovery"),
        ("workflow_recovery", "recovery"),
        ("workflow_failed", "employee"),
    ]

    [recovery] = node_event(thread_id, "node_completed", "recovery")

    assert recovery["details"] == {"failed_node": "employee"}


@pytest.mark.parametrize("document_results", [[]])
def test_empty_retrieval_is_node_failed(client, document_results):

    thread_id = start(client, QUESTION_750)

    [failed] = node_event(thread_id, "node_failed", "rag")

    assert failed["error_message"] == "Document search returned no context."


def test_business_action_failure_is_node_failed(client, monkeypatch):

    thread_id = start(client, QUESTION_750)

    def broken_session():
        raise OperationalError("INSERT", {}, Exception("db down"))

    monkeypatch.setattr(business_action_service, "session_scope", broken_session)

    client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve"}
    )

    [failed] = node_event(thread_id, "node_failed", "action")

    assert failed["error_message"] == "Could not record the expense reimbursement."
    assert failed["details"] == {"raised": False, "resumed": True}


def test_raised_exception_is_node_failed(monkeypatch):

    message = f"LLM unavailable {uuid4()}"

    def exploding_llm(question: str, context: str) -> str:
        raise RuntimeError(message)

    monkeypatch.setattr(agent_graph_module, "generate_answer", exploding_llm)

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post(
            "/workflows", json={"question": QUESTION_400}
        ).status_code == 500

    [failed] = events_with_error("node_failed", message)

    assert failed["node"] == "final"
    assert failed["error_type"] == "RuntimeError"
    assert failed["details"] == {"raised": True}

    assert full_timeline(failed["thread_id"])[-3:] == [
        ("node_started", "final"),
        ("node_failed", "final"),
        ("workflow_error", None),
    ]


# ============================================================
# ROBUSTNESS
# ============================================================

def test_observer_never_raises():

    auditor = NodeAuditor("observer-test")

    # Malformed task reports are logged, not raised.
    auditor.observe({"name": "employee"})
    auditor.observe({"name": "policy", "id": "x", "result": "not-a-dict"})
    auditor.observe(None)
    auditor.fail_pending(RuntimeError("boom"))


def test_graph_behaviour_is_unchanged_by_streaming(client):
    """Same responses as before the observer was added."""

    body = client.post("/workflows", json={"question": QUESTION_400}).json()

    assert body["status"] == "success"
    assert body["approval_status"] == "not_required"
    assert body["action_result"]["success"] is True
    assert "Transaction ID: EXP-E101-" in body["answer"]

    body = client.post("/workflows", json={"question": QUESTION_750}).json()

    assert body["status"] == "awaiting_approval"
    assert body["approval_request"]["type"] == "approval_required"
