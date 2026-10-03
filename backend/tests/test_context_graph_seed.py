"""
Context graph CG2: curated expense-process seed and the employee
projection. The session fixture in conftest already ran
sync_context_graph(), as the app does at startup.
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.services import context_graph_seed, tool_service
from app.services.agent_graph import agent_graph
from app.services.context_graph_seed import (
    AGENT_NODES,
    DOCUMENT,
    POLICY,
    PROCESS,
    PROCESS_DOCUMENT,
    STEPS,
    TOOLS,
    step_key,
    sync_context_graph,
)
from app.services.context_graph_service import get_node, neighbors, upsert_node
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode, Employee
from app.services.tool_service import lookup_expense_policy


DOCUMENT_LINES = Path(PROCESS_DOCUMENT).read_text(encoding="utf-8").splitlines()


def out(key: str, edge_type: str) -> list[dict]:

    return neighbors(key, edge_types=[edge_type])["neighbors"]


def out_keys(key: str, edge_type: str) -> list[str]:

    return [item["node"]["node_key"] for item in out(key, edge_type)]


def in_keys(key: str, edge_type: str) -> list[str]:

    return [
        item["node"]["node_key"]
        for item in neighbors(key, direction="in", edge_types=[edge_type])["neighbors"]
    ]


def cited_line(source_ref: str) -> str:
    """Text of the document line a source_ref points to."""

    document, line = source_ref.rsplit("#L", 1)

    assert document == DOCUMENT

    return DOCUMENT_LINES[int(line) - 1]


# ============================================================
# IDEMPOTENCY
# ============================================================

def test_sync_is_idempotent():

    # The first run may reconcile rows other tests left behind
    # (e.g. deactivate a test department no employee belongs to).
    sync_context_graph()

    second = sync_context_graph()

    assert second["created"] == second["updated"] == second["removed"] == 0
    assert second["unchanged"] > 0


def test_sync_is_atomic(monkeypatch):

    marker = f"system:atomic_{uuid.uuid4().hex[:8]}"
    original = context_graph_seed.seed_expense_process

    def seed_then_fail(session):
        original(session)
        upsert_node(node_key=marker, node_type="system", label="x",
                    source_type="curated", session=session)
        raise RuntimeError("seed failed")

    monkeypatch.setattr(context_graph_seed, "seed_expense_process", seed_then_fail)

    with pytest.raises(RuntimeError):
        sync_context_graph()

    assert get_node(marker) is None


# ============================================================
# PROCESS STRUCTURE AND PROVENANCE
# ============================================================

def test_process_has_ten_ordered_steps_in_a_linear_chain():

    steps = out(PROCESS, "has_step")

    assert sorted(item["via"]["properties"]["order"] for item in steps) == list(range(1, 11))
    assert {item["node"]["node_key"] for item in steps} == {step_key(n) for n in range(1, 11)}

    for order in range(1, 11):

        expected_next = [step_key(order + 1)] if order < 10 else []
        expected_previous = [step_key(order - 1)] if order > 1 else []

        assert out_keys(step_key(order), "next") == expected_next
        assert in_keys(step_key(order), "next") == expected_previous


def test_every_step_cites_its_own_document_line():

    for order in range(1, 11):

        step = get_node(step_key(order))

        assert step["properties"] == {"order": order}
        assert cited_line(step["source_ref"]).startswith(f"{order}. ")
        assert out_keys(step_key(order), "evidenced_by") == [DOCUMENT]


def test_no_document_text_is_copied_into_the_graph():

    with session_scope() as session:

        stored = " ".join(
            str(value)
            for row in session.execute(select(
                ContextNode.label, ContextNode.properties
            ))
            for value in row
        ) + " ".join(
            str(p) for p in session.scalars(select(ContextEdge.properties))
        )

    for phrase in ("along with the receipt", "contains the required information",
                   "sufficient department budget", "email notification about"):
        assert phrase not in stored


def test_roles_and_systems():

    for order in range(1, 10):
        assert len(out_keys(step_key(order), "performed_by")) == 1

    # The document does not say who notifies the employee.
    assert out_keys(step_key(10), "performed_by") == []

    assert out_keys(step_key(1), "performed_by") == ["role:employee"]
    assert out_keys(step_key(7), "performed_by") == ["role:manager"]
    assert out_keys(step_key(8), "performed_by") == ["role:finance"]

    assert out_keys(step_key(9), "uses_system") == ["system:accounting_system"]
    assert out_keys(step_key(10), "uses_system") == ["system:email"]


def test_rules_cite_their_thresholds():

    rules = in_keys(POLICY, "part_of")

    assert sorted(rules) == [
        "policy_rule:expense_approval_over_500",
        "policy_rule:expense_approval_over_5000",
    ]

    for rule_key in rules:

        rule = get_node(rule_key)
        threshold = rule["properties"]["threshold"]
        line = cited_line(rule["source_ref"])

        assert f"${threshold:,}" in line
        assert "approval" in line

    assert sorted(in_keys("policy_rule:expense_approval_over_500", "governed_by")) == [
        step_key(4), step_key(7)
    ]


# ============================================================
# PARITY WITH THE AUTHORITATIVE POLICY LOGIC
# ============================================================

ROLE_APPROVAL_TEXT = {
    frozenset(): "No approval required.",
    frozenset({"role:manager"}): "Manager approval required.",
    frozenset({"role:manager", "role:finance"}):
        "Manager approval and finance approval required.",
}


def approvals_from_graph(amount: float) -> str:

    roles = set()

    for rule_key in in_keys(POLICY, "part_of"):

        threshold = get_node(rule_key)["properties"]["threshold"]

        if amount > threshold:
            roles |= set(out_keys(rule_key, "requires_approval_from"))

    return ROLE_APPROVAL_TEXT[frozenset(roles)]


@pytest.mark.parametrize(
    "amount",
    [0, 0.01, 1, 499.99, 500, 500.01, 750, 4999.99, 5000, 5000.01, 6000, 1_000_000],
)
def test_graph_rules_match_lookup_expense_policy(amount):

    assert approvals_from_graph(amount) == lookup_expense_policy(amount)["approval"]


# ============================================================
# AUTOMATION MAPPING
# ============================================================

def test_agent_nodes_and_tools_exist_in_code():

    for key in AGENT_NODES:
        assert key.split(":", 1)[1] in agent_graph.nodes

    for key, (name, _) in TOOLS.items():
        assert key.split(":", 1)[1] == name
        assert callable(getattr(tool_service, name))


def test_steps_without_automation():
    """The gaps Workflow Discovery will start from."""

    unimplemented = {
        order for order in STEPS
        if not in_keys(step_key(order), "implements")
    }

    assert unimplemented == {1, 2, 3, 6, 10}

    [partial] = [
        item for item in neighbors(
            "agent_node:approval", edge_types=["implements"]
        )["neighbors"]
        if item["node"]["node_key"] == step_key(8)
    ]

    assert partial["via"]["properties"] == {"coverage": "partial"}


# ============================================================
# EMPLOYEE PROJECTION
# ============================================================

def test_every_employee_is_projected():

    with session_scope() as session:
        employees = list(session.scalars(select(Employee)))

    for employee in employees:

        key = f"employee:{employee.employee_id}"
        node = get_node(key)

        assert node["is_active"] is True
        assert node["source_ref"] == f"employees:{employee.employee_id}"
        # Only the id: attributes stay in the employees table.
        assert node["label"] == employee.employee_id
        assert node["properties"] is None

    assert out_keys("employee:E102", "member_of") == ["department:finance"]
    assert out_keys("employee:E102", "reports_to") == ["person:priya"]
    assert out_keys("person:priya", "has_role") == ["role:manager"]
    assert sorted(in_keys("person:ravi", "reports_to")) == [
        "employee:E101", "employee:E104"
    ]


def test_projection_follows_changes_to_the_employees_table():

    employee_id = f"T{uuid.uuid4().hex[:8].upper()}"
    key = f"employee:{employee_id}"
    department = f"QA {employee_id}"
    manager = f"Boss {employee_id}"

    department_key = f"department:qa_{employee_id.lower()}"
    person_key = f"person:boss_{employee_id.lower()}"

    try:

        with session_scope() as session:
            session.add(Employee(employee_id=employee_id, name="Test", department=department,
                                 manager=manager, annual_budget=1000))

        sync_context_graph()

        assert out_keys(key, "member_of") == [department_key]
        assert out_keys(key, "reports_to") == [person_key]

        # Department change: the edge moves; the old department is
        # no longer used and becomes inactive.
        with session_scope() as session:
            session.get(Employee, employee_id).department = "Finance"

        summary = sync_context_graph()

        assert summary["removed"] == 1
        assert out_keys(key, "member_of") == ["department:finance"]
        assert get_node(department_key)["is_active"] is False

        # Employee removed: nodes kept but inactive.
        with session_scope() as session:
            session.delete(session.get(Employee, employee_id))

        sync_context_graph()

        assert get_node(key)["is_active"] is False
        assert get_node(person_key)["is_active"] is False
        assert get_node("employee:E102")["is_active"] is True
        assert get_node("department:finance")["is_active"] is True

    finally:

        with session_scope() as session:
            leftover = session.get(Employee, employee_id)
            if leftover is not None:
                session.delete(leftover)


def test_reactivation_when_employee_returns():

    node = get_node("employee:E103")

    upsert_node(node_key="employee:E103", node_type="employee", label="E103",
                source_type="system_of_record", source_ref=node["source_ref"],
                is_active=False)

    sync_context_graph()

    assert get_node("employee:E103")["is_active"] is True
