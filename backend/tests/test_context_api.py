"""
CG3: GET /context/nodes/{node_key} and GET /context/processes/{key}.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.services.context_graph_seed import PROCESS, step_key
from app.services.context_graph_service import upsert_edge, upsert_node
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode


NODE_FIELDS = {
    "node_key", "node_type", "label", "properties",
    "source_type", "source_ref", "is_active",
}

EDGE_FIELDS = {
    "from_key", "edge_type", "to_key", "properties",
    "source_type", "source_ref", "confidence",
}


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


def get_node(client, key, **params):

    return client.get(f"/context/nodes/{key}", params=params)


def neighbor_keys(body) -> list[tuple[int, str]]:

    return [(n["depth"], n["node"]["node_key"]) for n in body["neighbors"]]


def graph_size() -> tuple[int, int]:

    with session_scope() as session:
        return (
            session.scalar(select(func.count()).select_from(ContextNode)),
            session.scalar(select(func.count()).select_from(ContextEdge)),
        )


# ============================================================
# NODE LOOKUP
# ============================================================

def test_existing_node_with_relationships(client):

    response = get_node(client, "employee:E102")

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {"node", "depth", "direction", "count", "truncated", "neighbors"}
    assert body["node"] == {
        "node_key": "employee:E102",
        "node_type": "employee",
        "label": "E102",
        "properties": None,
        "source_type": "system_of_record",
        "source_ref": "employees:E102",
        "is_active": True,
    }
    assert (body["depth"], body["direction"], body["truncated"]) == (1, "both", False)
    assert body["count"] == len(body["neighbors"])

    assert sorted(neighbor_keys(body)) == [
        (1, "department:finance"),
        (1, "person:priya"),
    ]


def test_response_schema(client):

    body = get_node(client, "role:manager", direction="in").json()

    assert set(body["node"]) == NODE_FIELDS

    for neighbor in body["neighbors"]:

        assert set(neighbor) == {"depth", "node", "via"}
        assert set(neighbor["node"]) == NODE_FIELDS
        assert set(neighbor["via"]) == EDGE_FIELDS


def test_relationship_traversal_depth_and_direction(client):

    body = get_node(client, "employee:E102", depth=2, direction="out").json()

    assert neighbor_keys(body) == [
        (1, "department:finance"),
        (1, "person:priya"),
        (2, "role:manager"),
    ]

    [role] = [n for n in body["neighbors"] if n["depth"] == 2]

    assert role["via"] == {
        "from_key": "person:priya",
        "edge_type": "has_role",
        "to_key": "role:manager",
        "properties": None,
        "source_type": "system_of_record",
        "source_ref": "employees.manager:Priya",
        "confidence": None,
    }

    # Who approves above $500: incoming requires_approval_from.
    approvers = get_node(
        client, "role:manager", direction="in", edge_type="requires_approval_from"
    ).json()

    assert neighbor_keys(approvers) == [(1, "policy_rule:expense_approval_over_500")]


def test_multiple_edge_type_filters(client):

    body = get_node(
        client, step_key(9), direction="out",
        edge_type=["performed_by", "uses_system"]
    ).json()

    assert sorted(k for _, k in neighbor_keys(body)) == [
        "role:finance", "system:accounting_system"
    ]


def test_node_keys_containing_slashes(client):

    response = get_node(client, "document:data/expense_process.txt", direction="in")

    assert response.status_code == 200
    assert response.json()["node"]["node_type"] == "document"
    assert response.json()["count"] > 0


def test_unknown_node_returns_404(client):

    for key in ("employee:E999", "nonsense", "document:missing/file.txt"):

        response = get_node(client, key)

        assert response.status_code == 404
        assert response.json() == {"detail": f"Context node {key} was not found."}


@pytest.mark.parametrize(
    "params",
    [
        {"depth": 0},
        {"depth": 4},
        {"direction": "sideways"},
        {"edge_type": "likes"},
        {"limit": 0},
        {"limit": 1001},
    ],
)
def test_invalid_parameters_are_rejected(client, params):

    assert get_node(client, "employee:E102", **params).status_code == 422


# ============================================================
# CYCLES, LIMITS, INACTIVE NODES
# ============================================================

@pytest.fixture
def cycle():
    """a -> b -> c -> a, plus an inactive d after c."""

    ns = f"t{uuid.uuid4().hex[:10]}"
    keys = {name: f"process_step:{ns}:{name}" for name in "abcd"}

    for name, key in keys.items():
        upsert_node(node_key=key, node_type="process_step", label=name,
                    source_type="curated", is_active=(name != "d"))

    for a, b in (("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")):
        upsert_edge(from_key=keys[a], edge_type="next", to_key=keys[b],
                    source_type="curated")

    return keys


def test_cycles_are_visited_once_at_max_depth(client, cycle):

    body = get_node(client, cycle["a"], depth=3, direction="both").json()

    keys = [k for _, k in neighbor_keys(body)]

    assert sorted(keys) == sorted([cycle["b"], cycle["c"]])
    assert len(keys) == len(set(keys))


def test_inactive_nodes_only_when_requested(client, cycle):

    body = get_node(client, cycle["a"], depth=3, direction="out").json()

    assert cycle["d"] not in [k for _, k in neighbor_keys(body)]

    body = get_node(client, cycle["a"], depth=3, direction="out",
                    include_inactive=True).json()

    assert (3, cycle["d"]) in neighbor_keys(body)


def test_limit_truncates(client):

    body = get_node(client, PROCESS, direction="out", limit=4).json()

    assert body["count"] == 4
    assert body["truncated"] is True


# ============================================================
# PROCESS
# ============================================================

def test_process_lookup(client):

    response = client.get("/context/processes/expense_reimbursement")

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {"process", "steps", "policy_rules", "evidence"}
    assert body["process"]["node_key"] == PROCESS
    assert body["process"]["label"] == "Expense Reimbursement"
    assert body["evidence"] == ["document:data/expense_process.txt#L1"]

    # The prefixed key works too.
    assert client.get(f"/context/processes/{PROCESS}").json() == body


def test_process_steps(client):

    steps = client.get("/context/processes/expense_reimbursement").json()["steps"]

    assert [s["order"] for s in steps] == list(range(1, 11))
    assert [s["step"]["node_key"] for s in steps] == [step_key(n) for n in range(1, 11)]

    for step in steps:

        assert set(step) == {
            "order", "step", "next_steps", "performed_by", "uses_systems",
            "governed_by", "implemented_by", "evidence",
        }
        assert step["evidence"] == [
            f"document:data/expense_process.txt#L{2 * step['order'] + 1}"
        ]
        assert step["next_steps"] == (
            [step_key(step["order"] + 1)] if step["order"] < 10 else []
        )

    by_order = {s["order"]: s for s in steps}

    assert [r["node_key"] for r in by_order[7]["performed_by"]] == ["role:manager"]
    assert by_order[7]["governed_by"] == ["policy_rule:expense_approval_over_500"]
    assert sorted(by_order[4]["governed_by"]) == [
        "policy_rule:expense_approval_over_500",
        "policy_rule:expense_approval_over_5000",
    ]
    assert [s["node_key"] for s in by_order[9]["uses_systems"]] == ["system:accounting_system"]
    assert by_order[10]["performed_by"] == []

    assert sorted(
        i["component"]["node_key"] for i in by_order[4]["implemented_by"]
    ) == [
        "agent_node:policy",
        "agent_node:rag",
        "tool:lookup_expense_policy",
        "tool:search_business_documents",
    ]
    assert by_order[8]["implemented_by"] == [{
        "component": by_order[8]["implemented_by"][0]["component"],
        "properties": {"coverage": "partial"},
    }]
    assert by_order[8]["implemented_by"][0]["component"]["node_key"] == "agent_node:approval"

    assert [o for o, s in by_order.items() if not s["implemented_by"]] == [1, 2, 3, 6, 10]


def test_process_policy_rules(client):

    rules = client.get("/context/processes/expense_reimbursement").json()["policy_rules"]

    by_key = {r["rule"]["node_key"]: r for r in rules}

    assert set(by_key) == {
        "policy_rule:expense_approval_over_500",
        "policy_rule:expense_approval_over_5000",
    }

    over_500 = by_key["policy_rule:expense_approval_over_500"]

    assert [(a["role"]["node_key"], a["threshold"]) for a in over_500["approvers"]] == [
        ("role:manager", 500)
    ]
    assert over_500["policies"] == ["policy:expense_reimbursement"]
    assert over_500["evidence"] == ["document:data/expense_process.txt#L15"]
    assert over_500["rule"]["properties"] == {
        "threshold": 500, "comparison": "greater_than", "currency": "USD"
    }

    assert [
        (a["role"]["node_key"], a["threshold"])
        for a in by_key["policy_rule:expense_approval_over_5000"]["approvers"]
    ] == [("role:finance", 5000)]


def test_unknown_process_returns_404(client):

    for key in ("payroll", "process:payroll"):

        response = client.get(f"/context/processes/{key}")

        assert response.status_code == 404
        assert response.json() == {"detail": "Process process:payroll was not found."}


# ============================================================
# READ-ONLY
# ============================================================

def test_reads_do_not_change_the_graph(client):

    before = graph_size()

    get_node(client, "employee:E102", depth=3, direction="both")
    client.get("/context/processes/expense_reimbursement")
    get_node(client, "employee:E999")

    assert graph_size() == before


def test_endpoints_are_documented(client):

    paths = client.get("/openapi.json").json()["paths"]

    assert paths["/context/nodes/{node_key}"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]["$ref"].endswith("NodeContextResponse")
    assert paths["/context/processes/{process_key}"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]["$ref"].endswith("ProcessContextResponse")
