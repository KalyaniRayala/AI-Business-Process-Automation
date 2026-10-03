import pytest
from fastapi.testclient import TestClient

from app.main import app


QUESTION_750 = "For employee E102, process a $750 expense."


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


def test_health(client):

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_approval_round_trip(client):

    started = client.post(
        "/workflows",
        json={"question": QUESTION_750}
    )

    assert started.status_code == 201

    body = started.json()
    thread_id = body["thread_id"]

    assert body["status"] == "awaiting_approval"
    assert body["approval_request"]["type"] == "approval_required"

    fetched = client.get(f"/workflows/{thread_id}")

    assert fetched.json()["status"] == "awaiting_approval"

    decided = client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve"}
    )

    assert decided.status_code == 200

    body = decided.json()

    assert body["status"] == "success"
    assert body["approval_status"] == "approved"
    assert body["action_result"]["success"] is True

    # A completed workflow cannot be decided again.
    repeat = client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve"}
    )

    assert repeat.status_code == 409


def test_invalid_decision_is_rejected(client):

    thread_id = client.post(
        "/workflows",
        json={"question": QUESTION_750}
    ).json()["thread_id"]

    response = client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "maybe"}
    )

    assert response.status_code == 422


def test_unknown_workflow_returns_404(client):

    response = client.get("/workflows/does-not-exist")

    assert response.status_code == 404
