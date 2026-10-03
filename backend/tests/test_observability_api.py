"""
The observability endpoints and the HTTP middleware.

These run against the real app, so they also prove the
instrumentation does not change how the API behaves.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.observability import metrics, spans
from app.observability.context import REQUEST_ID_HEADER
from app.observability.middleware import clean_request_id


@pytest.fixture
def client():

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_metrics():

    metrics.registry.clear()
    spans.reset()

    yield

    metrics.registry.clear()
    spans.reset()


# ============================================================
# CORRELATION HEADER
# ============================================================

def test_every_response_carries_a_request_id(client):

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


def test_two_requests_get_different_ids(client):

    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]

    assert first != second


def test_a_client_supplied_id_is_echoed_back(client):

    response = client.get("/health", headers={REQUEST_ID_HEADER: "my-trace-1"})

    assert response.headers[REQUEST_ID_HEADER] == "my-trace-1"


@pytest.mark.parametrize(
    "value",
    ["", "   ", "x" * 200, "has spaces", "inject\nheader", "<script>"],
)
def test_an_unreasonable_client_id_is_replaced(value):
    """The id goes into logs and a header, so it is not trusted."""

    cleaned = clean_request_id(value)

    assert cleaned != value
    assert cleaned.isalnum()


def test_a_sensible_client_id_is_kept():

    assert clean_request_id("trace-123_abc.1") == "trace-123_abc.1"


# ============================================================
# HEALTH
# ============================================================

def test_liveness_is_unchanged(client):

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_every_check(client):

    response = client.get("/health/ready")

    assert response.status_code == 200

    payload = response.json()

    names = [check["name"] for check in payload["checks"]]

    assert names == [
        "database",
        "vector_store",
        "model_provider",
        "workflow_state",
    ]

    assert payload["uptime_seconds"] >= 0


def test_readiness_passes_its_essential_checks(client):
    """The test database and store are real, so these must pass."""

    payload = client.get("/health/ready").json()

    essential = [check for check in payload["checks"] if check["essential"]]

    assert all(check["status"] == "ok" for check in essential), essential

    # SQLite keeps workflow state in memory, so the suite is either
    # fully ok (PostgreSQL) or degraded by that one check.
    assert payload["status"] in ("ok", "degraded")


def test_a_failing_essential_check_makes_the_service_unready(client, monkeypatch):

    from app.observability import health

    def broken():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(
        health,
        "CHECKS",
        (("database", broken, True),),
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "failed"


def test_a_failing_optional_check_is_only_degraded(client, monkeypatch):

    from app.observability import health

    def broken():
        raise RuntimeError("in memory")

    monkeypatch.setattr(
        health,
        "CHECKS",
        (("workflow_state", broken, False),),
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_readiness_does_not_call_cohere(client):
    """conftest replaces the clients with ones that raise."""

    assert client.get("/health/ready").status_code == 200


# ============================================================
# METRICS
# ============================================================

def test_metrics_are_served_in_the_prometheus_format(client):

    client.get("/health")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    body = response.text

    assert "# TYPE http_requests_total counter" in body
    assert 'http_requests_total{method="GET",route="/health",status="200"} 1' in body


def test_a_request_is_timed(client):

    client.get("/health")

    assert (
        metrics.http_duration.count(method="GET", route="/health") == 1
    )


def test_the_route_template_is_the_label_not_the_path(client):
    """Ids in a label would create a time series per workflow."""

    client.get("/workflows/does-not-exist")

    body = client.get("/metrics").text

    assert 'route="/workflows/{thread_id}"' in body
    assert "does-not-exist" not in body


def test_an_unmatched_path_does_not_create_a_series_per_url(client):

    client.get("/no/such/place")
    client.get("/another/missing/one")

    body = client.get("/metrics").text

    assert 'route="unmatched"' in body
    assert "no/such/place" not in body


def test_a_client_error_is_counted_with_its_status(client):

    client.get("/workflows/does-not-exist")

    assert (
        metrics.http_requests.value(
            method="GET", route="/workflows/{thread_id}", status="404"
        )
        == 1
    )


def test_in_progress_returns_to_zero(client):

    client.get("/health")

    assert metrics.http_in_progress.value() == 0


def test_metrics_stay_parseable_with_several_series(client):

    client.get("/health")
    client.post("/workflows", json={"question": ""})

    for line in client.get("/metrics").text.splitlines():

        if line.startswith("#") or not line:
            continue

        # Every sample line is "name{labels} value".
        assert len(line.rsplit(" ", 1)) == 2


# ============================================================
# SUMMARY
# ============================================================

def test_the_summary_reports_what_was_served(client):

    client.get("/health")
    client.get("/health")

    payload = client.get("/observability/summary").json()

    assert payload["requests"]["total"] >= 2
    assert payload["uptime_seconds"] >= 0
    assert payload["started_at"].endswith("+00:00")

    routes = [row["route"] for row in payload["requests"]["by_route"]]

    assert "/health" in routes


def test_the_summary_counts_failures(client):

    client.get("/workflows/does-not-exist")

    payload = client.get("/observability/summary").json()

    assert payload["requests"]["failed"] >= 1


def test_the_summary_reads_the_approval_queue_from_the_database(client):

    payload = client.get("/observability/summary").json()

    assert payload["workflows"]["awaiting_approval"] >= 0


def test_the_summary_lists_recent_and_slow_operations(client):

    from app.observability.spans import span

    with span("demo.slow"):
        pass

    payload = client.get("/observability/summary").json()

    operations = [row["operation"] for row in payload["recent_operations"]]

    assert "demo.slow" in operations
    assert payload["slowest_operations"]


def test_the_summary_limit_is_bounded(client):

    assert client.get("/observability/summary?limit=0").status_code == 422
    assert client.get("/observability/summary?limit=51").status_code == 422
    assert client.get("/observability/summary?limit=1").status_code == 200


def test_the_summary_shows_recent_errors(client):

    from app.observability.spans import span

    with pytest.raises(ValueError):
        with span("demo.broken"):
            raise ValueError("boom")

    payload = client.get("/observability/summary").json()

    assert payload["errors"]["recent"][0]["operation"] == "demo.broken"
    assert "ValueError" in payload["errors"]["recent"][0]["error"]


def test_the_summary_is_empty_but_valid_on_a_fresh_process(client):

    metrics.registry.clear()
    spans.reset()

    payload = client.get("/observability/summary").json()

    assert payload["workflows"]["by_outcome"] == {}
    assert payload["model_calls"]["total"] == 0
    assert payload["errors"]["recent"] == []


# ============================================================
# A REAL WORKFLOW
# ============================================================

QUESTION_750 = "For employee E102, process a $750 expense."


def test_a_workflow_run_is_measured_end_to_end(client):
    """
    The observer is fed by the same task stream as the audit
    trail, so a real run through the API must produce node
    timings, a run outcome and a thread id on the spans.
    """

    started = client.post("/workflows", json={"question": QUESTION_750})

    assert started.status_code == 201

    thread_id = started.json()["thread_id"]

    assert metrics.workflow_runs.value(outcome="awaiting_approval") == 1
    assert metrics.workflow_duration.count(operation="run") == 1

    assert metrics.workflow_nodes.value(node="policy", status="success") == 1
    assert metrics.workflow_node_duration.count(node="policy") == 1

    # The approval node paused rather than finished.
    assert metrics.workflow_nodes.value(node="approval", status="paused") == 1

    nodes = [
        record
        for record in spans.recent_spans.latest(50)
        if record.operation.startswith("workflow.node.")
    ]

    assert nodes
    assert all(record.thread_id == thread_id for record in nodes)


def test_a_resume_is_measured_separately(client):

    started = client.post("/workflows", json={"question": QUESTION_750})

    thread_id = started.json()["thread_id"]

    decided = client.post(
        f"/workflows/{thread_id}/decision", json={"decision": "approve"}
    )

    assert decided.status_code == 200

    assert metrics.workflow_duration.count(operation="resume") == 1
    assert metrics.workflow_runs.value(outcome="success") == 1


def test_the_summary_shows_the_run(client):

    client.post("/workflows", json={"question": QUESTION_750})

    payload = client.get("/observability/summary").json()

    assert payload["workflows"]["by_outcome"]["awaiting_approval"] == 1

    measured = [row["node"] for row in payload["workflows"]["nodes"]]

    assert "policy" in measured
    assert "rag" in measured


# ============================================================
# THE API STILL BEHAVES
# ============================================================

def test_the_openapi_document_lists_the_new_endpoints(client):

    paths = client.get("/openapi.json").json()["paths"]

    assert "/metrics" in paths
    assert "/health/ready" in paths
    assert "/observability/summary" in paths


def test_validation_errors_are_unaffected(client):

    response = client.post("/workflows", json={})

    assert response.status_code == 422
    assert response.headers[REQUEST_ID_HEADER]
