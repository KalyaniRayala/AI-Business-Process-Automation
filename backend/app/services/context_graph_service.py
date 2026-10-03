"""
Context graph: business-context entities and their relationships,
stored in PostgreSQL (context_nodes, context_edges).

- Node, edge and source types are controlled here; edges may only
  connect the node types listed in EDGE_RULES.
- upsert_node / upsert_edge are idempotent: repeating a call with
  the same values changes nothing (not even updated_at).
- Writes join the caller's transaction when a session is passed,
  otherwise they use their own.
- neighbors() walks the graph breadth-first up to MAX_DEPTH hops,
  visiting each node once, so cycles terminate.

Business tables remain the system of record; projected nodes point
back to them through source_ref.
"""

import re
from contextlib import contextmanager
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.audit_service import sanitize_details
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode, utc_now


# ============================================================
# VOCABULARY
# ============================================================

class NodeType(StrEnum):

    PROCESS = "process"
    PROCESS_STEP = "process_step"
    ROLE = "role"
    SYSTEM = "system"
    POLICY = "policy"
    POLICY_RULE = "policy_rule"
    DOCUMENT = "document"
    EMPLOYEE = "employee"
    DEPARTMENT = "department"
    PERSON = "person"
    AGENT_NODE = "agent_node"
    TOOL = "tool"


class EdgeType(StrEnum):

    HAS_STEP = "has_step"
    NEXT = "next"
    PERFORMED_BY = "performed_by"
    USES_SYSTEM = "uses_system"
    GOVERNED_BY = "governed_by"
    REQUIRES_APPROVAL_FROM = "requires_approval_from"
    PART_OF = "part_of"
    EVIDENCED_BY = "evidenced_by"
    MEMBER_OF = "member_of"
    REPORTS_TO = "reports_to"
    HAS_ROLE = "has_role"
    IMPLEMENTS = "implements"
    IDENTIFIED_AS = "identified_as"


class SourceType(StrEnum):

    SYSTEM_OF_RECORD = "system_of_record"
    DOCUMENT = "document"
    CURATED = "curated"
    DERIVED = "derived"
    CONNECTOR = "connector"


N = NodeType

# Allowed (from node type, to node type) pairs for each edge type.
EDGE_RULES: dict[EdgeType, set[tuple[NodeType, NodeType]]] = {
    EdgeType.HAS_STEP: {(N.PROCESS, N.PROCESS_STEP)},
    EdgeType.NEXT: {(N.PROCESS_STEP, N.PROCESS_STEP)},
    EdgeType.PERFORMED_BY: {(N.PROCESS_STEP, N.ROLE)},
    EdgeType.USES_SYSTEM: {(N.PROCESS_STEP, N.SYSTEM)},
    EdgeType.GOVERNED_BY: {(N.PROCESS_STEP, N.POLICY_RULE)},
    EdgeType.REQUIRES_APPROVAL_FROM: {(N.POLICY_RULE, N.ROLE)},
    EdgeType.PART_OF: {(N.POLICY_RULE, N.POLICY)},
    EdgeType.EVIDENCED_BY: {
        (N.PROCESS, N.DOCUMENT),
        (N.PROCESS_STEP, N.DOCUMENT),
        (N.POLICY, N.DOCUMENT),
        (N.POLICY_RULE, N.DOCUMENT),
    },
    EdgeType.MEMBER_OF: {(N.EMPLOYEE, N.DEPARTMENT), (N.PERSON, N.DEPARTMENT)},
    EdgeType.REPORTS_TO: {
        (N.EMPLOYEE, N.PERSON),
        (N.EMPLOYEE, N.EMPLOYEE),
        (N.PERSON, N.PERSON),
    },
    EdgeType.HAS_ROLE: {(N.PERSON, N.ROLE), (N.EMPLOYEE, N.ROLE)},
    EdgeType.IMPLEMENTS: {
        (N.AGENT_NODE, N.PROCESS_STEP),
        (N.TOOL, N.PROCESS_STEP),
    },
    # An employee record and the directory identity of the same person
    # (enterprise connectors).
    EdgeType.IDENTIFIED_AS: {(N.EMPLOYEE, N.PERSON)},
}


# ============================================================
# VALIDATION
# ============================================================

MAX_KEY_LENGTH = 200
MAX_LABEL_LENGTH = 200
MAX_SOURCE_REF_LENGTH = 300

# "<node_type>:<identifier>", e.g. "employee:E102",
# "process_step:expense_reimbursement:07"
KEY_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-/#]*$")

MAX_DEPTH = 3
MAX_NEIGHBORS = 1000


def validate_node_key(node_key: str, node_type: NodeType) -> None:

    if not isinstance(node_key, str) or len(node_key) > MAX_KEY_LENGTH:
        raise ValueError(f"Invalid node key: {node_key!r}")

    prefix, separator, identifier = node_key.partition(":")

    if not separator or prefix != node_type.value:
        raise ValueError(
            f"Node key {node_key!r} must start with '{node_type.value}:'."
        )

    if not KEY_IDENTIFIER.match(identifier):
        raise ValueError(f"Invalid node key identifier: {node_key!r}")


def validate_text(name: str, value: str | None, max_length: int, required: bool):

    if value is None:
        if required:
            raise ValueError(f"{name} is required.")
        return

    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"Invalid {name}: {value!r}")

    if len(value) > max_length:
        raise ValueError(f"{name} is longer than {max_length} characters.")


def validate_confidence(confidence) -> Decimal | None:

    if confidence is None:
        return None

    value = Decimal(str(confidence)).quantize(Decimal("0.001"))

    if not Decimal("0") <= value <= Decimal("1"):
        raise ValueError("confidence must be between 0 and 1.")

    return value


# ============================================================
# SESSION HANDLING
# ============================================================

@contextmanager
def use_session(session: Session | None):
    """
    Join the caller's transaction, or open a new one.
    """

    if session is not None:
        yield session
        return

    with session_scope() as own_session:
        yield own_session


# ============================================================
# NODES
# ============================================================

def upsert_node(
    *,
    node_key: str,
    node_type: NodeType | str,
    label: str,
    source_type: SourceType | str,
    source_ref: str | None = None,
    properties: dict | None = None,
    is_active: bool = True,
    session: Session | None = None
) -> dict:
    """
    Create the node, or update it if any value differs.

    Returns the node with "change": "created" | "updated" | "unchanged".
    A node's type cannot change.
    """

    node_type = NodeType(node_type)
    source_type = SourceType(source_type)

    validate_node_key(node_key, node_type)
    validate_text("label", label, MAX_LABEL_LENGTH, required=True)
    validate_text("source_ref", source_ref, MAX_SOURCE_REF_LENGTH, required=False)

    values = {
        "label": label,
        "source_type": source_type.value,
        "source_ref": source_ref,
        "properties": sanitize_details(properties),
        "is_active": bool(is_active),
    }

    try:

        with use_session(session) as active:
            return save_node(active, node_key, node_type, values)

    except IntegrityError:

        if session is not None:
            raise

        # A concurrent call created it first; apply as an update.
        with session_scope() as active:
            return save_node(active, node_key, node_type, values)


def save_node(session, node_key, node_type, values) -> dict:

    node = session.scalar(
        select(ContextNode).where(ContextNode.node_key == node_key)
    )

    if node is None:

        node = ContextNode(node_key=node_key, node_type=node_type.value, **values)

        session.add(node)
        session.flush()

        return node_to_dict(node, change="created")

    if node.node_type != node_type.value:
        raise ValueError(
            f"Node {node_key!r} is a {node.node_type!r}, not {node_type.value!r}."
        )

    change = apply_changes(node, values)

    session.flush()

    return node_to_dict(node, change=change)


def get_node(node_key: str, session: Session | None = None) -> dict | None:

    with use_session(session) as active:

        node = active.scalar(
            select(ContextNode).where(ContextNode.node_key == node_key)
        )

        return None if node is None else node_to_dict(node)


# ============================================================
# EDGES
# ============================================================

def upsert_edge(
    *,
    from_key: str,
    edge_type: EdgeType | str,
    to_key: str,
    source_type: SourceType | str,
    source_ref: str | None = None,
    properties: dict | None = None,
    confidence: float | Decimal | None = None,
    session: Session | None = None
) -> dict:
    """
    Create the edge between two existing nodes, or update it if
    any value differs. Only EDGE_RULES type pairs are allowed.

    Returns the edge with "change": "created" | "updated" | "unchanged".
    """

    edge_type = EdgeType(edge_type)
    source_type = SourceType(source_type)

    validate_text("source_ref", source_ref, MAX_SOURCE_REF_LENGTH, required=False)

    if from_key == to_key:
        raise ValueError("An edge cannot connect a node to itself.")

    values = {
        "source_type": source_type.value,
        "source_ref": source_ref,
        "properties": sanitize_details(properties),
        "confidence": validate_confidence(confidence),
    }

    try:

        with use_session(session) as active:
            return save_edge(active, from_key, edge_type, to_key, values)

    except IntegrityError:

        if session is not None:
            raise

        with session_scope() as active:
            return save_edge(active, from_key, edge_type, to_key, values)


def save_edge(session, from_key, edge_type, to_key, values) -> dict:

    nodes = {
        node.node_key: node
        for node in session.scalars(
            select(ContextNode).where(
                ContextNode.node_key.in_([from_key, to_key])
            )
        )
    }

    for key in (from_key, to_key):
        if key not in nodes:
            raise LookupError(f"Context node {key!r} does not exist.")

    source, target = nodes[from_key], nodes[to_key]

    pair = (NodeType(source.node_type), NodeType(target.node_type))

    if pair not in EDGE_RULES[edge_type]:
        raise ValueError(
            f"Edge {edge_type.value!r} cannot connect "
            f"{pair[0].value!r} to {pair[1].value!r}."
        )

    edge = session.scalar(
        select(ContextEdge).where(
            ContextEdge.from_node_id == source.id,
            ContextEdge.edge_type == edge_type.value,
            ContextEdge.to_node_id == target.id
        )
    )

    if edge is None:

        edge = ContextEdge(
            from_node_id=source.id,
            to_node_id=target.id,
            edge_type=edge_type.value,
            **values
        )

        session.add(edge)
        session.flush()

        change = "created"

    else:

        change = apply_changes(edge, values)

        session.flush()

    return edge_to_dict(edge, from_key, to_key, change=change)


def delete_edge(
    *,
    from_key: str,
    edge_type: EdgeType | str,
    to_key: str,
    session: Session | None = None
) -> bool:
    """
    Remove one edge (e.g. when a projected relationship changed).
    Returns False if it did not exist.
    """

    edge_type = EdgeType(edge_type)

    with use_session(session) as active:

        source = select(ContextNode.id).where(
            ContextNode.node_key == from_key
        ).scalar_subquery()

        target = select(ContextNode.id).where(
            ContextNode.node_key == to_key
        ).scalar_subquery()

        edge = active.scalar(
            select(ContextEdge).where(
                ContextEdge.from_node_id == source,
                ContextEdge.edge_type == edge_type.value,
                ContextEdge.to_node_id == target
            )
        )

        if edge is None:
            return False

        active.delete(edge)
        active.flush()

        return True


# ============================================================
# TRAVERSAL
# ============================================================

def neighbors(
    node_key: str,
    *,
    depth: int = 1,
    direction: str = "out",
    edge_types: list[EdgeType | str] | None = None,
    include_inactive: bool = False,
    limit: int = MAX_NEIGHBORS,
    session: Session | None = None
) -> dict:
    """
    Nodes reachable from node_key within `depth` hops (1..MAX_DEPTH).

    direction: "out" follows edges from->to, "in" to->from, "both".
    Each node is returned once, at its shortest distance, with the
    edge that first reached it. Inactive nodes are skipped and not
    traversed unless include_inactive is true.

    Raises LookupError if node_key does not exist.
    """

    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}.")

    if direction not in ("out", "in", "both"):
        raise ValueError("direction must be 'out', 'in' or 'both'.")

    if not 1 <= limit <= MAX_NEIGHBORS:
        raise ValueError(f"limit must be between 1 and {MAX_NEIGHBORS}.")

    edge_type_values = (
        None if edge_types is None
        else [EdgeType(edge_type).value for edge_type in edge_types]
    )

    with use_session(session) as active:

        start = active.scalar(
            select(ContextNode).where(ContextNode.node_key == node_key)
        )

        if start is None:
            raise LookupError(f"Context node {node_key!r} does not exist.")

        visited = {start.id}
        frontier = [start.id]
        found = []
        truncated = False

        for level in range(1, depth + 1):

            if not frontier or truncated:
                break

            frontier_ids = set(frontier)
            next_frontier = []

            for edge, from_node, to_node in level_edges(
                active, frontier, direction, edge_type_values
            ):

                candidates = []

                if direction in ("out", "both") and from_node.id in frontier_ids:
                    candidates.append(to_node)

                if direction in ("in", "both") and to_node.id in frontier_ids:
                    candidates.append(from_node)

                for neighbor in candidates:

                    # Visited check makes cycles terminate.
                    if neighbor.id in visited:
                        continue

                    if not neighbor.is_active and not include_inactive:
                        continue

                    if len(found) >= limit:
                        truncated = True
                        break

                    visited.add(neighbor.id)
                    next_frontier.append(neighbor.id)

                    found.append({
                        "depth": level,
                        "node": node_to_dict(neighbor),
                        "via": edge_to_dict(
                            edge, from_node.node_key, to_node.node_key
                        ),
                    })

                if truncated:
                    break

            frontier = next_frontier

        return {
            "start": node_to_dict(start),
            "depth": depth,
            "direction": direction,
            "neighbors": found,
            "truncated": truncated,
        }


def level_edges(session, frontier, direction, edge_type_values):
    """
    All edges touching the frontier nodes in the given direction,
    with both endpoint nodes, in a stable order.
    """

    if direction == "out":
        condition = ContextEdge.from_node_id.in_(frontier)
    elif direction == "in":
        condition = ContextEdge.to_node_id.in_(frontier)
    else:
        condition = or_(
            ContextEdge.from_node_id.in_(frontier),
            ContextEdge.to_node_id.in_(frontier)
        )

    query = select(ContextEdge).where(condition)

    if edge_type_values is not None:
        query = query.where(ContextEdge.edge_type.in_(edge_type_values))

    edges = list(session.scalars(query.order_by(ContextEdge.id)))

    node_ids = {e.from_node_id for e in edges} | {e.to_node_id for e in edges}

    nodes = {
        node.id: node
        for node in session.scalars(
            select(ContextNode).where(ContextNode.id.in_(node_ids))
        )
    } if node_ids else {}

    return [(e, nodes[e.from_node_id], nodes[e.to_node_id]) for e in edges]


# ============================================================
# PROCESS VIEW (read-only)
# ============================================================

def get_process_context(process_key: str) -> dict | None:
    """
    A process with its ordered steps and each step's direct
    relationships, plus the policy rules governing those steps.

    Only one-hop lookups from known nodes are used, so the result
    is bounded and cannot loop. Inactive nodes are left out.
    Returns None if the process does not exist.
    """

    with session_scope() as session:

        process = get_node(process_key, session=session)

        if process is None or process["node_type"] != NodeType.PROCESS.value:
            return None

        process_view = neighbors(process_key, direction="out", session=session)

        steps = []
        rule_keys = []

        for item in process_view["neighbors"]:

            if item["via"]["edge_type"] != EdgeType.HAS_STEP.value:
                continue

            step = item["node"]

            order = (
                (item["via"]["properties"] or {}).get("order")
                or (step["properties"] or {}).get("order")
            )

            outgoing = neighbors(step["node_key"], direction="out", session=session)
            incoming = neighbors(step["node_key"], direction="in", session=session)

            def targets(edge_type):
                return [
                    n for n in outgoing["neighbors"]
                    if n["via"]["edge_type"] == edge_type
                ]

            governed_by = [
                n["node"]["node_key"] for n in targets(EdgeType.GOVERNED_BY)
            ]

            rule_keys += [key for key in governed_by if key not in rule_keys]

            steps.append({
                "order": order,
                "step": step,
                "next_steps": [n["node"]["node_key"] for n in targets(EdgeType.NEXT)],
                "performed_by": [n["node"] for n in targets(EdgeType.PERFORMED_BY)],
                "uses_systems": [n["node"] for n in targets(EdgeType.USES_SYSTEM)],
                "governed_by": governed_by,
                "implemented_by": [
                    {"component": n["node"], "properties": n["via"]["properties"]}
                    for n in incoming["neighbors"]
                    if n["via"]["edge_type"] == EdgeType.IMPLEMENTS.value
                ],
                "evidence": evidence(targets(EdgeType.EVIDENCED_BY)),
            })

        steps.sort(key=lambda s: (s["order"] is None, s["order"] or 0))

        policy_rules = []

        for rule_key in rule_keys:

            rule_view = neighbors(rule_key, direction="out", session=session)

            def rule_targets(edge_type):
                return [
                    n for n in rule_view["neighbors"]
                    if n["via"]["edge_type"] == edge_type
                ]

            policy_rules.append({
                "rule": rule_view["start"],
                "approvers": [
                    {
                        "role": n["node"],
                        "threshold": (n["via"]["properties"] or {}).get("threshold"),
                    }
                    for n in rule_targets(EdgeType.REQUIRES_APPROVAL_FROM)
                ],
                "policies": [
                    n["node"]["node_key"] for n in rule_targets(EdgeType.PART_OF)
                ],
                "evidence": evidence(rule_targets(EdgeType.EVIDENCED_BY)),
            })

        return {
            "process": process,
            "steps": steps,
            "policy_rules": policy_rules,
            "evidence": evidence([
                n for n in process_view["neighbors"]
                if n["via"]["edge_type"] == EdgeType.EVIDENCED_BY.value
            ]),
        }


def get_employee_context(employee_id: str) -> dict | None:
    """
    An employee's department(s) and manager(s), with the managers'
    roles. Returns None if the employee node does not exist.
    """

    employee_key = f"{NodeType.EMPLOYEE.value}:{employee_id}"

    with session_scope() as session:

        employee = get_node(employee_key, session=session)

        if employee is None:
            return None

        related = neighbors(
            employee_key,
            direction="out",
            edge_types=[EdgeType.MEMBER_OF, EdgeType.REPORTS_TO],
            session=session
        )["neighbors"]

        managers = []

        for item in related:

            if item["via"]["edge_type"] != EdgeType.REPORTS_TO.value:
                continue

            roles = neighbors(
                item["node"]["node_key"],
                direction="out",
                edge_types=[EdgeType.HAS_ROLE],
                session=session
            )["neighbors"]

            managers.append({
                "person": item["node"],
                "roles": [role["node"] for role in roles],
            })

        return {
            "employee": employee,
            "departments": [
                item["node"] for item in related
                if item["via"]["edge_type"] == EdgeType.MEMBER_OF.value
            ],
            "managers": managers,
        }


def get_applicable_rules(process_key: str, amount: float) -> list[dict] | None:
    """
    Policy rules of the process that apply to an expense amount,
    with the roles they require approval from and the steps they
    govern. Returns None if the process does not exist.

    Informational: tool_service.lookup_expense_policy remains the
    authoritative approval logic.
    """

    process = get_process_context(process_key)

    if process is None:
        return None

    applicable = []

    for rule in process["policy_rules"]:

        properties = rule["rule"]["properties"] or {}
        threshold = properties.get("threshold")
        comparison = properties.get("comparison")

        if threshold is None:
            continue

        if comparison != "greater_than":
            raise ValueError(
                f"Unsupported rule comparison {comparison!r} on "
                f"{rule['rule']['node_key']}."
            )

        if not amount > threshold:
            continue

        applicable.append({
            "rule": rule["rule"],
            "threshold": threshold,
            "approver_roles": [a["role"] for a in rule["approvers"]],
            "governs_steps": [
                step["step"] for step in process["steps"]
                if rule["rule"]["node_key"] in step["governed_by"]
            ],
            "evidence": rule["evidence"],
        })

    return sorted(applicable, key=lambda r: r["threshold"])


def evidence(evidenced_by: list[dict]) -> list[str]:
    """
    Source references of evidenced_by edges (the exact document
    location when known, otherwise the document node).
    """

    return [
        item["via"]["source_ref"] or item["node"]["node_key"]
        for item in evidenced_by
    ]


# ============================================================
# HELPERS
# ============================================================

def apply_changes(row, values: dict) -> str:
    """
    Set only the values that differ; bump updated_at only then.
    """

    changed = False

    for name, value in values.items():

        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True

    if not changed:
        return "unchanged"

    row.updated_at = utc_now()

    return "updated"


def node_to_dict(node: ContextNode, change: str | None = None) -> dict:

    result = {
        "node_key": node.node_key,
        "node_type": node.node_type,
        "label": node.label,
        "properties": node.properties,
        "source_type": node.source_type,
        "source_ref": node.source_ref,
        "is_active": node.is_active,
    }

    if change is not None:
        result["change"] = change

    return result


def edge_to_dict(
    edge: ContextEdge,
    from_key: str,
    to_key: str,
    change: str | None = None
) -> dict:

    result = {
        "from_key": from_key,
        "edge_type": edge.edge_type,
        "to_key": to_key,
        "properties": edge.properties,
        "source_type": edge.source_type,
        "source_ref": edge.source_ref,
        "confidence": (
            None if edge.confidence is None else float(edge.confidence)
        ),
    }

    if change is not None:
        result["change"] = change

    return result
