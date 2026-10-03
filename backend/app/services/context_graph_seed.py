"""
Context graph content (CG2).

1. Curated expense-reimbursement knowledge from
   data/expense_process.txt: the process, its steps, roles,
   systems and approval rules, plus which LangGraph nodes and tools
   implement which steps. Every step and rule cites its document
   line in source_ref; document text itself is not copied.

2. A projection of the employees table (the system of record):
   employee -> department and employee -> manager.

sync_context_graph() applies both in one transaction and is
idempotent. It runs at application startup after the employee seed.
"""

import re

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.services.context_graph_service import (
    EdgeType as E,
    NodeType as N,
    SourceType,
    delete_edge,
    neighbors,
    upsert_edge,
    upsert_node,
)
from app.services.db import session_scope
from app.services.db_models import ContextNode, Employee


PROCESS_DOCUMENT = "data/expense_process.txt"

PROCESS = "process:expense_reimbursement"
POLICY = "policy:expense_reimbursement"
DOCUMENT = f"document:{PROCESS_DOCUMENT}"

RULE_OVER_500 = "policy_rule:expense_approval_over_500"
RULE_OVER_5000 = "policy_rule:expense_approval_over_5000"

MANAGER_ROLE = "role:manager"


def step_key(order: int) -> str:

    return f"process_step:expense_reimbursement:{order:02d}"


def document_line(order: int) -> str:
    """Step n is on line 2n + 1 of the process document."""

    return f"{DOCUMENT}#L{2 * order + 1}"


# ============================================================
# CURATED EXPENSE-REIMBURSEMENT KNOWLEDGE
# ============================================================

ROLES = {
    "role:employee": "Employee",
    "role:finance": "Finance",
    MANAGER_ROLE: "Manager",
}

SYSTEMS = {
    "system:accounting_system": "Accounting system",
    "system:email": "Email",
}

# order: (label, performed by role or None if the document does not
#         say who performs it, systems used)
STEPS = {
    1: ("Submit expense claim with receipt", "role:employee", []),
    2: ("Receive expense claim", "role:finance", []),
    3: ("Validate receipt", "role:finance", []),
    4: ("Check expense reimbursement policy", "role:finance", []),
    5: ("Verify employee identity and department", "role:finance", []),
    6: ("Check department budget", "role:finance", []),
    7: ("Manager approval for expenses above $500", MANAGER_ROLE, []),
    8: ("Finance approval for expenses above $5,000", "role:finance", []),
    9: ("Record approved expense in accounting system", "role:finance",
        ["system:accounting_system"]),
    10: ("Notify employee of the final decision", None, ["system:email"]),
}

# Amounts strictly above the threshold require approval from the
# role. The same thresholds are applied by
# tool_service.lookup_expense_policy (the authoritative logic); a
# parity test keeps them in agreement.
RULES = {
    RULE_OVER_500: {
        "label": "Expenses above $500 require manager approval",
        "threshold": 500,
        "approver": MANAGER_ROLE,
        "step": 7,
    },
    RULE_OVER_5000: {
        "label": "Expenses above $5,000 require finance approval",
        "threshold": 5000,
        "approver": "role:finance",
        "step": 8,
    },
}

# Automation that exists today: LangGraph nodes and tools, and the
# steps they implement. Steps with no implementation (receipt check,
# budget check, notification, ...) are what Workflow Discovery will
# surface.
AGENT_NODES = {
    "agent_node:employee": ("Employee lookup node", {5: None}),
    "agent_node:policy": ("Policy evaluation node", {4: None}),
    "agent_node:rag": ("Document retrieval node", {4: None}),
    "agent_node:approval": (
        "Human approval node",
        {
            7: None,
            # One decision is collected even when both manager and
            # finance approval are required.
            8: {"coverage": "partial"},
        },
    ),
    "agent_node:action": ("Business action node", {9: None}),
}

TOOLS = {
    "tool:lookup_employee": ("lookup_employee", {5: None}),
    "tool:lookup_expense_policy": ("lookup_expense_policy", {4: None}),
    "tool:search_business_documents": ("search_business_documents", {4: None}),
}


def seed_expense_process(session: Session) -> list[dict]:

    results = []

    def node(key, node_type, label, source_type=SourceType.CURATED, **fields):
        results.append(upsert_node(
            node_key=key, node_type=node_type, label=label,
            source_type=source_type, session=session, **fields
        ))

    def edge(from_key, edge_type, to_key, source_type=SourceType.CURATED, **fields):
        results.append(upsert_edge(
            from_key=from_key, edge_type=edge_type, to_key=to_key,
            source_type=source_type, session=session, **fields
        ))

    # Document, process, policy
    node(DOCUMENT, N.DOCUMENT, "Expense Reimbursement Process",
         source_type=SourceType.DOCUMENT, source_ref=PROCESS_DOCUMENT)

    node(PROCESS, N.PROCESS, "Expense Reimbursement",
         source_ref=f"{DOCUMENT}#L1")
    edge(PROCESS, E.EVIDENCED_BY, DOCUMENT, source_ref=f"{DOCUMENT}#L1")

    node(POLICY, N.POLICY, "Expense reimbursement approval policy",
         source_ref=PROCESS_DOCUMENT)
    edge(POLICY, E.EVIDENCED_BY, DOCUMENT, source_ref=PROCESS_DOCUMENT)

    for key, label in ROLES.items():
        node(key, N.ROLE, label)

    for key, label in SYSTEMS.items():
        node(key, N.SYSTEM, label)

    # Steps
    for order, (label, role, systems) in STEPS.items():

        key = step_key(order)

        node(key, N.PROCESS_STEP, label,
             source_ref=document_line(order),
             properties={"order": order})

        edge(PROCESS, E.HAS_STEP, key, properties={"order": order})
        edge(key, E.EVIDENCED_BY, DOCUMENT, source_ref=document_line(order))

        if role is not None:
            edge(key, E.PERFORMED_BY, role, source_ref=document_line(order))

        for system in systems:
            edge(key, E.USES_SYSTEM, system, source_ref=document_line(order))

        if order > 1:
            edge(step_key(order - 1), E.NEXT, key)

    # Approval rules
    for key, rule in RULES.items():

        line = document_line(rule["step"])

        node(key, N.POLICY_RULE, rule["label"], source_ref=line,
             properties={
                 "threshold": rule["threshold"],
                 "comparison": "greater_than",
                 "currency": "USD",
             })

        edge(key, E.PART_OF, POLICY)
        edge(key, E.EVIDENCED_BY, DOCUMENT, source_ref=line)
        edge(key, E.REQUIRES_APPROVAL_FROM, rule["approver"],
             source_ref=line, properties={"threshold": rule["threshold"]})

        # The policy-check step and the approval step apply the rule.
        edge(step_key(4), E.GOVERNED_BY, key)
        edge(step_key(rule["step"]), E.GOVERNED_BY, key)

    # Automation mapping
    for components, node_type, ref in (
        (AGENT_NODES, N.AGENT_NODE, "app/services/agent_graph.py"),
        (TOOLS, N.TOOL, "app/services/tool_service.py"),
    ):
        for key, (label, implemented) in components.items():

            node(key, node_type, label, source_ref=ref)

            for order, properties in implemented.items():
                edge(key, E.IMPLEMENTS, step_key(order), properties=properties)

    return results


# ============================================================
# EMPLOYEE PROJECTION (system of record: employees table)
# ============================================================

def slug(value: str) -> str:

    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def department_key(department: str) -> str:

    return f"department:{slug(department)}"


def person_key(name: str) -> str:
    """
    Managers are stored as free-text names, not employee ids, so
    people are keyed by name. Two managers with the same name would
    share a node until an identity model exists.
    """

    return f"person:{slug(name)}"


def project_employees(session: Session) -> list[dict]:

    results = []
    active_keys = set()

    # Loaded up front: the loop runs further queries on this session.
    employees = list(
        session.scalars(select(Employee).order_by(Employee.employee_id))
    )

    for employee in employees:

        employee_key = f"employee:{employee.employee_id}"
        department = department_key(employee.department)
        manager = person_key(employee.manager)

        active_keys |= {employee_key, department, manager}

        # Only ids and labels: attributes stay in the employees table.
        results.append(upsert_node(
            node_key=employee_key, node_type=N.EMPLOYEE,
            label=employee.employee_id,
            source_type=SourceType.SYSTEM_OF_RECORD,
            source_ref=f"employees:{employee.employee_id}",
            session=session
        ))

        results.append(upsert_node(
            node_key=department, node_type=N.DEPARTMENT,
            label=employee.department,
            source_type=SourceType.SYSTEM_OF_RECORD,
            source_ref=f"employees.department:{employee.department}",
            session=session
        ))

        results.append(upsert_node(
            node_key=manager, node_type=N.PERSON,
            label=employee.manager,
            source_type=SourceType.SYSTEM_OF_RECORD,
            source_ref=f"employees.manager:{employee.manager}",
            session=session
        ))

        results.append(upsert_edge(
            from_key=manager, edge_type=E.HAS_ROLE, to_key=MANAGER_ROLE,
            source_type=SourceType.SYSTEM_OF_RECORD,
            source_ref=f"employees.manager:{employee.manager}",
            session=session
        ))

        for edge_type, target in (
            (E.MEMBER_OF, department),
            (E.REPORTS_TO, manager),
        ):
            replace_edge(session, employee_key, edge_type, target, results)

    results += deactivate_missing(session, active_keys)

    return results


def replace_edge(session, from_key, edge_type, target, results):
    """
    Keep exactly one edge of this type: remove edges to any other
    target (e.g. after a department change), then upsert.
    """

    current = neighbors(
        from_key, edge_types=[edge_type], include_inactive=True,
        session=session
    )

    for item in current["neighbors"]:

        old_target = item["node"]["node_key"]

        if old_target != target:
            delete_edge(from_key=from_key, edge_type=edge_type,
                        to_key=old_target, session=session)
            results.append({"change": "removed"})

    results.append(upsert_edge(
        from_key=from_key, edge_type=edge_type, to_key=target,
        source_type=SourceType.SYSTEM_OF_RECORD,
        source_ref=f"employees:{from_key.split(':', 1)[1]}",
        session=session
    ))


def deactivate_missing(session, active_keys: set[str]) -> list[dict]:
    """
    Projected nodes whose source row or value no longer exists are
    marked inactive (kept, not deleted).
    """

    results = []

    stale = session.scalars(
        select(ContextNode).where(
            ContextNode.source_type == SourceType.SYSTEM_OF_RECORD.value,
            ContextNode.node_type.in_([
                N.EMPLOYEE.value, N.DEPARTMENT.value, N.PERSON.value
            ]),
            ContextNode.is_active.is_(True),
            ContextNode.node_key.not_in(active_keys) if active_keys else true()
        )
    )

    for node in list(stale):

        results.append(upsert_node(
            node_key=node.node_key, node_type=node.node_type,
            label=node.label, source_type=node.source_type,
            source_ref=node.source_ref, properties=node.properties,
            is_active=False, session=session
        ))

    return results


# ============================================================
# ENTRY POINT
# ============================================================

def sync_context_graph() -> dict:
    """
    Apply the curated seed and the employee projection in one
    transaction. Idempotent. Returns counts per change type.
    """

    with session_scope() as session:

        results = seed_expense_process(session)
        results += project_employees(session)

    summary = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0}

    for result in results:
        summary[result["change"]] += 1

    return summary
