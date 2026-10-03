"""
CG6: Context Graph relationships inside the LangGraph workflow.

A `context` node runs in parallel with employee / policy / rag and
stores graph facts in state. They are shown to the human approver and
used in the final answer; routing, approval and the business action
still depend only on the existing policy tool.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command
from sqlalchemy import func, select

from app.main import app
from app.services import agent_graph as agent_graph_module
from app.services.agent_graph import agent_graph, context_node
from app.services.context_rag_service import (
    check_policy_consistency,
    policy_approvers,
    summarize_for_approval,
)
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode
from app.services.workflow_audit import AUDITED_NODES


QUESTION_750 = "For employee E102, process a $750 expense."
QUESTION_400 = "For employee E103, process a $400 expense."

POLICY_500 = {"success": True, "expense_amount": 750.0,
              "approval": "Manager approval required."}


def new_config() -> dict:

    return {"configurable": {"thread_id": str(uuid4())}}


def graph_size() -> tuple[int, int]:

    with session_scope() as session:
        return (
            session.scalar(select(func.count()).select_from(ContextNode)),
            session.scalar(select(func.count()).select_from(ContextEdge)),
        )


@pytest.fixture
def broken_graph(monkeypatch):

    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(agent_graph_module, "get_business_context", broken)


# ============================================================
# WIRING
# ============================================================

def test_context_node_runs_in_parallel_with_the_other_lookups():

    edges = {(e.source, e.target) for e in agent_graph.get_graph().edges}

    assert {("__start__", "analyze"), ("analyze", "context"),
            ("context", "synchronize")} <= edges

    # Same fan-out / join as employee, policy and rag.
    for node in ("employee", "policy", "rag"):
        assert ("analyze", node) in edges
        assert (node, "synchronize") in edges

    # No new routing: check_results still only leads to approval / recovery.
    assert {e.target for e in agent_graph.get_graph().edges
            if e.source == "context"} == {"synchronize"}


def test_node_audit_events_are_unchanged():

    assert "context" not in AUDITED_NODES


# ============================================================
# CONTEXT NODE
# ============================================================

def test_context_node_reads_the_graph():

    context = context_node({"question": QUESTION_750})["business_context"]

    assert context["success"] is True
    assert context["employee"]["managers"] == [{"name": "Priya", "roles": ["Manager"]}]
    assert context["required_approvals"] == ["Manager"]
    assert [r["rule"] for r in context["applicable_rules"]] == [
        "policy_rule:expense_approval_over_500"
    ]


@pytest.mark.parametrize(
    "question, error",
    [
        ("What is the reimbursement process?", "Employee ID not found."),
        ("For employee E999, process a $750 expense.",
         "Employee E999 was not found in the context graph."),
    ],
)
def test_context_node_reports_missing_employee(question, error):

    assert context_node({"question": question})["business_context"] == {
        "success": False,
        "error": error,
    }


def test_context_node_survives_graph_errors(broken_graph):

    result = context_node({"question": QUESTION_750})["business_context"]

    assert result["success"] is False
    assert result["error"] == "database unavailable"


# ============================================================
# DECISION SUPPORT HELPERS
# ============================================================

@pytest.mark.parametrize(
    "approval, expected",
    [
        ("No approval required.", []),
        ("Manager approval required.", ["Manager"]),
        ("Manager approval and finance approval required.", ["Manager", "Finance"]),
    ],
)
def test_policy_approvers(approval, expected):

    assert policy_approvers({"success": True, "approval": approval}) == expected


def test_policy_approvers_unavailable():

    assert policy_approvers({"success": False, "error": "x"}) is None
    assert policy_approvers(None) is None


@pytest.mark.parametrize("amount", [0.01, 500, 500.01, 4999.99, 5000, 5000.01, 1_000_000])
def test_graph_and_policy_tool_agree_at_every_threshold(amount):

    from app.services.tool_service import get_business_context, lookup_expense_policy

    check = check_policy_consistency(
        lookup_expense_policy(amount),
        get_business_context("E102", amount),
    )

    assert check["consistent"] is True


def test_mismatch_is_detected():

    context = {"success": True, "required_approvals": ["Finance"]}

    assert check_policy_consistency(POLICY_500, context) == {
        "consistent": False,
        "graph_approvers": ["Finance"],
        "policy_approvers": ["Manager"],
    }


@pytest.mark.parametrize(
    "policy, context",
    [
        ({"success": False}, {"success": True, "required_approvals": []}),
        (POLICY_500, {"success": False}),
        (POLICY_500, {"success": True, "required_approvals": None}),
        (POLICY_500, None),
    ],
)
def test_consistency_is_none_when_either_side_is_unavailable(policy, context):

    assert check_policy_consistency(policy, context) is None


@pytest.mark.parametrize("context", [None, {}, {"success": False, "error": "x"}])
def test_no_approver_summary_without_graph_context(context):

    assert summarize_for_approval(POLICY_500, context) is None


# ============================================================
# WORKFLOW: APPROVER SEES GRAPH CONTEXT
# ============================================================

def test_approval_request_includes_graph_context():

    config = new_config()

    result = agent_graph.invoke({"question": QUESTION_750}, config=config)

    request = result["__interrupt__"][0].value

    assert request["type"] == "approval_required"
    assert request["policy"]["approval"] == "Manager approval required."
    assert request["business_context"] == {
        "departments": ["Finance"],
        "managers": [{"name": "Priya", "roles": ["Manager"]}],
        "required_approvals": ["Manager"],
        "rules": [{
            "rule": "policy_rule:expense_approval_over_500",
            "description": "Expenses above $500 require manager approval",
            "evidence": ["document:data/expense_process.txt#L15"],
        }],
        "policy_check": {
            "consistent": True,
            "graph_approvers": ["Manager"],
            "policy_approvers": ["Manager"],
        },
    }

    # Persisted with the workflow state (checkpoint).
    state = agent_graph.get_state(config).values

    assert state["business_context"]["required_approvals"] == ["Manager"]


def test_manager_and_finance_context_for_large_amounts():

    result = agent_graph.invoke(
        {"question": "For employee E101, process a $6000 expense."},
        config=new_config(),
    )

    summary = result["__interrupt__"][0].value["business_context"]

    assert summary["required_approvals"] == ["Manager", "Finance"]
    assert [r["rule"] for r in summary["rules"]] == [
        "policy_rule:expense_approval_over_500",
        "policy_rule:expense_approval_over_5000",
    ]


def test_api_exposes_graph_context_to_the_approver():

    with TestClient(app) as client:

        body = client.post("/workflows", json={"question": QUESTION_750}).json()

        assert body["status"] == "awaiting_approval"
        assert body["approval_request"]["business_context"]["required_approvals"] == [
            "Manager"
        ]

        # Still visible when the paused workflow is fetched later.
        fetched = client.get(f"/workflows/{body['thread_id']}").json()

        assert fetched["approval_request"]["business_context"] == (
            body["approval_request"]["business_context"]
        )


# ============================================================
# DECISIONS STAY WITH THE POLICY TOOL
# ============================================================

def test_no_approval_path_is_unchanged():

    result = agent_graph.invoke({"question": QUESTION_400}, config=new_config())

    assert "__interrupt__" not in result
    assert result["approval_status"] == "not_required"
    assert result["status"] == "success"
    assert result["action_result"]["success"] is True
    assert result["business_context"]["approval_required"] is False


def test_approved_and_rejected_paths_are_unchanged():

    approved, rejected = new_config(), new_config()

    agent_graph.invoke({"question": QUESTION_750}, config=approved)
    agent_graph.invoke({"question": QUESTION_750}, config=rejected)

    approved_result = agent_graph.invoke(Command(resume="approve"), config=approved)
    rejected_result = agent_graph.invoke(Command(resume="reject"), config=rejected)

    assert approved_result["status"] == "success"
    assert approved_result["action_result"]["success"] is True
    assert rejected_result["status"] == "rejected"
    assert "action_result" not in rejected_result


def test_graph_disagreement_does_not_change_approval_or_action(monkeypatch):
    """The graph says only Finance must approve; the policy tool says
    Manager. The policy tool wins; the approver is told about it."""

    def disagreeing(employee_id, expense_amount=None):
        return {
            "success": True, "source": "context_graph",
            "employee": {"employee_id": employee_id, "departments": [], "managers": []},
            "process": "process:expense_reimbursement",
            "expense_amount": expense_amount,
            "approval_required": True,
            "required_approvals": ["Finance"],
            "applicable_rules": [],
        }

    monkeypatch.setattr(agent_graph_module, "get_business_context", disagreeing)

    config = new_config()

    result = agent_graph.invoke({"question": QUESTION_750}, config=config)

    request = result["__interrupt__"][0].value

    assert request["policy"]["approval"] == "Manager approval required."
    assert request["business_context"]["policy_check"]["consistent"] is False

    final = agent_graph.invoke(Command(resume="approve"), config=config)

    assert final["status"] == "success"
    assert final["action_result"]["success"] is True


def test_graph_failure_leaves_the_workflow_unchanged(broken_graph):

    config = new_config()

    result = agent_graph.invoke({"question": QUESTION_750}, config=config)

    request = result["__interrupt__"][0].value

    assert request["policy"]["approval"] == "Manager approval required."
    assert "business_context" not in request

    final = agent_graph.invoke(Command(resume="approve"), config=config)

    assert final["status"] == "success"
    assert final["action_result"]["success"] is True
    assert final["business_context"]["success"] is False


def test_unknown_employee_still_recovers_from_the_employee_lookup():

    result = agent_graph.invoke(
        {"question": "For employee E999, process a $750 expense."},
        config=new_config(),
    )

    assert result["status"] == "failed"
    assert result["failed_node"] == "employee"


# ============================================================
# FINAL ANSWER CONTEXT: GRAPH + DOCUMENTS, ONE LOOKUP
# ============================================================

def test_final_answer_uses_the_workflow_context_without_a_second_lookup(monkeypatch):

    prompts, lookups = [], []

    real = agent_graph_module.get_business_context

    def counting(**kwargs):
        lookups.append(kwargs)
        return real(**kwargs)

    def fail_retrieve(*args, **kwargs):
        raise AssertionError("final_node must reuse state['business_context']")

    monkeypatch.setattr(agent_graph_module, "get_business_context", counting)
    monkeypatch.setattr(agent_graph_module, "retrieve_graph_context", fail_retrieve)
    monkeypatch.setattr(
        agent_graph_module, "generate_answer",
        lambda question, context: prompts.append(context) or "Stubbed answer.",
    )

    config = new_config()

    agent_graph.invoke({"question": QUESTION_750}, config=config)
    result = agent_graph.invoke(Command(resume="approve"), config=config)

    assert result["status"] == "success"
    assert lookups == [{"employee_id": "E102", "expense_amount": 750.0}]

    [prompt] = prompts

    graph_part = prompt.split("Context Graph:", 1)[1].split("Human Approval Status:", 1)[0]

    assert "manager Priya (Manager)" in graph_part
    assert "policy_rule:expense_approval_over_500" in graph_part
    assert "7. Expenses above $500 require manager approval." in prompt


def test_workflows_without_context_state_fall_back_to_a_lookup(monkeypatch):
    """Checkpoints created before the context node have no
    business_context; final_node still adds graph facts."""

    prompts = []

    monkeypatch.setattr(
        agent_graph_module, "generate_answer",
        lambda question, context: prompts.append(context) or "Stubbed answer.",
    )

    agent_graph_module.final_node({
        "question": QUESTION_400,
        "employee_data": {"success": True, "employee": {"employee_id": "E103"}},
        "policy_result": {"success": True, "expense_amount": 400.0,
                          "approval": "No approval required."},
        "document_context": "docs",
    })

    assert "Employee E103: department Marketing" in prompts[0]


# ============================================================
# READ-ONLY
# ============================================================

def test_workflow_does_not_change_the_graph():

    before = graph_size()

    config = new_config()

    agent_graph.invoke({"question": QUESTION_750}, config=config)
    agent_graph.invoke(Command(resume="approve"), config=config)
    agent_graph.invoke({"question": QUESTION_400}, config=new_config())

    assert graph_size() == before
