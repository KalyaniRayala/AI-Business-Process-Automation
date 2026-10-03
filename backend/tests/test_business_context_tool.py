"""
CG4: the read-only get_business_context tool and the context graph
read helpers it uses.
"""

import inspect

import pytest
from sqlalchemy import func, select

from app.services import context_graph_service, tool_calling_service, tool_service
from app.services.context_graph_seed import sync_context_graph
from app.services.context_graph_service import (
    get_applicable_rules,
    get_employee_context,
    get_node,
    upsert_node,
)
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode
from app.services.tool_executor import execute_with_retry
from app.services.tool_service import (
    business_context_tool,
    get_business_context,
    lookup_expense_policy,
)


RULE_500 = "policy_rule:expense_approval_over_500"
RULE_5000 = "policy_rule:expense_approval_over_5000"


def graph_size() -> tuple[int, int]:

    with session_scope() as session:
        return (
            session.scalar(select(func.count()).select_from(ContextNode)),
            session.scalar(select(func.count()).select_from(ContextEdge)),
        )


# ============================================================
# TOOL RESULTS
# ============================================================

def test_manager_approval_context():

    result = get_business_context("E102", 750)

    assert result == {
        "success": True,
        "source": "context_graph",
        "employee": {
            "employee_id": "E102",
            "departments": ["Finance"],
            "managers": [{"name": "Priya", "roles": ["Manager"]}],
        },
        "process": "process:expense_reimbursement",
        "expense_amount": 750,
        "approval_required": True,
        "required_approvals": ["Manager"],
        "applicable_rules": [{
            "rule": RULE_500,
            "description": "Expenses above $500 require manager approval",
            "threshold": 500,
            "approver_roles": ["Manager"],
            "approval_steps": [
                "Check expense reimbursement policy",
                "Manager approval for expenses above $500",
            ],
            "evidence": ["document:data/expense_process.txt#L15"],
        }],
    }


def test_manager_and_finance_approval_context():

    result = get_business_context("E101", 6000)

    assert result["employee"]["departments"] == ["Engineering"]
    assert result["employee"]["managers"] == [{"name": "Ravi", "roles": ["Manager"]}]
    assert result["required_approvals"] == ["Manager", "Finance"]
    assert [r["rule"] for r in result["applicable_rules"]] == [RULE_500, RULE_5000]
    assert result["applicable_rules"][1]["evidence"] == [
        "document:data/expense_process.txt#L17"
    ]


def test_no_approval_needed():

    result = get_business_context("E103", 400)

    assert result["success"] is True
    assert result["approval_required"] is False
    assert result["required_approvals"] == []
    assert result["applicable_rules"] == []


def test_employee_context_without_amount():

    result = get_business_context("E104")

    assert result["success"] is True
    assert result["employee"]["managers"] == [{"name": "Ravi", "roles": ["Manager"]}]
    assert result["expense_amount"] is None
    assert result["approval_required"] is None
    assert result["required_approvals"] is None
    assert result["applicable_rules"] is None


# ============================================================
# PARITY WITH THE AUTHORITATIVE POLICY TOOL
# ============================================================

APPROVAL_TEXT = {
    (): "No approval required.",
    ("Manager",): "Manager approval required.",
    ("Manager", "Finance"): "Manager approval and finance approval required.",
}


@pytest.mark.parametrize(
    "amount",
    [0, 0.01, 499.99, 500, 500.01, 750, 4999.99, 5000, 5000.01, 6000, 1_000_000],
)
def test_required_approvals_match_lookup_expense_policy(amount):

    required = tuple(get_business_context("E102", amount)["required_approvals"])

    assert APPROVAL_TEXT[required] == lookup_expense_policy(amount)["approval"]


# ============================================================
# ERRORS
# ============================================================

@pytest.mark.parametrize(
    "employee_id, amount, error",
    [
        ("E999", 750, "Employee E999 was not found in the context graph."),
        ("", 750, "Invalid employee id: ''."),
        ("E102;drop", 750, "Invalid employee id: 'E102;drop'."),
        ("E" * 21, None, "Invalid employee id"),
        (None, None, "Invalid employee id: None."),
        ("E102", -1, "Expense amount cannot be negative."),
    ],
)
def test_invalid_requests_return_errors(employee_id, amount, error):

    result = get_business_context(employee_id, amount)

    assert result["success"] is False
    assert result["error"].startswith(error)


def test_inactive_employee_is_not_found():

    node = get_node("employee:E103")

    try:

        upsert_node(node_key="employee:E103", node_type="employee", label="E103",
                    source_type="system_of_record", source_ref=node["source_ref"],
                    is_active=False)

        assert get_business_context("E103", 100) == {
            "success": False,
            "error": "Employee E103 was not found in the context graph.",
        }

    finally:

        sync_context_graph()

    assert get_node("employee:E103")["is_active"] is True


def test_missing_process_returns_error(monkeypatch):

    monkeypatch.setattr(tool_service, "EXPENSE_PROCESS_KEY", "process:missing")

    result = get_business_context("E102", 750)

    assert result["success"] is False
    assert "process:missing" in result["error"]


def test_database_errors_are_handled_by_the_retry_executor(monkeypatch):

    def broken(employee_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(tool_service, "get_employee_context", broken)

    result = execute_with_retry(
        get_business_context, retries=1, employee_id="E102", expense_amount=750
    )

    assert result["success"] is False
    assert result["error"] == "database unavailable"


# ============================================================
# READ HELPERS
# ============================================================

def test_get_employee_context():

    context = get_employee_context("E102")

    assert context["employee"]["node_key"] == "employee:E102"
    assert [d["node_key"] for d in context["departments"]] == ["department:finance"]
    assert [
        (m["person"]["node_key"], [r["node_key"] for r in m["roles"]])
        for m in context["managers"]
    ] == [("person:priya", ["role:manager"])]

    assert get_employee_context("E999") is None


def test_get_applicable_rules():

    assert get_applicable_rules("process:expense_reimbursement", 500) == []

    [rule] = get_applicable_rules("process:expense_reimbursement", 500.01)

    assert rule["rule"]["node_key"] == RULE_500
    assert [r["node_key"] for r in rule["approver_roles"]] == ["role:manager"]

    assert get_applicable_rules("process:missing", 750) is None


def test_unsupported_rule_comparison_is_rejected(monkeypatch):

    real = context_graph_service.get_process_context

    def with_unknown_comparison(process_key):
        process = real(process_key)
        process["policy_rules"][0]["rule"]["properties"]["comparison"] = "between"
        return process

    monkeypatch.setattr(context_graph_service, "get_process_context", with_unknown_comparison)

    with pytest.raises(ValueError, match="Unsupported rule comparison"):
        get_applicable_rules("process:expense_reimbursement", 750)


# ============================================================
# TOOL DEFINITION AND SCOPE
# ============================================================

def test_tool_schema_matches_function():

    function = business_context_tool["function"]
    parameters = inspect.signature(get_business_context).parameters

    assert business_context_tool["type"] == "function"
    assert function["name"] == get_business_context.__name__
    assert set(function["parameters"]["properties"]) == set(parameters)
    assert function["parameters"]["required"] == [
        name for name, p in parameters.items() if p.default is inspect.Parameter.empty
    ]


def test_tool_is_read_only():

    before = graph_size()

    get_business_context("E102", 6000)
    get_business_context("E999", 10)

    assert graph_size() == before


def test_tool_is_not_yet_registered_with_the_llm_agent():
    """CG4 scope: available in tool_service only."""

    source = inspect.getsource(tool_calling_service)

    assert "business_context_tool" not in source
    assert "get_business_context" not in source
