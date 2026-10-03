"""
GET /workflows/{thread_id}/audit
"""

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_service import list_events, record_event
from app.services.db import session_scope
from app.services.db_models import WorkflowRun


QUESTION_750 = "For employee E102, process a $750 expense."

EVENT_FIELDS = {
    "id",
    "event_id",
    "occurred_at",
    "thread_id",
    "event_type",
    "actor_type",
    "actor_id",
    "node",
    "entity_type",
    "entity_id",
    "status",
    "message",
    "details",
    "error_type",
    "error_message",
}


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def approved_workflow(client) -> str:

    thread_id = client.post(
        "/workflows", json={"question": QUESTION_750}
    ).json()["thread_id"]

    client.post(
        f"/workflows/{thread_id}/decision",
        json={
            "decision": "approve",
            "approver": "Priya",
            "comment": "Receipt verified."
        }
    )

    return thread_id


def audit(client, thread_id: str, **params):

    return client.get(f"/workflows/{thread_id}/audit", params=params)


# ============================================================
# HISTORY
# ============================================================

def test_returns_full_history_in_chronological_order(client, approved_workflow):

    response = audit(client, approved_workflow)

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {"thread_id", "events", "count", "limit", "next_after_id"}
    assert body["thread_id"] == approved_workflow
    assert body["limit"] == 100
    assert body["count"] == len(body["events"]) == 19
    assert body["next_after_id"] is None

    events = body["events"]

    # Same events, same order as the audit service.
    assert [e["id"] for e in events] == [
        e["id"] for e in list_events(thread_id=approved_workflow)
    ]

    ids = [e["id"] for e in events]
    times = [datetime.fromisoformat(e["occurred_at"]) for e in events]

    assert ids == sorted(ids)
    assert times == sorted(times)

    assert [
        e["event_type"] for e in events
        if not e["event_type"].startswith("node_")
    ] == [
        "workflow_started",
        "approval_requested",
        "approval_approved",
        "workflow_resumed",
        "business_action_executed",
        "workflow_completed",
    ]

    assert all(e["thread_id"] == approved_workflow for e in events)


def test_event_shape(client, approved_workflow):

    events = audit(client, approved_workflow).json()["events"]

    for event in events:

        assert set(event) == EVENT_FIELDS
        assert uuid.UUID(event["event_id"])

    [approved] = [e for e in events if e["event_type"] == "approval_approved"]

    assert approved["actor_type"] == "human"
    assert approved["actor_id"] == "Priya"
    assert approved["status"] == "success"
    assert approved["entity_type"] == "approval_decision"
    assert approved["details"] == {
        "decision": "approve",
        "comment_provided": True,
        "approver_authenticated": False
    }


def test_response_contains_no_question_comment_or_answer(client, approved_workflow):

    text = audit(client, approved_workflow).text

    assert "process a $750 expense" not in text
    assert "Receipt verified." not in text
    assert "Stubbed answer." not in text


def test_other_workflows_are_not_included(client, approved_workflow):

    other = client.post(
        "/workflows", json={"question": QUESTION_750}
    ).json()["thread_id"]

    events = audit(client, other).json()["events"]

    assert {e["thread_id"] for e in events} == {other}
    assert [e["event_type"] for e in events][0] == "workflow_started"


# ============================================================
# PAGINATION
# ============================================================

def test_pages_follow_next_after_id(client, approved_workflow):

    full = audit(client, approved_workflow).json()["events"]

    collected = []
    after_id = None

    while True:

        params = {"limit": 5}

        if after_id is not None:
            params["after_id"] = after_id

        page = audit(client, approved_workflow, **params).json()

        assert page["limit"] == 5
        assert page["count"] <= 5

        collected += page["events"]
        after_id = page["next_after_id"]

        if after_id is None:
            break

        assert after_id == page["events"][-1]["id"]

    assert [e["id"] for e in collected] == [e["id"] for e in full]


def test_page_after_last_event_is_empty(client, approved_workflow):

    last_id = audit(client, approved_workflow).json()["events"][-1]["id"]

    body = audit(client, approved_workflow, after_id=last_id).json()

    assert body["events"] == []
    assert body["count"] == 0
    assert body["next_after_id"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"after_id": -1},
        {"limit": "many"},
    ],
)
def test_invalid_pagination_is_rejected(client, approved_workflow, params):

    assert audit(client, approved_workflow, **params).status_code == 422


# ============================================================
# UNKNOWN / EDGE WORKFLOWS
# ============================================================

def test_unknown_workflow_returns_404(client):

    thread_id = f"does-not-exist-{uuid.uuid4()}"

    response = audit(client, thread_id)

    assert response.status_code == 404
    assert response.json() == {"detail": f"Workflow {thread_id} was not found."}

    # Also with a pagination cursor.
    assert audit(client, thread_id, after_id=10).status_code == 404


def test_known_workflow_without_events_returns_empty_history(client):
    """A run created before auditing existed."""

    thread_id = f"pre-audit-{uuid.uuid4()}"

    with session_scope() as session:
        session.add(WorkflowRun(
            thread_id=thread_id,
            question="legacy",
            status="success"
        ))

    body = audit(client, thread_id).json()

    assert body["events"] == []
    assert body["count"] == 0


def test_thread_with_events_but_no_run_row_is_returned(client):
    """Events recorded for a graph run outside the API."""

    thread_id = f"script-run-{uuid.uuid4()}"

    record_event(
        event_type="business_action_executed",
        message="Recorded.",
        status="success",
        thread_id=thread_id
    )

    body = audit(client, thread_id).json()

    assert [e["event_type"] for e in body["events"]] == [
        "business_action_executed"
    ]


def test_reading_audit_history_does_not_change_it(client, approved_workflow):

    before = audit(client, approved_workflow).json()
    after = audit(client, approved_workflow).json()

    assert before == after
