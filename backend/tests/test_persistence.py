from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.main import app
from app.services import business_action_service
from app.services.agent_graph import agent_graph
from app.services.business_action_service import (
    find_reimbursement,
    record_expense_reimbursement,
)
from app.services.database_service import (
    create_workflow_run,
    get_approval_decision,
    get_workflow_run,
    record_approval_decision,
    to_money,
)
from app.services.db import session_scope
from app.services.db_models import Reimbursement


QUESTION_750 = "For employee E102, process a $750 expense."


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


def new_thread_id() -> str:

    return str(uuid4())


def run_graph(question: str, thread_id: str) -> dict:

    return agent_graph.invoke(
        {"question": question},
        config={"configurable": {"thread_id": thread_id}}
    )


def record_for(thread_id: str) -> dict:

    return record_expense_reimbursement(
        employee_id="E102",
        employee_name="Rahul",
        amount=750.0,
        department="Finance",
        manager="Priya",
        thread_id=thread_id,
        question=QUESTION_750
    )


# ============================================================
# BUSINESS ACTION
# ============================================================

def test_workflow_action_saves_reimbursement():

    thread_id = new_thread_id()

    result = run_graph(
        "For employee E101, process a $400 expense.",
        thread_id
    )

    saved = find_reimbursement(thread_id)

    assert saved is not None
    assert saved.transaction_id == (
        result["action_result"]["reimbursement"]["transaction_id"]
    )
    assert saved.employee_id == "E101"
    assert saved.amount == to_money(400)

    # Graph runs outside the API still get a workflow run row.
    assert get_workflow_run(thread_id) is not None


def test_reimbursement_is_idempotent_per_workflow():

    thread_id = new_thread_id()

    first = record_for(thread_id)
    second = record_for(thread_id)

    assert first["success"] is True
    assert second["success"] is True
    assert (
        second["reimbursement"]["transaction_id"]
        == first["reimbursement"]["transaction_id"]
    )
    assert second["message"] == (
        "Expense reimbursement was already recorded."
    )

    with session_scope() as session:

        count = session.scalar(
            select(func.count()).select_from(Reimbursement).where(
                Reimbursement.thread_id == thread_id
            )
        )

    assert count == 1


def test_database_error_fails_the_action(monkeypatch):

    def broken_session():
        raise OperationalError("SELECT 1", {}, Exception("db down"))

    monkeypatch.setattr(
        business_action_service, "session_scope", broken_session
    )

    thread_id = new_thread_id()

    run_graph(QUESTION_750, thread_id)

    result = agent_graph.invoke(
        Command(resume="approve"),
        config={"configurable": {"thread_id": thread_id}}
    )

    assert result["action_result"]["success"] is False
    assert result["status"] == "failed"


def test_constraints_reject_duplicate_and_negative_reimbursements():

    thread_id = new_thread_id()

    create_workflow_run(thread_id, QUESTION_750)

    def reimbursement(amount, transaction_id, thread):
        return Reimbursement(
            transaction_id=transaction_id,
            thread_id=thread,
            employee_id="E102",
            amount=to_money(amount),
            department="Finance",
            manager="Priya",
            status="approved"
        )

    with session_scope() as session:
        session.add(reimbursement(10, f"T-{uuid4().hex}", thread_id))

    # Second reimbursement for the same workflow
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(reimbursement(10, f"T-{uuid4().hex}", thread_id))

    # Negative amount
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(reimbursement(-1, f"T-{uuid4().hex}", None))


# ============================================================
# API: WORKFLOW RUNS + APPROVAL DECISIONS
# ============================================================

def test_approved_workflow_is_persisted(client):

    thread_id = client.post(
        "/workflows",
        json={"question": QUESTION_750}
    ).json()["thread_id"]

    run = get_workflow_run(thread_id)

    assert run["status"] == "awaiting_approval"
    assert run["employee_id"] == "E102"
    assert run["expense_amount"] == 750.0

    body = client.post(
        f"/workflows/{thread_id}/decision",
        json={
            "decision": "approve",
            "approver": "Priya",
            "comment": "Receipt verified."
        }
    ).json()

    run = get_workflow_run(thread_id)

    assert run["status"] == "success"
    assert run["approval_status"] == "approved"

    decision = get_approval_decision(thread_id)

    assert decision["decision"] == "approve"
    assert decision["approver"] == "Priya"
    assert decision["comment"] == "Receipt verified."

    saved = find_reimbursement(thread_id)

    assert saved.transaction_id == (
        body["action_result"]["reimbursement"]["transaction_id"]
    )


def test_rejected_workflow_is_persisted_without_reimbursement(client):

    thread_id = client.post(
        "/workflows",
        json={"question": QUESTION_750}
    ).json()["thread_id"]

    # approver and comment are optional
    client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "reject"}
    )

    assert get_workflow_run(thread_id)["status"] == "rejected"

    decision = get_approval_decision(thread_id)

    assert decision["decision"] == "reject"
    assert decision["approver"] is None

    assert find_reimbursement(thread_id) is None


def test_recorded_decision_blocks_a_second_decision(client):
    """Simulates a concurrent request that decided first."""

    thread_id = client.post(
        "/workflows",
        json={"question": QUESTION_750}
    ).json()["thread_id"]

    assert record_approval_decision(thread_id, "reject") is True

    response = client.post(
        f"/workflows/{thread_id}/decision",
        json={"decision": "approve"}
    )

    assert response.status_code == 409
    assert find_reimbursement(thread_id) is None


def test_failed_workflow_is_persisted(client):

    thread_id = client.post(
        "/workflows",
        json={"question": "For employee E999, process a $750 expense."}
    ).json()["thread_id"]

    run = get_workflow_run(thread_id)

    assert run["status"] == "failed"
    assert run["failed_node"] == "employee"
    assert run["employee_id"] is None
    assert "E999" in run["error_message"]
