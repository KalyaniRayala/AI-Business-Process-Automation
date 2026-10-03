"""
GET /audit-events
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.audit_service import list_events


QUESTION_750 = "For employee E102, process a $750 expense."


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
        json={"decision": "approve", "approver": "Priya", "comment": "Receipt verified."}
    )

    return thread_id


def search(client, **params):

    return client.get("/audit-events", params=params)


def test_filter_by_thread_matches_workflow_history(client, approved_workflow):

    body = search(client, thread_id=approved_workflow).json()

    assert set(body) == {"events", "count", "limit", "next_after_id"}

    history = client.get(f"/workflows/{approved_workflow}/audit").json()

    assert body["events"] == history["events"]


def test_filter_by_event_type(client, approved_workflow):

    body = search(
        client, thread_id=approved_workflow, event_type="business_action_executed"
    ).json()

    [event] = body["events"]

    assert event["event_type"] == "business_action_executed"
    assert event["entity_type"] == "reimbursement"


def test_filter_by_entity_finds_events_across_workflows(client, approved_workflow):

    transaction_id = search(
        client, thread_id=approved_workflow, event_type="business_action_executed"
    ).json()["events"][0]["entity_id"]

    events = search(
        client, entity_type="reimbursement", entity_id=transaction_id
    ).json()["events"]

    # The executed event and the action node's completion.
    assert {e["event_type"] for e in events} == {
        "business_action_executed",
        "node_completed",
    }
    assert {e["thread_id"] for e in events} == {approved_workflow}


def test_results_are_chronological_and_paginate(client, approved_workflow):

    full = search(client, thread_id=approved_workflow).json()["events"]

    ids = [e["id"] for e in full]

    assert ids == sorted(ids)

    first = search(client, thread_id=approved_workflow, limit=10).json()
    second = search(
        client, thread_id=approved_workflow, limit=10,
        after_id=first["next_after_id"]
    ).json()

    assert first["next_after_id"] == first["events"][-1]["id"]
    assert [e["id"] for e in first["events"] + second["events"]] == ids
    assert second["next_after_id"] is None


def test_no_match_returns_empty_page(client):

    body = search(client, thread_id=f"nothing-{uuid.uuid4()}")

    assert body.status_code == 200
    assert body.json() == {
        "events": [], "count": 0, "limit": 100, "next_after_id": None
    }


def test_unfiltered_search_is_bounded(client, approved_workflow):

    body = search(client, limit=3).json()

    assert body["count"] == 3
    assert body["next_after_id"] == body["events"][-1]["id"]


@pytest.mark.parametrize(
    "params",
    [
        {"event_type": "not_a_real_event"},
        {"limit": 0},
        {"limit": 501},
        {"after_id": -1},
        {"thread_id": "x" * 65},
        {"entity_type": "x" * 51},
        {"entity_id": "x" * 101},
    ],
)
def test_invalid_filters_are_rejected(client, params):

    assert search(client, **params).status_code == 422


def test_search_exposes_no_question_comment_or_answer(client, approved_workflow):

    text = search(client, thread_id=approved_workflow).text

    assert "process a $750 expense" not in text
    assert "Receipt verified." not in text
    assert "Stubbed answer." not in text


def test_search_matches_audit_service(client, approved_workflow):

    body = search(client, thread_id=approved_workflow, event_type="node_completed").json()

    assert [e["id"] for e in body["events"]] == [
        e["id"] for e in list_events(
            thread_id=approved_workflow, event_type="node_completed"
        )
    ]
