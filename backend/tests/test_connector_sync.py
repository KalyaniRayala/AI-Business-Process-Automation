"""
Connector sync into the context graph (PostgreSQL / SQLite test
database): ownership, idempotency, stale data, atomicity, audit.
External APIs are mocked.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.services import audit_service
from app.services.audit_service import list_events
from app.services.connectors import (
    Connector,
    ConnectorBatch,
    ConnectorDataError,
    ConnectorRequestError,
    EdgeSpec,
    NodeSpec,
    sync_connector,
)
from app.services.connectors import sync as sync_module
from app.services.context_graph_service import (
    get_employee_context,
    get_node,
    neighbors,
    upsert_node,
)
from app.services.db import session_scope
from app.services.db_models import AuditEvent, ContextEdge, ContextNode
from app.services.tool_service import get_business_context
from connector_fakes import (
    ALICE, BOB, CAROL, CLIENT_SECRET, TOKEN, make_connector, make_user, purge,
)


@pytest.fixture(autouse=True)
def clean_graph():

    purge()
    yield
    purge()


def person(user_id: str) -> str:

    return f"person:entra/{user_id}"


def run_sync(users, **kwargs) -> dict:

    connector, _ = make_connector([users])

    return sync_connector(connector, **kwargs)


def graph_size() -> tuple[int, int]:

    with session_scope() as session:
        return (
            session.scalar(select(func.count()).select_from(ContextNode)),
            session.scalar(select(func.count()).select_from(ContextEdge)),
        )


def latest_event_id() -> int:

    with session_scope() as session:
        return session.scalar(select(func.max(AuditEvent.id))) or 0


def connector_events(after_id: int, name: str = "entra") -> list[dict]:

    return [
        event for event in list_events(entity_type="connector", entity_id=name,
                                       after_id=after_id, limit=500)
    ]


def out_keys(key, edge_type, **kwargs) -> list[str]:

    return sorted(
        item["node"]["node_key"]
        for item in neighbors(key, edge_types=[edge_type], **kwargs)["neighbors"]
    )


DIRECTORY = [
    make_user(ALICE, "Alice Ng", department="Finance", title="Finance Director",
              employee_id="E102"),
    make_user(BOB, "Bob Rao", department="Finance", manager=ALICE,
              employee_id="E104"),
    make_user(CAROL, "Carol Diaz", department="Legal", manager=ALICE),
]


# ============================================================
# MAPPING INTO THE GRAPH
# ============================================================

def test_first_sync_creates_connector_owned_nodes_and_edges():

    summary = run_sync(DIRECTORY)

    assert summary == {
        "connector": "entra",
        # 3 people + the new Legal department (Finance already exists)
        "nodes_created": 4, "nodes_updated": 0, "nodes_unchanged": 0,
        "nodes_deactivated": 0, "nodes_left_unchanged": 1,
        # 2 reports_to + 3 member_of + 1 manager role + 2 identified_as
        "edges_created": 8, "edges_updated": 0, "edges_unchanged": 0,
        "edges_removed": 0, "edges_skipped": 0,
        "records_skipped": 0,
    }

    alice = get_node(person(ALICE))

    assert alice["node_type"] == "person"
    assert alice["label"] == "Alice Ng"
    assert alice["source_type"] == "connector"
    assert alice["source_ref"] == f"entra:users/{ALICE}"
    assert alice["properties"]["job_title"] == "Finance Director"
    assert alice["is_active"] is True

    assert get_node("department:legal")["source_type"] == "connector"


def test_sync_is_idempotent():

    run_sync(DIRECTORY)

    summary = run_sync(DIRECTORY)

    assert summary["nodes_created"] == summary["nodes_updated"] == 0
    assert summary["edges_created"] == summary["edges_updated"] == 0
    assert summary["nodes_unchanged"] == 4
    assert summary["edges_unchanged"] == 8
    assert summary["edges_removed"] == summary["nodes_deactivated"] == 0


def test_reporting_lines_departments_and_manager_role_are_in_the_graph():

    run_sync(DIRECTORY)

    assert out_keys(person(BOB), "reports_to") == [person(ALICE)]
    assert out_keys(person(CAROL), "reports_to") == [person(ALICE)]
    assert out_keys(person(ALICE), "reports_to") == []

    assert out_keys(person(BOB), "member_of") == ["department:finance"]
    assert out_keys(person(CAROL), "member_of") == ["department:legal"]

    assert out_keys(person(ALICE), "has_role") == ["role:manager"]
    assert out_keys(person(BOB), "has_role") == []


def test_links_to_existing_nodes_without_changing_them():
    """department:finance and role:manager belong to the employees
    projection / curated seed; the connector only links to them."""

    before_department = get_node("department:finance")
    before_role = get_node("role:manager")

    run_sync(DIRECTORY)

    assert get_node("department:finance") == before_department
    assert get_node("department:finance")["source_type"] == "system_of_record"
    assert get_node("role:manager") == before_role

    incoming = neighbors("department:finance", direction="in",
                         edge_types=["member_of"])["neighbors"]

    sources = {item["node"]["node_key"]: item["via"]["source_type"] for item in incoming}

    assert sources[person(ALICE)] == "connector"
    assert sources["employee:E102"] == "system_of_record"   # existing, untouched


def test_hr_record_is_linked_to_the_directory_identity():

    run_sync(DIRECTORY)

    assert out_keys("employee:E102", "identified_as") == [person(ALICE)]
    assert out_keys("employee:E104", "identified_as") == [person(BOB)]

    [item] = neighbors(
        "employee:E102", edge_types=["identified_as"]
    )["neighbors"]

    assert item["via"]["source_type"] == "connector"
    assert item["via"]["source_ref"] == f"entra:users/{ALICE}"


def test_unknown_employee_ids_skip_only_that_edge():

    users = [make_user(ALICE, "Alice", employee_id="E9999")]

    summary = run_sync(users)

    assert summary["edges_skipped"] == 1
    assert summary["nodes_created"] == 1
    assert get_node(person(ALICE)) is not None


def test_manager_chain_is_visible_through_the_context_api():

    run_sync(DIRECTORY)

    with TestClient(app) as client:

        response = client.get(
            "/context/nodes/employee:E104",
            params={"depth": 3, "direction": "out",
                    "edge_type": ["identified_as", "reports_to", "has_role"]},
        )

        assert response.status_code == 200

        keys = {n["node"]["node_key"] for n in response.json()["neighbors"]}

        # HR record -> directory identity -> manager -> manager role
        assert {person(BOB), person(ALICE), "role:manager"} <= keys

        # A key with a slash resolves too.
        assert client.get(f"/context/nodes/{person(BOB)}").status_code == 200


# ============================================================
# EXISTING BEHAVIOUR IS UNCHANGED
# ============================================================

def test_existing_employee_context_and_tools_are_unaffected():

    before_context = get_employee_context("E102")
    before_tool = get_business_context("E102", 750)

    run_sync(DIRECTORY)

    assert get_employee_context("E102") == before_context
    assert get_business_context("E102", 750) == before_tool


def test_startup_projection_does_not_touch_connector_data():
    """sync_context_graph (run at every app start) deactivates stale
    system_of_record nodes; connector nodes are not among them."""

    from app.services.context_graph_seed import sync_context_graph

    run_sync(DIRECTORY)

    before = graph_size()

    sync_context_graph()

    assert graph_size() == before
    assert get_node(person(ALICE))["is_active"] is True
    assert out_keys("employee:E102", "identified_as") == [person(ALICE)]


# ============================================================
# CHANGES IN THE EXTERNAL SYSTEM
# ============================================================

def test_changed_reporting_line_and_department_replace_old_edges():

    run_sync(DIRECTORY)

    changed = [
        DIRECTORY[0],
        make_user(BOB, "Bob Rao", department="Legal", manager=CAROL,
                  employee_id="E104"),
        DIRECTORY[2],
    ]

    summary = run_sync(changed)

    assert out_keys(person(BOB), "reports_to") == [person(CAROL)]
    assert out_keys(person(BOB), "member_of") == ["department:legal"]
    assert summary["edges_removed"] == 2          # old manager, old department

    # Carol now manages someone, so she holds the manager role.
    assert out_keys(person(CAROL), "has_role") == ["role:manager"]


def test_manager_role_is_removed_when_nobody_reports_to_them_any_more():

    run_sync(DIRECTORY)

    run_sync([make_user(ALICE, "Alice Ng", department="Finance"),
              make_user(BOB, "Bob Rao", department="Finance")])

    assert out_keys(person(ALICE), "has_role") == []


def test_disabled_account_becomes_inactive_and_can_return():

    run_sync(DIRECTORY)

    disabled = [DIRECTORY[0], DIRECTORY[1], make_user(
        CAROL, "Carol Diaz", department="Legal", manager=ALICE, enabled=False)]

    summary = run_sync(disabled)

    assert get_node(person(CAROL))["is_active"] is False
    assert get_node(person(CAROL))["properties"]["account_enabled"] is False
    assert summary["nodes_updated"] == 1

    assert person(CAROL) not in out_keys(person(ALICE), "reports_to", direction="in")

    run_sync(DIRECTORY)

    assert get_node(person(CAROL))["is_active"] is True


def test_users_that_disappear_are_deactivated_not_deleted():

    run_sync(DIRECTORY)

    summary = run_sync(DIRECTORY[:2])         # Carol and Legal are gone

    assert summary["nodes_deactivated"] == 2
    assert get_node(person(CAROL))["is_active"] is False
    assert get_node("department:legal")["is_active"] is False
    assert get_node(person(ALICE))["is_active"] is True

    # Their edges are removed; inactive nodes are hidden from traversal.
    assert person(CAROL) not in out_keys(person(ALICE), "reports_to",
                                         direction="in", include_inactive=True)


def test_nodes_owned_by_someone_else_are_not_overwritten():

    upsert_node(node_key=person(ALICE), node_type="person",
                label="Curated Alice", source_type="curated",
                properties={"note": "hand written"})

    summary = run_sync(DIRECTORY[:1])

    assert summary["nodes_left_unchanged"] == 2      # her node + Finance
    assert summary["nodes_created"] == 0

    node = get_node(person(ALICE))

    assert (node["label"], node["source_type"]) == ("Curated Alice", "curated")
    assert node["properties"] == {"note": "hand written"}


# ============================================================
# SAFETY GUARDS AND ATOMICITY
# ============================================================

def test_empty_result_is_refused_when_the_connector_owns_data():

    run_sync(DIRECTORY)

    before = graph_size()

    with pytest.raises(ConnectorDataError, match="returned no data but owns 4"):
        run_sync([])

    assert graph_size() == before
    assert get_node(person(ALICE))["is_active"] is True


def test_empty_result_can_be_allowed_explicitly():

    run_sync(DIRECTORY)

    summary = run_sync([], allow_empty=True)

    assert summary["nodes_deactivated"] == 4
    assert get_node(person(ALICE))["is_active"] is False


def test_empty_result_is_fine_when_nothing_is_owned():

    summary = run_sync([])

    assert summary["nodes_created"] == summary["nodes_deactivated"] == 0


def test_failed_fetch_changes_nothing():

    run_sync(DIRECTORY)

    before = graph_size()

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.extend([httpx.Response(503)] * 3)

    with pytest.raises(ConnectorRequestError):
        sync_connector(connector)

    assert graph_size() == before
    assert get_node(person(ALICE))["is_active"] is True
    assert get_node(person(CAROL))["is_active"] is True


def test_sync_is_atomic(monkeypatch):

    before = graph_size()

    real = sync_module.upsert_edge
    calls = []

    def fail_on_third_edge(**kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            raise RuntimeError("SQL with parameters: secret-sql-detail")
        return real(**kwargs)

    monkeypatch.setattr(sync_module, "upsert_edge", fail_on_third_edge)

    after_id = latest_event_id()

    with pytest.raises(RuntimeError):
        run_sync(DIRECTORY)

    # Nothing was applied, including the nodes written before the failure.
    assert graph_size() == before
    assert get_node(person(ALICE)) is None

    started, failed = connector_events(after_id)

    assert failed["event_type"] == "connector_sync_failed"
    assert failed["status"] == "failure"
    assert failed["error_type"] == "RuntimeError"
    # Unexpected errors are not copied into the audit trail.
    assert "secret-sql-detail" not in json.dumps(failed)
    assert failed["error_message"] == "Unexpected error; see the application log."


# ============================================================
# AUDIT TRAIL
# ============================================================

def test_sync_is_audited():

    after_id = latest_event_id()

    summary = run_sync(DIRECTORY)

    started, completed = connector_events(after_id)

    assert started["event_type"] == "connector_sync_started"
    assert (started["entity_type"], started["entity_id"]) == ("connector", "entra")
    assert started["status"] == "info"

    assert completed["event_type"] == "connector_sync_completed"
    assert completed["status"] == "success"
    assert completed["actor_type"] == "system"
    assert completed["details"] == summary


def test_failures_are_audited_with_safe_messages():

    after_id = latest_event_id()

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.append(httpx.Response(403))

    with pytest.raises(Exception, match="User.Read.All"):
        sync_connector(connector)

    started, failed = connector_events(after_id)

    assert failed["event_type"] == "connector_sync_failed"
    assert failed["error_type"] == "ConnectorAuthError"
    assert "User.Read.All" in failed["error_message"]


def test_audit_trail_never_contains_credentials():

    after_id = latest_event_id()

    run_sync(DIRECTORY)

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.extend([httpx.ConnectError(CLIENT_SECRET)] * 3)

    with pytest.raises(ConnectorRequestError):
        sync_connector(connector)

    text = json.dumps(connector_events(after_id))

    assert CLIENT_SECRET not in text
    assert TOKEN not in text


# ============================================================
# THE CONNECTOR INTERFACE IS GENERIC
# ============================================================

class StaticConnector(Connector):
    """A second, trivial connector: proves the interface and the
    ownership rules are not Entra specific."""

    name = "hr_demo"

    def __init__(self, batch: ConnectorBatch):
        self.batch = batch

    def collect(self) -> ConnectorBatch:
        return self.batch


def demo_batch(*, ref="hr_demo:people/1", key="person:hr_demo/1") -> ConnectorBatch:

    return ConnectorBatch(
        nodes=[NodeSpec(key, "person", "Demo Person", ref)],
        edges=[EdgeSpec(key, "has_role", "role:manager", ref)],
    )


def test_another_connector_syncs_and_owns_only_its_own_data():

    run_sync(DIRECTORY)

    summary = sync_connector(StaticConnector(demo_batch()))

    assert summary["connector"] == "hr_demo"
    assert (summary["nodes_created"], summary["edges_created"]) == (1, 1)

    # Entra's data is untouched, and vice versa.
    assert get_node(person(ALICE))["is_active"] is True

    run_sync(DIRECTORY[:1])

    assert get_node("person:hr_demo/1")["is_active"] is True

    sync_connector(StaticConnector(ConnectorBatch(nodes=[
        NodeSpec("person:hr_demo/2", "person", "Two", "hr_demo:people/2")
    ])))

    assert get_node("person:hr_demo/1")["is_active"] is False
    assert get_node(person(ALICE))["is_active"] is True


def test_a_connector_cannot_claim_data_outside_its_namespace():

    before = graph_size()

    with pytest.raises(ConnectorDataError, match="outside the 'hr_demo' connector"):
        sync_connector(StaticConnector(demo_batch(ref="entra:users/1")))

    with pytest.raises(ConnectorDataError, match="outside the 'hr_demo' connector"):
        sync_connector(StaticConnector(ConnectorBatch(
            nodes=demo_batch().nodes,
            edges=[EdgeSpec("person:hr_demo/1", "has_role", "role:manager", "entra:x")],
        )))

    assert graph_size() == before


def test_duplicate_nodes_in_a_batch_are_rejected():

    batch = demo_batch()
    batch.nodes.append(batch.nodes[0])

    with pytest.raises(ConnectorDataError, match="Duplicate node"):
        sync_connector(StaticConnector(batch))


def test_invalid_connector_names_are_rejected():

    for name in ("Bad Name", "", "a:b", "x" * 31, "../x"):

        connector = StaticConnector(ConnectorBatch())
        connector.name = name

        with pytest.raises(ValueError, match="Invalid connector name"):
            sync_connector(connector)


def test_edges_to_missing_nodes_are_skipped_not_fatal():

    batch = demo_batch()
    batch.edges.append(EdgeSpec(
        "person:hr_demo/1", "has_role", "role:does_not_exist", "hr_demo:people/1"
    ))

    summary = sync_connector(StaticConnector(batch))

    assert (summary["edges_created"], summary["edges_skipped"]) == (1, 1)
