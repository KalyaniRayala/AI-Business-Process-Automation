"""
Microsoft Entra ID connector: directory data -> graph batch, and the
Graph API calls (authentication, pagination, failures). All HTTP is
mocked; no real credentials are used.
"""

import httpx
import pytest

from app.services.connectors import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorDataError,
    ConnectorRequestError,
    create_connector,
)
from app.services.connectors.entra import build_batch
from app.services.connectors.settings import HttpSettings
from connector_fakes import (
    ALICE, BOB, CAROL, CLIENT_SECRET, GRAPH_URL, TENANT_ID, TOKEN,
    FakeGraph, make_connector, make_user,
)


def node_keys(batch):

    return [n.node_key for n in batch.nodes]


def edge_set(batch):

    return {(e.from_key, e.edge_type, e.to_key) for e in batch.edges}


def nodes(batch):

    return {n.node_key: n for n in batch.nodes}


# ============================================================
# MAPPING
# ============================================================

DIRECTORY = [
    make_user(ALICE, "Alice Ng", department="Finance", title="Finance Director",
              employee_id="e102"),
    make_user(BOB, "Bob Rao", department="Finance", title="Analyst",
              manager=ALICE, employee_id="E104"),
    make_user(CAROL, "Carol Diaz", department="Engineering", manager=ALICE,
              enabled=False),
]


def test_users_departments_and_reporting_lines_are_mapped():

    batch = build_batch(DIRECTORY)

    assert node_keys(batch) == [
        f"person:entra/{ALICE}", f"person:entra/{BOB}", f"person:entra/{CAROL}",
        "department:finance", "department:engineering",
    ]

    assert edge_set(batch) == {
        (f"person:entra/{BOB}", "reports_to", f"person:entra/{ALICE}"),
        (f"person:entra/{CAROL}", "reports_to", f"person:entra/{ALICE}"),
        (f"person:entra/{ALICE}", "member_of", "department:finance"),
        (f"person:entra/{BOB}", "member_of", "department:finance"),
        (f"person:entra/{CAROL}", "member_of", "department:engineering"),
        # People who manage others hold the manager role.
        (f"person:entra/{ALICE}", "has_role", "role:manager"),
        # HR record <-> directory identity (ids are upper-cased).
        ("employee:E102", "identified_as", f"person:entra/{ALICE}"),
        ("employee:E104", "identified_as", f"person:entra/{BOB}"),
    }

    assert batch.skipped_records == 0


def test_node_details():

    mapped = nodes(build_batch(DIRECTORY))

    alice = mapped[f"person:entra/{ALICE}"]

    assert (alice.node_type, alice.label, alice.is_active) == (
        "person", "Alice Ng", True
    )
    assert alice.source_ref == f"entra:users/{ALICE}"
    assert alice.properties == {
        "job_title": "Finance Director",
        "department": "Finance",
        "employee_id": "e102",
        "account_enabled": True,
    }

    # Disabled accounts become inactive nodes.
    carol = mapped[f"person:entra/{CAROL}"]

    assert carol.is_active is False
    assert carol.properties["account_enabled"] is False

    department = mapped["department:finance"]

    assert (department.node_type, department.label, department.source_ref) == (
        "department", "Finance", "entra:departments/finance"
    )


def test_every_reference_stays_inside_the_connector_namespace():

    batch = build_batch(DIRECTORY)

    assert all(n.source_ref.startswith("entra:") for n in batch.nodes)
    assert all(e.source_ref.startswith("entra:") for e in batch.edges)


def test_only_minimal_attributes_are_read_or_stored():

    user = make_user(ALICE, "Alice Ng") | {
        "mail": "alice@example.test",
        "mobilePhone": "+1 555 0100",
        "userPrincipalName": "alice@example.test",
        "onPremisesSamAccountName": "alice",
    }

    [node] = build_batch([user]).nodes

    assert set(node.properties) <= {
        "job_title", "department", "employee_id", "account_enabled"
    }
    assert "alice@example.test" not in repr(node)
    assert "555" not in repr(node)


def test_malformed_records_are_skipped_and_counted():

    batch = build_batch([
        make_user(ALICE, "Alice Ng"),
        "not a user",
        {"displayName": "No id"},
        {"id": "has spaces!"},
        {"id": 123},
        {"id": "../../etc"},
        make_user(ALICE, "Duplicate Alice"),
    ])

    assert node_keys(batch) == [f"person:entra/{ALICE}"]
    assert nodes(batch)[f"person:entra/{ALICE}"].label == "Alice Ng"
    assert batch.skipped_records == 6


def test_unresolvable_and_self_managers_create_no_edge():

    batch = build_batch([
        make_user(ALICE, "Alice", manager="not-in-directory"),
        make_user(BOB, "Bob", manager=BOB),
    ])

    assert edge_set(batch) == set()


@pytest.mark.parametrize(
    "employee_id", ["bad id", "has/slash", "x" * 21, "", None, 42]
)
def test_invalid_employee_ids_are_not_linked(employee_id):

    user = make_user(ALICE, "Alice")
    user["employeeId"] = employee_id

    assert edge_set(build_batch([user])) == set()


def test_departments_without_a_usable_name_are_ignored():

    batch = build_batch([
        make_user(ALICE, "Alice", department="!!!"),
        make_user(BOB, "Bob", department="   "),
    ])

    assert node_keys(batch) == [f"person:entra/{ALICE}", f"person:entra/{BOB}"]
    assert edge_set(batch) == set()


def test_text_is_cleaned_and_bounded():

    batch = build_batch([
        make_user(ALICE, "  Alice \n  Ng  ", department="x" * 500),
        make_user(BOB, None),
    ])

    mapped = nodes(batch)

    assert mapped[f"person:entra/{ALICE}"].label == "Alice Ng"
    assert len(mapped[f"person:entra/{ALICE}"].properties["department"]) == 200
    assert mapped[f"person:entra/{BOB}"].label == BOB      # falls back to the id


def test_empty_directory_maps_to_an_empty_batch():

    batch = build_batch([])

    assert (batch.nodes, batch.edges, batch.skipped_records) == ([], [], 0)


# ============================================================
# GRAPH API
# ============================================================

def test_collect_authenticates_and_reads_users():

    connector, graph = make_connector([DIRECTORY])

    batch = connector.collect()

    assert len(batch.nodes) == 5

    token_request, users_request = graph.requests

    assert token_request.url.path == f"/{TENANT_ID}/oauth2/v2.0/token"
    assert users_request.headers["authorization"] == f"Bearer {TOKEN}-1"

    params = users_request.url.params

    assert str(users_request.url).startswith(f"{GRAPH_URL}/users?")
    assert params["$top"] == "100"
    assert params["$expand"] == "manager($select=id)"
    assert set(params["$select"].split(",")) == {
        "id", "displayName", "department", "jobTitle", "employeeId",
        "accountEnabled",
    }   # no mail, phone or other personal attributes requested


def test_pagination_follows_next_links():

    pages = [
        [make_user(ALICE, "Alice")],
        [make_user(BOB, "Bob", manager=ALICE)],
        [make_user(CAROL, "Carol")],
    ]

    connector, graph = make_connector(pages)

    batch = connector.collect()

    assert [k for k in node_keys(batch) if k.startswith("person:")] == [
        f"person:entra/{ALICE}", f"person:entra/{BOB}", f"person:entra/{CAROL}",
    ]
    assert len(graph.graph_requests()) == 3
    assert graph.token_calls == 1            # one token for all pages
    assert (f"person:entra/{BOB}", "reports_to", f"person:entra/{ALICE}") in edge_set(batch)


def test_expired_token_is_refreshed_once():

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.append(httpx.Response(401))

    batch = connector.collect()

    assert len(batch.nodes) == 5
    assert graph.token_calls == 2
    assert graph.graph_requests()[-1].headers["authorization"].endswith("-2")


def test_persistent_401_is_an_auth_error():

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.extend([httpx.Response(401), httpx.Response(401)])

    with pytest.raises(ConnectorAuthError, match="rejected the access token"):
        connector.collect()


def test_403_explains_the_missing_permission():

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.append(httpx.Response(
        403, json={"error": {"code": "Authorization_RequestDenied",
                             "message": "Insufficient privileges for tenant X"}}
    ))

    with pytest.raises(ConnectorAuthError) as caught:
        connector.collect()

    message = str(caught.value)

    assert "User.Read.All" in message
    assert "Authorization_RequestDenied" in message
    assert "tenant X" not in message          # provider text is not echoed
    assert len(graph.graph_requests()) == 1   # not retried


def test_wrong_client_secret_fails_before_any_graph_call():

    connector, graph = make_connector([DIRECTORY])
    graph.token_failures.append(httpx.Response(401, json={"error": "invalid_client"}))

    with pytest.raises(ConnectorAuthError, match="invalid_client"):
        connector.collect()

    assert graph.graph_requests() == []


def test_transient_failures_are_retried():

    pages = [[make_user(ALICE, "Alice")], [make_user(BOB, "Bob")]]

    connector, graph = make_connector(pages)

    # The token endpoint and the users API each fail transiently first.
    graph.token_failures.append(httpx.Response(503))
    graph.graph_failures.extend([
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.ConnectError("reset"),
    ])

    batch = connector.collect()

    assert len([k for k in node_keys(batch) if k.startswith("person:")]) == 2
    assert len(graph.graph_requests()) == 2 + 2     # 2 failed + 2 pages


def test_persistent_server_errors_fail_without_a_partial_batch():

    connector, graph = make_connector(
        [[make_user(ALICE, "Alice")], [make_user(BOB, "Bob")]],
        http_settings=HttpSettings(max_retries=1),
    )

    # Page 1 succeeds; page 2 keeps failing.
    original = graph.users
    served = []

    def flaky(request):
        if served:
            return httpx.Response(500)
        served.append(request)
        return original(request)

    graph.users = flaky

    with pytest.raises(ConnectorRequestError, match="HTTP 500"):
        connector.collect()


def test_other_client_errors_are_request_errors():

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.append(httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}}))

    with pytest.raises(ConnectorRequestError, match=r"HTTP 404: Request_ResourceNotFound"):
        connector.collect()


def test_next_link_to_another_host_is_refused_and_never_called():

    pages = [[make_user(ALICE, "Alice")], [make_user(BOB, "Bob")]]

    connector, graph = make_connector(pages)
    graph.next_link_base = "https://evil.example.test/v1.0"

    with pytest.raises(ConnectorDataError, match="unexpected host"):
        connector.collect()

    assert {r.url.host for r in graph.requests} == {
        "graph.microsoft.com", "login.microsoftonline.com"
    }


@pytest.mark.parametrize(
    "link",
    ["http://graph.microsoft.com/v1.0/users?x=1", "//evil.example.test/x", 42],
)
def test_other_unsafe_next_links_are_refused(link):

    connector, graph = make_connector([[make_user(ALICE, "Alice")]])

    original = graph.users

    def with_link(request):
        response = original(request)
        body = response.json()
        body["@odata.nextLink"] = link
        return httpx.Response(200, json=body)

    graph.users = with_link

    with pytest.raises(ConnectorDataError):
        connector.collect()


def test_too_many_pages_are_refused_instead_of_truncated():

    pages = [[make_user(f"id-{n}", f"U{n}")] for n in range(3)]

    connector, _ = make_connector(pages, http_settings=HttpSettings(max_pages=2))

    with pytest.raises(ConnectorDataError, match="more than 2 pages"):
        connector.collect()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"unexpected": []}),
        httpx.Response(200, json={"value": "not a list"}),
        httpx.Response(200, json=["a", "list"]),
    ],
)
def test_unusable_responses_are_data_errors(response):

    connector, graph = make_connector([DIRECTORY])
    graph.graph_failures.append(response)

    with pytest.raises(ConnectorDataError):
        connector.collect()


def test_errors_never_contain_credentials():

    failures = [
        [httpx.Response(401), httpx.Response(401)],
        [httpx.Response(403)],
        [httpx.Response(500)] * 3,
        [httpx.ConnectError(f"cannot reach with {CLIENT_SECRET}")] * 3,
        [httpx.Response(200, text=f"garbage {CLIENT_SECRET}")],
    ]

    for failure in failures:

        connector, graph = make_connector([DIRECTORY])
        graph.graph_failures.extend(failure)

        with pytest.raises(Exception) as caught:
            connector.collect()

        assert CLIENT_SECRET not in str(caught.value)
        assert TOKEN not in str(caught.value)


# ============================================================
# FACTORY
# ============================================================

ENV = {
    "ENTRA_TENANT_ID": TENANT_ID,
    "ENTRA_CLIENT_ID": "22222222-2222-2222-2222-222222222222",
    "ENTRA_CLIENT_SECRET": CLIENT_SECRET,
}


def test_create_connector_from_environment():

    connector = create_connector("entra", ENV | {"CONNECTOR_MAX_RETRIES": "1"})

    assert connector.name == "entra"
    assert connector.http_settings.max_retries == 1
    assert CLIENT_SECRET not in repr(connector.settings)


def test_create_connector_requires_configuration():

    with pytest.raises(ConnectorConfigError, match="ENTRA_CLIENT_SECRET"):
        create_connector("entra", {})


def test_unknown_connector():

    with pytest.raises(ConnectorConfigError, match="Unknown connector 'nope'"):
        create_connector("nope", ENV)
