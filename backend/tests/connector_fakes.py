"""
Scripted stand-in for the Microsoft Entra token endpoint and the
Microsoft Graph users API, for httpx.MockTransport. Nothing here
touches the network, and every credential is a fake value.
"""

import urllib.parse
from collections import deque

import httpx
from sqlalchemy import delete, or_, select

from app.services.connectors import EntraConnector
from app.services.connectors.settings import EntraSettings, HttpSettings
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode


TENANT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "22222222-2222-2222-2222-222222222222"
CLIENT_SECRET = "fake-client-secret-for-tests"
TOKEN = "fake-access-token"

GRAPH_URL = "https://graph.microsoft.com/v1.0"

# Distinct ids for readable tests.
ALICE = "aaaaaaaa-0000-0000-0000-000000000001"
BOB = "bbbbbbbb-0000-0000-0000-000000000002"
CAROL = "cccccccc-0000-0000-0000-000000000003"


def make_settings() -> EntraSettings:

    return EntraSettings(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )


def make_user(
    user_id: str,
    name: str,
    *,
    department: str | None = None,
    title: str | None = None,
    employee_id: str | None = None,
    manager: str | None = None,
    enabled: bool = True,
) -> dict:
    """A user as Microsoft Graph returns it."""

    user = {
        "id": user_id,
        "displayName": name,
        "department": department,
        "jobTitle": title,
        "employeeId": employee_id,
        "accountEnabled": enabled,
    }

    if manager is not None:
        user["manager"] = {"id": manager}

    return user


class FakeGraph:
    """
    Callable for httpx.MockTransport. `pages` is a list of user
    lists, served one per request with @odata.nextLink between them.
    """

    def __init__(self, pages: list[list[dict]]):

        self.pages = pages
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.valid_tokens: set[str] = set()

        # Responses (or exceptions) returned before normal handling.
        self.token_failures: deque = deque()
        self.graph_failures: deque = deque()

        self.next_link_base = GRAPH_URL

    # httpx.MockTransport handler
    def __call__(self, request: httpx.Request) -> httpx.Response:

        self.requests.append(request)

        if request.url.path.endswith("/oauth2/v2.0/token"):
            return self.token(request)

        return self.users(request)

    def token(self, request: httpx.Request) -> httpx.Response:

        if self.token_failures:
            return self.fail(self.token_failures.popleft())

        form = dict(urllib.parse.parse_qsl(request.content.decode()))

        if (
            form.get("grant_type") != "client_credentials"
            or form.get("client_id") != CLIENT_ID
            or form.get("client_secret") != CLIENT_SECRET
        ):

            return httpx.Response(401, json={
                "error": "invalid_client",
                "error_description": "AADSTS7000215: Invalid client secret.",
            })

        self.token_calls += 1

        token = f"{TOKEN}-{self.token_calls}"
        self.valid_tokens.add(token)

        return httpx.Response(200, json={
            "access_token": token,
            "expires_in": 3600,
            "token_type": "Bearer",
        })

    def users(self, request: httpx.Request) -> httpx.Response:

        if self.graph_failures:
            return self.fail(self.graph_failures.popleft())

        bearer = request.headers.get("authorization", "")

        if bearer.removeprefix("Bearer ") not in self.valid_tokens:

            return httpx.Response(
                401, json={"error": {"code": "InvalidAuthenticationToken"}}
            )

        skip = request.url.params.get("$skiptoken")
        index = int(skip.removeprefix("page")) if skip else 0

        body = {"value": self.pages[index]}

        if index + 1 < len(self.pages):

            body["@odata.nextLink"] = (
                f"{self.next_link_base}/users?$skiptoken=page{index + 1}"
            )

        return httpx.Response(200, json=body)

    @staticmethod
    def fail(item):

        if isinstance(item, Exception):
            raise item

        return item

    # Convenience for assertions.
    def graph_requests(self) -> list[httpx.Request]:

        return [r for r in self.requests if r.url.path.endswith("/users")]


def make_connector(
    pages: list[list[dict]], *, graph: FakeGraph | None = None,
    http_settings: HttpSettings | None = None,
) -> tuple[EntraConnector, FakeGraph]:
    """An EntraConnector wired to a FakeGraph, with no real sleeping."""

    graph = graph or FakeGraph(pages)

    connector = EntraConnector(
        make_settings(),
        http_settings or HttpSettings(max_retries=2),
        transport=httpx.MockTransport(graph),
        sleep=lambda seconds: None,
        jitter=lambda: 1.0,
    )

    return connector, graph


def purge() -> None:
    """Remove everything connectors (or these tests) added to the graph."""

    with session_scope() as session:

        ours = select(ContextNode.id).where(or_(
            ContextNode.source_type == "connector",
            ContextNode.node_key.like("person:entra/%"),
            ContextNode.node_key.like("person:hr_demo/%"),
        ))

        session.execute(delete(ContextEdge).where(or_(
            ContextEdge.source_type == "connector",
            ContextEdge.from_node_id.in_(ours),
            ContextEdge.to_node_id.in_(ours),
        )))

        session.execute(delete(ContextNode).where(ContextNode.id.in_(ours)))
