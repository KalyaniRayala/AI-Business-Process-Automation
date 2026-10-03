from uuid import uuid4

import pytest
from langgraph.types import Command

from app.services.agent_graph import agent_graph


def new_config() -> dict:

    return {
        "configurable": {
            "thread_id": str(uuid4())
        }
    }


def start(question: str, config: dict) -> dict:

    return agent_graph.invoke(
        {"question": question},
        config=config
    )


# ============================================================
# NO APPROVAL REQUIRED
# ============================================================

def test_small_expense_runs_action_without_approval():

    result = start(
        "For employee E101, process a $400 expense.",
        new_config()
    )

    assert "__interrupt__" not in result
    assert result["approval_status"] == "not_required"
    assert result["status"] == "success"
    assert result["action_result"]["success"] is True

    reimbursement = result["action_result"]["reimbursement"]

    assert reimbursement["employee_id"] == "E101"
    assert reimbursement["amount"] == 400.0
    assert reimbursement["transaction_id"].startswith("EXP-E101-")


# ============================================================
# APPROVAL REQUIRED
# ============================================================

QUESTION_750 = "For employee E102, process a $750 expense."


def test_large_expense_pauses_for_approval():

    result = start(QUESTION_750, new_config())

    assert "__interrupt__" in result

    request = result["__interrupt__"][0].value

    assert request["type"] == "approval_required"
    assert request["policy"]["approval"] == "Manager approval required."
    assert request["employee"]["employee"]["employee_id"] == "E102"
    assert "action_result" not in result


def test_approved_expense_runs_action():

    config = new_config()

    start(QUESTION_750, config)

    result = agent_graph.invoke(
        Command(resume="approve"),
        config=config
    )

    assert result["approval_status"] == "approved"
    assert result["status"] == "success"
    assert result["action_result"]["success"] is True
    assert result["action_result"]["reimbursement"]["amount"] == 750.0
    assert "Transaction ID: EXP-E102-" in result["answer"]


def test_rejected_expense_skips_action():

    config = new_config()

    start(QUESTION_750, config)

    result = agent_graph.invoke(
        Command(resume="reject"),
        config=config
    )

    assert result["approval_status"] == "rejected"
    assert result["status"] == "rejected"
    assert "action_result" not in result


# ============================================================
# RECOVERY / ERROR ROUTING
# ============================================================

def test_unknown_employee_routes_to_recovery():

    result = start(
        "For employee E999, process a $750 expense.",
        new_config()
    )

    assert "__interrupt__" not in result
    assert result["status"] == "failed"
    assert result["failed_node"] == "employee"
    assert "action_result" not in result


def test_missing_amount_routes_to_recovery():

    result = start(
        "For employee E102, explain the expense process.",
        new_config()
    )

    assert result["status"] == "failed"
    assert result["failed_node"] == "policy"
    assert "action_result" not in result


@pytest.mark.parametrize("document_results", [[]])
def test_empty_document_search_routes_to_recovery(document_results):

    result = start(QUESTION_750, new_config())

    assert result["status"] == "failed"
    assert result["failed_node"] == "rag"
    assert "action_result" not in result
