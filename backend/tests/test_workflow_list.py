"""
GET /workflows: the list of workflow runs the dashboard is built on.

Test runs are inserted with timestamps in the far future so they are
always the newest rows, whatever else the shared test database holds,
and are deleted afterwards.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.main import app
from app.services.database_service import (
    MAX_WORKFLOW_PAGE_SIZE,
    WORKFLOW_STATUSES,
    list_workflow_runs,
)
from app.services.db import session_scope
from app.services.db_models import AuditEvent, WorkflowRun


UTC = timezone.utc
FUTURE = datetime(2060, 1, 1, tzinfo=UTC)


@pytest.fixture
def runs():
    """Five test runs, oldest first: statuses in STATUSES order."""

    namespace = uuid4().hex[:10]
    created = []

    with session_scope() as session:

        for n, status in enumerate(WORKFLOW_STATUSES):

            thread_id = f"list-{namespace}-{n}"

            session.add(WorkflowRun(
                thread_id=thread_id,
                question=f"Question {n}",
                employee_id="E101" if n % 2 == 0 else None,
                expense_amount=100 + n * 100 if n != 2 else None,
                status=status,
                approval_status="approved" if status == "success" else None,
                failed_node="rag" if status == "failed" else None,
                error_message="boom" if status == "failed" else None,
                created_at=FUTURE + timedelta(hours=n),
                updated_at=FUTURE + timedelta(hours=n, minutes=5),
            ))

            created.append(thread_id)

    yield created

    with session_scope() as session:

        session.execute(delete(WorkflowRun).where(WorkflowRun.thread_id.in_(created)))


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


# ============================================================
# LIST
# ============================================================

def test_newest_runs_come_first(client, runs):

    body = client.get("/workflows", params={"limit": 5}).json()

    assert [w["thread_id"] for w in body["workflows"]] == list(reversed(runs))


def test_summary_fields(client, runs):

    body = client.get("/workflows", params={"limit": 5}).json()

    newest = body["workflows"][0]                      # the "failed" run

    timestamps = {k: newest.pop(k) for k in ("created_at", "updated_at")}

    # Always UTC with an explicit offset, whatever the database returns.

    assert newest == {
        "thread_id": runs[4],
        "question": "Question 4",
        "employee_id": "E101",
        "expense_amount": 500.0,
        "status": "failed",
        "approval_status": None,
        "failed_node": "rag",
        "error_message": "boom",
    }

    assert timestamps["created_at"] == "2060-01-01T04:00:00+00:00"
    assert timestamps["updated_at"] == "2060-01-01T04:05:00+00:00"

    # runs[3] has status "rejected"; runs[2] ("success") has no amount.
    by_id = {w["thread_id"]: w for w in body["workflows"]}

    assert by_id[runs[3]]["status"] == "rejected"
    assert by_id[runs[3]]["expense_amount"] == 400.0


def test_run_without_an_amount_has_null(client, runs):

    body = client.get("/workflows", params={"limit": 5}).json()

    by_id = {w["thread_id"]: w for w in body["workflows"]}

    assert by_id[runs[2]]["expense_amount"] is None
    assert by_id[runs[1]]["employee_id"] is None
    assert by_id[runs[0]]["employee_id"] == "E101"


def test_status_filter(client, runs):

    body = client.get(
        "/workflows", params={"status": "awaiting_approval", "limit": 200}
    ).json()

    assert runs[1] in [w["thread_id"] for w in body["workflows"]]
    assert {w["status"] for w in body["workflows"]} == {"awaiting_approval"}
    assert body["total"] >= 1
    assert body["total"] >= len(body["workflows"]) or len(body["workflows"]) == 200


@pytest.mark.parametrize("status", WORKFLOW_STATUSES)
def test_every_documented_status_can_be_filtered(client, runs, status):

    body = client.get("/workflows", params={"status": status, "limit": 1}).json()

    # The newest run with each status is one of the test runs.
    assert body["workflows"][0]["thread_id"] in runs
    assert body["workflows"][0]["status"] == status


def test_pagination(client, runs):

    first = client.get("/workflows", params={"limit": 2, "offset": 0}).json()
    second = client.get("/workflows", params={"limit": 2, "offset": 2}).json()

    newest = list(reversed(runs))

    assert [w["thread_id"] for w in first["workflows"]] == newest[0:2]
    assert [w["thread_id"] for w in second["workflows"]] == newest[2:4]

    assert (first["limit"], first["offset"]) == (2, 0)
    assert (second["limit"], second["offset"]) == (2, 2)
    assert first["total"] == second["total"] >= 5


def test_offset_past_the_end_is_an_empty_page(client, runs):

    total = client.get("/workflows", params={"limit": 1}).json()["total"]

    body = client.get("/workflows", params={"offset": total + 10}).json()

    assert body["workflows"] == []
    assert body["total"] == total


def test_total_counts_all_matching_runs(client, runs):

    with session_scope() as session:
        expected = session.scalar(select(func.count()).select_from(WorkflowRun))

    assert client.get("/workflows", params={"limit": 1}).json()["total"] == expected


@pytest.mark.parametrize(
    "params",
    [
        {"status": "nonsense"},
        {"limit": 0},
        {"limit": MAX_WORKFLOW_PAGE_SIZE + 1},
        {"offset": -1},
        {"limit": "many"},
    ],
)
def test_invalid_parameters_are_rejected(client, params):

    assert client.get("/workflows", params=params).status_code == 422


def test_listing_is_read_only(client, runs):

    def counts():
        with session_scope() as session:
            return (
                session.scalar(select(func.count()).select_from(WorkflowRun)),
                session.scalar(select(func.count()).select_from(AuditEvent)),
            )

    before = counts()

    client.get("/workflows")
    client.get("/workflows", params={"status": "failed", "limit": 5})

    assert counts() == before


def test_endpoint_is_documented(client):

    paths = client.get("/openapi.json").json()["paths"]

    schema = paths["/workflows"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]

    assert schema["$ref"].endswith("WorkflowListResponse")

    # Starting a workflow is still a POST on the same path.
    assert "post" in paths["/workflows"]


# ============================================================
# SERVICE
# ============================================================

def test_service_clamps_paging_and_rejects_unknown_status(runs):

    assert len(list_workflow_runs(limit=10_000)["workflows"]) <= MAX_WORKFLOW_PAGE_SIZE
    assert len(list_workflow_runs(limit=0)["workflows"]) == 1
    assert list_workflow_runs(offset=-5)["workflows"][0]["thread_id"] == runs[-1]

    with pytest.raises(ValueError, match="Invalid workflow status"):
        list_workflow_runs(status="nonsense")


# ============================================================
# END TO END
# ============================================================

def test_real_workflow_appears_in_the_list_and_in_the_approval_queue(client):

    started = client.post(
        "/workflows",
        json={"question": "For employee E102, process a $750 expense."},
    ).json()

    thread_id = started["thread_id"]

    assert started["status"] == "awaiting_approval"

    pending = client.get(
        "/workflows", params={"status": "awaiting_approval", "limit": 200}
    ).json()

    entry = next(w for w in pending["workflows"] if w["thread_id"] == thread_id)

    assert entry["employee_id"] == "E102"
    assert entry["expense_amount"] == 750.0
    assert entry["question"] == "For employee E102, process a $750 expense."

    client.post(
        f"/workflows/{thread_id}/decision", json={"decision": "approve", "approver": "Priya"}
    )

    still_pending = client.get(
        "/workflows", params={"status": "awaiting_approval", "limit": 200}
    ).json()

    assert thread_id not in [w["thread_id"] for w in still_pending["workflows"]]

    done = client.get("/workflows", params={"status": "success", "limit": 200}).json()

    assert thread_id in [w["thread_id"] for w in done["workflows"]]


# ============================================================
# TIMESTAMPS
# ============================================================

def test_to_utc_iso_handles_naive_and_offset_timestamps():
    """PostgreSQL gives aware local times, SQLite gives naive UTC."""

    from app.services.database_service import to_utc_iso

    ist = timezone(timedelta(hours=5, minutes=30))

    assert to_utc_iso(datetime(2060, 1, 1, 4, 0)) == "2060-01-01T04:00:00+00:00"
    assert to_utc_iso(datetime(2060, 1, 1, 4, 0, tzinfo=UTC)) == "2060-01-01T04:00:00+00:00"
    assert to_utc_iso(datetime(2060, 1, 1, 9, 30, tzinfo=ist)) == "2060-01-01T04:00:00+00:00"
