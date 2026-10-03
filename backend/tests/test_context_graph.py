"""
Context graph CG1: schema, controlled types, idempotent upserts and
depth-limited, cycle-safe traversal. No application data is seeded.
"""

import uuid

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from app.services.context_graph_service import (
    EDGE_RULES,
    MAX_DEPTH,
    EdgeType,
    NodeType,
    get_node,
    neighbors,
    upsert_edge,
    upsert_node,
)
from app.services.db import engine, session_scope
from app.services.db_models import ContextEdge, ContextNode


@pytest.fixture
def ns() -> str:
    """Unique key namespace; the PostgreSQL test database keeps rows."""

    return f"t{uuid.uuid4().hex[:12]}"


def node(ns, node_type: str, name: str, **fields) -> dict:

    return upsert_node(
        node_key=f"{node_type}:{ns}:{name}",
        node_type=node_type,
        label=fields.pop("label", name),
        source_type=fields.pop("source_type", "curated"),
        **fields
    )


def key(ns, node_type: str, name: str) -> str:

    return f"{node_type}:{ns}:{name}"


def edge(ns, from_type, from_name, edge_type, to_type, to_name, **fields) -> dict:

    return upsert_edge(
        from_key=key(ns, from_type, from_name),
        edge_type=edge_type,
        to_key=key(ns, to_type, to_name),
        source_type=fields.pop("source_type", "curated"),
        **fields
    )


def steps(ns, *names):

    for name in names:
        node(ns, "process_step", name)


def chain(ns, *names):
    """process_step names linked by NEXT edges."""

    steps(ns, *names)

    for a, b in zip(names, names[1:]):
        edge(ns, "process_step", a, "next", "process_step", b)


def keys(result: dict) -> list[str]:

    return [item["node"]["node_key"] for item in result["neighbors"]]


# ============================================================
# SCHEMA
# ============================================================

def test_tables_indexes_and_constraints_exist():

    inspector = inspect(engine)

    assert {"context_nodes", "context_edges"} <= set(inspector.get_table_names())

    assert "ix_context_nodes_node_type" in {
        i["name"] for i in inspector.get_indexes("context_nodes")
    }
    assert {
        "ix_context_edges_from_node_id_edge_type",
        "ix_context_edges_to_node_id_edge_type",
    } <= {i["name"] for i in inspector.get_indexes("context_edges")}

    assert "uq_context_edges_from_type_to" in {
        c["name"] for c in inspector.get_unique_constraints("context_edges")
    }

    foreign_keys = inspector.get_foreign_keys("context_edges")

    assert {fk["referred_table"] for fk in foreign_keys} == {"context_nodes"}
    assert {fk["options"].get("ondelete") for fk in foreign_keys} == {"CASCADE"}


def raw_node(**overrides) -> ContextNode:

    values = {
        "node_key": f"process:{uuid.uuid4().hex}",
        "node_type": "process",
        "label": "p",
        "source_type": "curated",
    }
    values.update(overrides)

    return ContextNode(**values)


def test_database_constraints(ns):

    node(ns, "process_step", "a")
    node(ns, "process_step", "b")

    with session_scope() as session:
        ids = dict(session.execute(
            select(ContextNode.node_key, ContextNode.id).where(
                ContextNode.node_key.in_([key(ns, "process_step", "a"), key(ns, "process_step", "b")])
            )
        ).all())

    a, b = ids[key(ns, "process_step", "a")], ids[key(ns, "process_step", "b")]

    violations = [
        # duplicate node key
        lambda s: s.add(raw_node(node_key=key(ns, "process_step", "a"), node_type="process_step")),
        # invalid source type
        lambda s: s.add(raw_node(source_type="guess")),
        # self loop
        lambda s: s.add(ContextEdge(from_node_id=a, to_node_id=a, edge_type="next", source_type="curated")),
        # confidence out of range
        lambda s: s.add(ContextEdge(from_node_id=a, to_node_id=b, edge_type="next", source_type="curated", confidence=2)),
        # edge to a missing node
        lambda s: s.add(ContextEdge(from_node_id=a, to_node_id=10**12, edge_type="next", source_type="curated")),
    ]

    for violate in violations:
        with pytest.raises(IntegrityError):
            with session_scope() as session:
                violate(session)

    edge(ns, "process_step", "a", "next", "process_step", "b")

    # Duplicate (from, type, to)
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(ContextEdge(from_node_id=a, to_node_id=b, edge_type="next", source_type="curated"))


def test_deleting_a_node_deletes_its_edges(ns):

    chain(ns, "a", "b")

    with session_scope() as session:
        session.delete(session.scalar(
            select(ContextNode).where(ContextNode.node_key == key(ns, "process_step", "a"))
        ))

    with session_scope() as session:
        remaining = session.scalar(
            select(func.count()).select_from(ContextEdge).join(
                ContextNode, ContextEdge.to_node_id == ContextNode.id
            ).where(ContextNode.node_key == key(ns, "process_step", "b"))
        )

    assert remaining == 0


# ============================================================
# NODES
# ============================================================

def test_upsert_node_is_idempotent(ns):

    created = node(ns, "role", "finance", label="Finance", properties={"team": "AP"})

    assert created["change"] == "created"
    assert created == {
        "node_key": key(ns, "role", "finance"),
        "node_type": "role",
        "label": "Finance",
        "properties": {"team": "AP"},
        "source_type": "curated",
        "source_ref": None,
        "is_active": True,
        "change": "created",
    }

    def updated_at():
        with session_scope() as session:
            return session.scalar(
                select(ContextNode.updated_at).where(
                    ContextNode.node_key == key(ns, "role", "finance")
                )
            )

    before = updated_at()

    again = node(ns, "role", "finance", label="Finance", properties={"team": "AP"})

    assert again["change"] == "unchanged"
    assert updated_at() == before

    changed = node(ns, "role", "finance", label="Finance team", properties={"team": "AP"})

    assert changed["change"] == "updated"
    assert changed["label"] == "Finance team"
    assert updated_at() > before

    with session_scope() as session:
        assert session.scalar(
            select(func.count()).select_from(ContextNode).where(
                ContextNode.node_key == key(ns, "role", "finance")
            )
        ) == 1


def test_node_type_cannot_change(ns):

    from app.services.context_graph_service import save_node

    node(ns, "role", "x")

    # Through the public API the key prefix already pins the type.
    with pytest.raises(ValueError, match="must start with 'system:'"):
        upsert_node(node_key=key(ns, "role", "x"), node_type="system",
                    label="x", source_type="curated")

    # Second line of defence: the stored type is checked on save.
    with pytest.raises(ValueError, match="is a 'role'"):
        with session_scope() as session:
            save_node(session, key(ns, "role", "x"), NodeType.SYSTEM, {
                "label": "x", "source_type": "curated", "source_ref": None,
                "properties": None, "is_active": True,
            })

    assert get_node(key(ns, "role", "x"))["node_type"] == "role"


@pytest.mark.parametrize(
    "node_key, node_type",
    [
        ("employee:E102", "not_a_type"),         # unknown type
        ("role:E102", "employee"),               # prefix must match type
        ("employee", "employee"),                # no identifier
        ("employee:", "employee"),               # empty identifier
        ("employee:E 102", "employee"),          # space
        ("employee:E102;drop", "employee"),      # disallowed character
        ("employee:" + "x" * 200, "employee"),   # too long
    ],
)
def test_invalid_node_keys_and_types_are_rejected(node_key, node_type):

    with pytest.raises(ValueError):
        upsert_node(node_key=node_key, node_type=node_type, label="x", source_type="curated")


@pytest.mark.parametrize(
    "fields",
    [
        {"label": ""},
        {"label": "x" * 201},
        {"source_type": "guess"},
        {"source_ref": "x" * 301},
        {"properties": "not-a-dict"},
    ],
)
def test_invalid_node_fields_are_rejected(ns, fields):

    values = {"label": "x", "source_type": "curated"} | fields

    with pytest.raises(ValueError):
        upsert_node(node_key=key(ns, "role", "bad"), node_type="role", **values)


def test_node_properties_are_sanitized(ns):

    created = node(ns, "system", "erp", properties={"api_key": "k", "vendor": "SAP"})

    assert created["properties"] == {"api_key": "[redacted]", "vendor": "SAP"}


def test_get_node(ns):

    node(ns, "department", "finance", source_type="system_of_record",
         source_ref="employees:department:Finance")

    found = get_node(key(ns, "department", "finance"))

    assert found["source_type"] == "system_of_record"
    assert found["source_ref"] == "employees:department:Finance"
    assert "change" not in found

    assert get_node(key(ns, "department", "missing")) is None


def test_upsert_joins_caller_transaction(ns):

    with pytest.raises(RuntimeError):
        with session_scope() as session:
            upsert_node(
                node_key=key(ns, "role", "temp"), node_type="role",
                label="Temp", source_type="curated", session=session
            )
            raise RuntimeError("business change failed")

    assert get_node(key(ns, "role", "temp")) is None


# ============================================================
# EDGES
# ============================================================

def test_upsert_edge_is_idempotent(ns):

    node(ns, "policy_rule", "over_500")
    node(ns, "role", "manager")

    created = edge(ns, "policy_rule", "over_500", "requires_approval_from", "role", "manager",
                   properties={"threshold": 500})

    assert created == {
        "from_key": key(ns, "policy_rule", "over_500"),
        "edge_type": "requires_approval_from",
        "to_key": key(ns, "role", "manager"),
        "properties": {"threshold": 500},
        "source_type": "curated",
        "source_ref": None,
        "confidence": None,
        "change": "created",
    }

    again = edge(ns, "policy_rule", "over_500", "requires_approval_from", "role", "manager",
                 properties={"threshold": 500})

    assert again["change"] == "unchanged"

    updated = edge(ns, "policy_rule", "over_500", "requires_approval_from", "role", "manager",
                   properties={"threshold": 500}, confidence=0.9)

    assert updated["change"] == "updated"
    assert updated["confidence"] == 0.9

    with session_scope() as session:
        assert session.scalar(
            select(func.count()).select_from(ContextEdge).where(
                ContextEdge.edge_type == "requires_approval_from",
                ContextEdge.properties.is_not(None),
                ContextEdge.from_node_id == select(ContextNode.id).where(
                    ContextNode.node_key == key(ns, "policy_rule", "over_500")
                ).scalar_subquery()
            )
        ) == 1


def test_edge_requires_existing_nodes(ns):

    node(ns, "process_step", "a")

    with pytest.raises(LookupError, match="does not exist"):
        edge(ns, "process_step", "a", "next", "process_step", "missing")


@pytest.mark.parametrize(
    "from_type, edge_type, to_type",
    [
        ("employee", "next", "process_step"),          # wrong source type
        ("process_step", "member_of", "department"),   # wrong edge for pair
        ("role", "implements", "process_step"),        # wrong source for implements
    ],
)
def test_edge_type_rules_are_enforced(ns, from_type, edge_type, to_type):

    node(ns, from_type, "a")
    node(ns, to_type, "b")

    with pytest.raises(ValueError, match="cannot connect"):
        edge(ns, from_type, "a", edge_type, to_type, "b")


@pytest.mark.parametrize(
    "fields",
    [
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"source_type": "guess"},
        {"edge_type": "likes"},
    ],
)
def test_invalid_edge_fields_are_rejected(ns, fields):

    steps(ns, "a", "b")

    values = {
        "from_key": key(ns, "process_step", "a"),
        "edge_type": "next",
        "to_key": key(ns, "process_step", "b"),
        "source_type": "curated",
    } | fields

    with pytest.raises(ValueError):
        upsert_edge(**values)


def test_self_loop_is_rejected(ns):

    node(ns, "process_step", "a")

    with pytest.raises(ValueError, match="itself"):
        edge(ns, "process_step", "a", "next", "process_step", "a")


def test_every_edge_type_has_rules():

    assert set(EDGE_RULES) == set(EdgeType)
    assert all(EDGE_RULES[edge_type] for edge_type in EdgeType)


# ============================================================
# TRAVERSAL
# ============================================================

def test_neighbors_depth_and_direction(ns):

    chain(ns, "s1", "s2", "s3", "s4", "s5")

    start = key(ns, "process_step", "s1")

    assert keys(neighbors(start)) == [key(ns, "process_step", "s2")]

    result = neighbors(start, depth=3)

    assert [(i["depth"], i["node"]["node_key"]) for i in result["neighbors"]] == [
        (1, key(ns, "process_step", "s2")),
        (2, key(ns, "process_step", "s3")),
        (3, key(ns, "process_step", "s4")),
    ]
    assert result["truncated"] is False

    assert result["neighbors"][1]["via"]["from_key"] == key(ns, "process_step", "s2")
    assert result["neighbors"][1]["via"]["to_key"] == key(ns, "process_step", "s3")

    middle = key(ns, "process_step", "s3")

    assert keys(neighbors(middle, direction="in")) == [key(ns, "process_step", "s2")]
    assert sorted(keys(neighbors(middle, direction="both"))) == sorted([
        key(ns, "process_step", "s2"), key(ns, "process_step", "s4")
    ])


def test_depth_is_limited(ns):

    node(ns, "process_step", "a")

    for depth in (0, MAX_DEPTH + 1):
        with pytest.raises(ValueError, match="depth"):
            neighbors(key(ns, "process_step", "a"), depth=depth)


def test_cycles_terminate_and_each_node_appears_once(ns):

    chain(ns, "a", "b", "c")
    edge(ns, "process_step", "c", "next", "process_step", "a")

    result = neighbors(key(ns, "process_step", "a"), depth=MAX_DEPTH, direction="both")

    assert sorted(keys(result)) == sorted([
        key(ns, "process_step", "b"), key(ns, "process_step", "c")
    ])
    assert {i["depth"] for i in result["neighbors"]} == {1}


def test_edge_type_filter(ns):

    node(ns, "process", "expense")
    steps(ns, "receipt")
    node(ns, "role", "finance")
    node(ns, "system", "accounting")
    edge(ns, "process", "expense", "has_step", "process_step", "receipt", properties={"order": 3})
    edge(ns, "process_step", "receipt", "performed_by", "role", "finance")
    edge(ns, "process_step", "receipt", "uses_system", "system", "accounting")

    step = key(ns, "process_step", "receipt")

    assert sorted(keys(neighbors(step))) == sorted([
        key(ns, "role", "finance"), key(ns, "system", "accounting")
    ])
    assert keys(neighbors(step, edge_types=["performed_by"])) == [key(ns, "role", "finance")]
    assert keys(neighbors(step, direction="in", edge_types=[EdgeType.HAS_STEP])) == [
        key(ns, "process", "expense")
    ]

    with pytest.raises(ValueError):
        neighbors(step, edge_types=["likes"])


def test_inactive_nodes_are_skipped_and_not_traversed(ns):

    chain(ns, "a", "b", "c")
    node(ns, "process_step", "b", is_active=False)

    start = key(ns, "process_step", "a")

    assert keys(neighbors(start, depth=3)) == []
    assert keys(neighbors(start, depth=3, include_inactive=True)) == [
        key(ns, "process_step", "b"), key(ns, "process_step", "c")
    ]


def test_limit_truncates(ns):

    node(ns, "process", "big")

    for i in range(5):
        node(ns, "process_step", f"s{i}")
        edge(ns, "process", "big", "has_step", "process_step", f"s{i}")

    result = neighbors(key(ns, "process", "big"), limit=3)

    assert len(result["neighbors"]) == 3
    assert result["truncated"] is True

    with pytest.raises(ValueError, match="limit"):
        neighbors(key(ns, "process", "big"), limit=0)


def test_neighbors_of_unknown_node_raise(ns):

    with pytest.raises(LookupError):
        neighbors(key(ns, "process", "missing"))

    with pytest.raises(ValueError, match="direction"):
        node(ns, "process", "p")
        neighbors(key(ns, "process", "p"), direction="sideways")
