"""
Connector HTTP behaviour: retries, back-off, safe errors, OAuth token
handling and configuration. All HTTP is mocked.
"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.services.connectors import ConnectorConfigError
from app.services.connectors.base import ConnectorAuthError, ConnectorRequestError
from app.services.connectors.http import ClientCredentialsAuth, ConnectorHttpClient
from app.services.connectors.settings import EntraSettings, HttpSettings
from connector_fakes import (
    CLIENT_ID, CLIENT_SECRET, TENANT_ID, FakeGraph,
)


URL = "https://api.example.test/items?token=URL-SECRET"


def client(handler, *, retries=3, **kwargs):

    sleeps = []

    http = ConnectorHttpClient(
        HttpSettings(
            max_retries=retries, backoff_seconds=0.5, max_backoff_seconds=30
        ),
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        jitter=lambda: 1.0,
        **kwargs,
    )

    return http, sleeps


def scripted(*items):
    """Handler returning each item (response or exception) in turn."""

    queue = list(items)
    calls = []

    def handler(request):

        calls.append(request)
        item = queue.pop(0)

        if isinstance(item, Exception):
            raise item

        return item

    handler.calls = calls

    return handler


# ============================================================
# RETRIES
# ============================================================

def test_success_needs_no_retry():

    handler = scripted(httpx.Response(200, json={"ok": True}))
    http, sleeps = client(handler)

    assert http.send("GET", URL).json() == {"ok": True}
    assert len(handler.calls) == 1 and sleeps == []


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried_with_exponential_backoff(status):

    handler = scripted(
        httpx.Response(status), httpx.Response(status), httpx.Response(200)
    )
    http, sleeps = client(handler)

    assert http.send("GET", URL).status_code == 200
    assert len(handler.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_backoff_is_capped_and_jittered():

    handler = scripted(*[httpx.Response(503)] * 4)
    sleeps = []

    http = ConnectorHttpClient(
        HttpSettings(max_retries=3, backoff_seconds=10, max_backoff_seconds=15),
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        jitter=lambda: 0.0,
    )

    with pytest.raises(ConnectorRequestError):
        http.send("GET", URL)

    # 10 * 2**n * 0.5 (minimum jitter), never above the cap.
    assert sleeps == [5.0, 10.0, 15.0]


def test_retry_after_seconds_is_honoured_but_capped():

    handler = scripted(
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "9999"}),
        httpx.Response(200),
    )
    http, sleeps = client(handler)

    http.send("GET", URL)

    assert sleeps == [7.0, 30.0]


def test_retry_after_http_date():

    soon = datetime.now(timezone.utc) + timedelta(seconds=20)
    past = datetime.now(timezone.utc) - timedelta(seconds=20)

    handler = scripted(
        httpx.Response(503, headers={"Retry-After": format_datetime(soon, usegmt=True)}),
        httpx.Response(503, headers={"Retry-After": format_datetime(past, usegmt=True)}),
        httpx.Response(503, headers={"Retry-After": "not a date"}),
        httpx.Response(200),
    )
    http, sleeps = client(handler)

    http.send("GET", URL)

    assert 10 < sleeps[0] <= 20
    assert sleeps[1] == 0.0
    assert sleeps[2] == 2.0   # unparseable: normal back-off (0.5 * 2**2)


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("bad")],
)
def test_network_errors_are_retried(error):

    handler = scripted(error, httpx.Response(200))
    http, sleeps = client(handler)

    assert http.send("GET", URL).status_code == 200
    assert len(handler.calls) == 2


def test_gives_up_after_max_retries_without_leaking_details():

    handler = scripted(
        *[httpx.ConnectError(f"cannot reach {URL}")] * 3
    )
    http, sleeps = client(handler, retries=2)

    with pytest.raises(ConnectorRequestError) as caught:
        http.send("GET", URL)

    message = str(caught.value)

    assert message == "Request failed after 3 attempt(s): ConnectError."
    assert "URL-SECRET" not in message and "example.test" not in message
    assert len(handler.calls) == 3


def test_http_error_message_names_only_the_status():

    handler = scripted(*[httpx.Response(503, text="body with SECRET-BODY")] * 2)
    http, _ = client(handler, retries=1)

    with pytest.raises(ConnectorRequestError, match=r"HTTP 503\.$") as caught:
        http.send("GET", URL)

    assert "SECRET-BODY" not in str(caught.value)


def test_zero_retries_means_one_attempt():

    handler = scripted(httpx.Response(500))
    http, sleeps = client(handler, retries=0)

    with pytest.raises(ConnectorRequestError, match="1 attempt"):
        http.send("GET", URL)

    assert sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_client_errors_are_returned_not_retried(status):

    handler = scripted(httpx.Response(status), httpx.Response(200))
    http, sleeps = client(handler)

    assert http.send("GET", URL).status_code == status
    assert len(handler.calls) == 1 and sleeps == []


def test_redirects_are_not_followed():
    """The Authorization header must never be forwarded elsewhere."""

    handler = scripted(
        httpx.Response(302, headers={"Location": "https://evil.example.test/"}),
        httpx.Response(200),
    )
    http, _ = client(handler)

    response = http.send("GET", URL, headers={"Authorization": "Bearer x"})

    assert response.status_code == 302
    assert len(handler.calls) == 1


# ============================================================
# OAUTH CLIENT CREDENTIALS
# ============================================================

TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"


def make_auth(graph: FakeGraph, clock=None, secret=CLIENT_SECRET, retries=2):

    http = ConnectorHttpClient(
        HttpSettings(max_retries=retries),
        transport=httpx.MockTransport(graph),
        sleep=lambda s: None,
        jitter=lambda: 1.0,
    )

    kwargs = {"clock": clock} if clock else {}

    return ClientCredentialsAuth(
        http, token_url=TOKEN_URL, client_id=CLIENT_ID, client_secret=secret,
        scope="https://graph.microsoft.com/.default", **kwargs
    )


def test_token_is_cached_until_it_nearly_expires():

    graph = FakeGraph([[]])
    now = [1000.0]
    auth = make_auth(graph, clock=lambda: now[0])

    first = auth.token()

    now[0] += 3000          # well inside the 3600s lifetime
    assert auth.token() == first
    assert graph.token_calls == 1

    now[0] += 560           # within the 60s expiry margin
    assert auth.token() != first
    assert graph.token_calls == 2


def test_invalidate_forces_a_new_token():

    graph = FakeGraph([[]])
    auth = make_auth(graph)

    first = auth.token()
    auth.invalidate()

    assert auth.token() != first


def test_token_request_uses_client_credentials_form():

    graph = FakeGraph([[]])

    make_auth(graph).token()

    [request] = graph.requests

    assert request.method == "POST"
    assert str(request.url) == TOKEN_URL
    assert b"grant_type=client_credentials" in request.content
    assert b"scope=https%3A%2F%2Fgraph.microsoft.com%2F.default" in request.content


def test_rejected_credentials_raise_auth_error_without_leaking():

    graph = FakeGraph([[]])
    auth = make_auth(graph, secret="the-wrong-secret")

    with pytest.raises(ConnectorAuthError) as caught:
        auth.token()

    message = str(caught.value)

    assert "HTTP 401, invalid_client" in message
    assert "the-wrong-secret" not in message
    assert "AADSTS" not in message          # no provider error text
    assert graph.token_calls == 0           # 401 is not retried
    assert len(graph.requests) == 1


def test_token_endpoint_outage_is_retried_then_succeeds():

    graph = FakeGraph([[]])
    graph.token_failures.extend([httpx.Response(503), httpx.ConnectError("x")])

    assert make_auth(graph).token().startswith("fake-access-token")
    assert len(graph.requests) == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"token_type": "Bearer"}),
        httpx.Response(200, json={"access_token": ""}),
        httpx.Response(200, json={"access_token": 123}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, text="not json"),
    ],
)
def test_malformed_token_responses_are_rejected(response):

    graph = FakeGraph([[]])
    graph.token_failures.append(response)

    with pytest.raises(ConnectorAuthError, match="access token"):
        make_auth(graph).token()


# ============================================================
# CONFIGURATION
# ============================================================

FULL_ENV = {
    "ENTRA_TENANT_ID": TENANT_ID,
    "ENTRA_CLIENT_ID": CLIENT_ID,
    "ENTRA_CLIENT_SECRET": CLIENT_SECRET,
}


def test_entra_settings_from_env():

    settings = EntraSettings.from_env(FULL_ENV)

    assert (settings.tenant_id, settings.client_id) == (TENANT_ID, CLIENT_ID)
    assert settings.authority_url == "https://login.microsoftonline.com"
    assert settings.graph_url == "https://graph.microsoft.com/v1.0"


def test_missing_settings_name_variables_not_values():

    with pytest.raises(ConnectorConfigError) as caught:
        EntraSettings.from_env({"ENTRA_TENANT_ID": TENANT_ID})

    assert str(caught.value) == (
        "Entra connector is not configured; missing: "
        "ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET"
    )


def test_blank_values_count_as_missing():

    with pytest.raises(ConnectorConfigError, match="ENTRA_CLIENT_SECRET"):
        EntraSettings.from_env(FULL_ENV | {"ENTRA_CLIENT_SECRET": "   "})


def test_secret_is_not_in_repr_or_str():

    settings = EntraSettings.from_env(FULL_ENV)

    assert CLIENT_SECRET not in repr(settings)
    assert CLIENT_SECRET not in str(settings)


@pytest.mark.parametrize(
    "name, value",
    [
        ("ENTRA_TENANT_ID", "../evil"),
        ("ENTRA_TENANT_ID", "tenant/with/slash"),
        ("ENTRA_CLIENT_ID", "id with spaces"),
        ("ENTRA_GRAPH_URL", "http://graph.example.test/v1.0"),
        ("ENTRA_GRAPH_URL", "ftp://graph.example.test"),
        ("ENTRA_AUTHORITY_URL", "https://user:pass@login.example.test"),
        ("ENTRA_AUTHORITY_URL", "https://login.example.test/?x=1"),
        ("ENTRA_GRAPH_URL", "not a url"),
    ],
)
def test_invalid_settings_are_rejected(name, value):

    with pytest.raises(ConnectorConfigError) as caught:
        EntraSettings.from_env(FULL_ENV | {name: value})

    assert CLIENT_SECRET not in str(caught.value)


def test_local_http_is_allowed_for_mock_servers_and_slash_is_trimmed():

    settings = EntraSettings.from_env(
        FULL_ENV | {"ENTRA_GRAPH_URL": "http://localhost:8080/v1.0/"}
    )

    assert settings.graph_url == "http://localhost:8080/v1.0"


def test_http_settings_defaults_and_bounds():

    assert HttpSettings.from_env({}) == HttpSettings()

    custom = HttpSettings.from_env({
        "CONNECTOR_TIMEOUT_SECONDS": "5",
        "CONNECTOR_MAX_RETRIES": "0",
        "CONNECTOR_MAX_PAGES": "7",
    })

    assert (custom.timeout_seconds, custom.max_retries, custom.max_pages) == (5.0, 0, 7)

    for name, value in (
        ("CONNECTOR_TIMEOUT_SECONDS", "0"),
        ("CONNECTOR_TIMEOUT_SECONDS", "abc"),
        ("CONNECTOR_MAX_RETRIES", "11"),
        ("CONNECTOR_MAX_RETRIES", "-1"),
        ("CONNECTOR_MAX_PAGES", "0"),
    ):
        with pytest.raises(ConnectorConfigError, match=name):
            HttpSettings.from_env({name: value})
