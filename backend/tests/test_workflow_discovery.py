"""
Workflow discovery: process behaviour learned from the audit trail
and business tables, compared with the Context Graph's process model.

Most tests insert synthetic audit events with exact timestamps so
durations and waits can be asserted precisely; the end-to-end tests
run real workflows through the API.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.services import workflow_discovery_service as discovery
from app.services.database_service import get_employee
from app.services.db import session_scope
from app.services.db_models import (
    AuditEvent, ContextEdge, ContextNode, WorkflowRun,
)
from discovery_helpers import (
    BASE, PARALLEL, emit, new_threads, run_real_workflows, standard_events,
)
from app.services.workflow_discovery_service import (
    amount_bands,
    band_for,
    discover_workflows,
    summarize,
)


UTC = timezone.utc


def discover(thread_ids, **kwargs) -> dict:

    return discover_workflows(thread_ids=thread_ids, **kwargs)


def variant_for(result, text):

    [variant] = [v for v in result["variants"] if v["path_text"] == text]

    return variant


STANDARD_PATH = "employee+policy+rag -> approval -> action -> final"


# ============================================================
# PATHS
# ============================================================

def test_parallel_nodes_share_a_stage_whatever_their_finish_order():

    threads = []

    for order in (
        ("employee", "policy", "rag"),
        ("rag", "policy", "employee"),
        ("policy", "rag", "employee"),
    ):
        threads += new_threads(1, finish_order=order)

    result = discover(threads)

    [variant] = result["variants"]

    assert variant["path"] == [
        ["employee", "policy", "rag"], ["approval"], ["action"], ["final"],
    ]
    assert variant["path_text"] == STANDARD_PATH
    assert variant["workflows"] == 3
    assert variant["share"] == 1.0


def test_pausing_for_approval_does_not_repeat_the_stage():

    [thread] = new_threads(1, approval="approve")

    [variant] = discover([thread])["variants"]

    assert variant["path"] == [
        ["employee", "policy", "rag"], ["approval"], ["action"], ["final"],
    ]


def test_human_approval_makes_a_separate_variant():
    """Every path runs the approval node; only some wait for a person."""

    threads = (
        new_threads(2) + new_threads(1, approval="approve")
    )

    result = discover(threads)

    assert [(v["path_text"], v["workflows"], v["human_approval"])
            for v in result["variants"]] == [
        (STANDARD_PATH, 2, False),
        (STANDARD_PATH + " [human approval]", 1, True),
    ]

    assert result["variants"][0]["variant_id"] != result["variants"][1]["variant_id"]


def test_rejection_and_recovery_paths():

    threads = new_threads(1, approval="reject") + new_threads(1, fail_node="employee")

    result = discover(threads)

    paths = {v["path_text"] for v in result["variants"]}

    assert paths == {
        "employee+policy+rag -> approval -> rejection -> final [human approval]",
        "employee+policy+rag -> recovery",
    }


def test_variants_are_ordered_by_frequency_with_outcomes_and_samples():

    threads = (
        new_threads(5)
        + new_threads(2, approval="approve")
        + new_threads(1, approval="reject")
        + new_threads(1, fail_node="rag")
    )

    result = discover(threads)

    assert [v["workflows"] for v in result["variants"]] == [5, 2, 1, 1]

    first = result["variants"][0]

    assert first["share"] == round(5 / 9, 4)
    assert first["outcomes"] == {"completed": 5}
    assert len(first["sample_thread_ids"]) == 3          # capped
    assert set(first["sample_thread_ids"]) <= set(threads)

    approved = result["variants"][1]

    assert approved["outcomes"] == {"completed": 2}

    assert sum(v["share"] for v in result["variants"]) == pytest.approx(1.0, abs=0.001)


def test_cycle_time_includes_waiting_for_approval():

    [plain] = new_threads(1)
    [waited] = new_threads(1, approval="approve", wait=60.0)

    result = discover([plain, waited])

    plain_cycle = variant_for(result, STANDARD_PATH)["cycle_time_seconds"]
    waited_cycle = variant_for(result, STANDARD_PATH + " [human approval]")["cycle_time_seconds"]

    assert plain_cycle["count"] == 1 and plain_cycle["median"] < 5
    assert waited_cycle["median"] > 60


# ============================================================
# OUTCOMES
# ============================================================

def test_every_outcome_is_classified():

    completed = new_threads(1)
    rejected = new_threads(1, approval="reject")
    failed = new_threads(1, fail_node="policy")
    pending = new_threads(1, approval="pending")
    started_only = [emit(f"disc-{uuid4().hex[:12]}", [(0.0, "workflow_started", None)])]
    errored = [emit(f"disc-{uuid4().hex[:12]}", [
        (0.0, "workflow_started", None), (0.1, "node_started", "employee"),
        (0.2, "node_failed", "employee"), (0.3, "workflow_error", None),
    ])]

    result = discover(completed + rejected + failed + pending + started_only + errored)

    assert result["outcomes"] == {
        "completed": 1, "rejected": 1, "failed": 1, "error": 1,
        "awaiting_approval": 1, "in_progress": 1,
    }

    assert variant_for(result, "(no nodes executed)")["workflows"] == 1


# ============================================================
# STEP STATISTICS
# ============================================================

def test_step_durations_failures_and_modeled_steps():

    threads = new_threads(3) + new_threads(1, fail_node="employee")

    steps = {s["node"]: s for s in discover(threads)["steps"]}

    # started at +0.1 and finished at +0.2 / +0.3 / +0.4 (employee in the failing run +0.2)
    assert steps["policy"]["executions"] == 4
    assert steps["policy"]["failures"] == 0
    assert steps["policy"]["failure_rate"] == 0.0

    employee = steps["employee"]

    assert employee["executions"] == 4
    assert employee["failures"] == 1
    assert employee["failure_rate"] == 0.25
    assert employee["workflows"] == 4

    assert steps["action"]["duration_seconds"] == {
        "count": 3, "mean": 0.3, "median": 0.3, "p95": 0.3, "max": 0.3
    }

    # Nodes map to the modeled steps they implement.
    assert steps["policy"]["modeled_steps"] == [4]
    assert steps["employee"]["modeled_steps"] == [5]
    assert steps["approval"]["modeled_steps"] == [7, 8]
    assert steps["action"]["modeled_steps"] == [9]
    assert steps["final"]["modeled_steps"] == []


def test_approval_node_duration_excludes_the_human_wait():

    [thread] = new_threads(1, approval="approve", wait=300.0)

    approval = {s["node"]: s for s in discover([thread])["steps"]}["approval"]

    # Only the resumed run (start -> completion), not the 300s pause.
    assert approval["executions"] == 1
    assert approval["duration_seconds"]["max"] == pytest.approx(0.1, abs=0.01)


def test_steps_are_ordered_by_how_many_workflows_used_them():

    steps = discover(new_threads(2) + new_threads(1, fail_node="rag"))["steps"]

    counts = [s["workflows"] for s in steps]

    assert counts == sorted(counts, reverse=True)


# ============================================================
# APPROVALS
# ============================================================

def test_approval_statistics():

    threads = (
        new_threads(2)                                     # no approval needed
        + new_threads(1, approval="approve", wait=60.0)
        + new_threads(1, approval="approve", wait=30.0)
        + new_threads(1, approval="reject", wait=90.0)
        + new_threads(1, approval="pending")
    )

    approvals = discover(threads)["approvals"]

    assert approvals["workflows"] == 6
    assert approvals["required_approval"] == 4
    assert approvals["approval_rate_of_workflows"] == round(4 / 6, 4)
    assert (approvals["approved"], approvals["rejected"], approvals["pending"]) == (2, 1, 1)
    assert approvals["rejection_rate"] == round(1 / 3, 4)

    wait = approvals["wait_seconds"]

    assert wait["count"] == 3
    assert wait["median"] == pytest.approx(60.0, abs=0.1)
    assert wait["max"] == pytest.approx(90.0, abs=0.1)


def test_no_approvals_means_no_wait_statistics():

    approvals = discover(new_threads(2))["approvals"]

    assert approvals["required_approval"] == 0
    assert approvals["wait_seconds"] is None
    assert approvals["rejection_rate"] == 0.0


# ============================================================
# PROCESS MODEL COMPARISON
# ============================================================

def test_process_model_shows_what_is_automated_and_what_is_not():

    model = discover(new_threads(3))["process_model"]

    assert model["process"] == "process:expense_reimbursement"
    assert model["thresholds"] == [500.0, 5000.0]

    by_order = {s["order"]: s for s in model["steps"]}

    assert [s["order"] for s in model["steps"]] == list(range(1, 11))

    assert {o: s["automation"] for o, s in by_order.items()} == {
        1: "manual", 2: "manual", 3: "manual", 4: "automated", 5: "automated",
        6: "manual", 7: "automated", 8: "partial", 9: "automated", 10: "manual",
    }

    assert [s["order"] for s in model["automation_gaps"]] == [1, 2, 3, 6, 8, 10]


def test_modeled_steps_report_who_performs_them_and_the_evidence():

    model = discover(new_threads(1))["process_model"]

    step = {s["order"]: s for s in model["steps"]}[6]

    assert step["label"] == "Check department budget"
    assert step["performed_by"] == ["Finance"]
    assert step["implemented_by"] == []
    assert step["evidence"] == ["document:data/expense_process.txt#L13"]

    notify = {s["order"]: s for s in model["steps"]}[10]

    assert notify["uses_systems"] == ["Email"]

    approval_step = {s["order"]: s for s in model["steps"]}[8]

    assert approval_step["implemented_by"] == [{
        "component": "agent_node:approval",
        "label": "Human approval node",
        "coverage": "partial",
    }]


def test_observed_usage_per_modeled_step():

    threads = new_threads(3) + new_threads(1, fail_node="rag")

    model = {s["order"]: s for s in discover(threads)["process_model"]["steps"]}

    assert model[4]["workflows_observed"] == 4      # policy / rag ran in every run
    assert model[5]["workflows_observed"] == 4
    assert model[7]["workflows_observed"] == 3      # approval ran in the 3 that got there
    assert model[9]["workflows_observed"] == 3
    # Manual steps cannot be observed by the system.
    assert model[1]["workflows_observed"] is None
    assert model[10]["workflows_observed"] is None


def test_nodes_outside_the_model_are_reported():

    threads = (
        new_threads(1)
        + new_threads(1, approval="reject")
        + new_threads(1, fail_node="policy")
    )

    extra = emit(f"disc-{uuid4().hex[:12]}", [
        (0.0, "workflow_started", None),
        (0.1, "node_started", "mystery"),
        (0.2, "node_completed", "mystery"),
        (0.3, "workflow_completed", None),
    ])

    unmapped = discover(threads + [extra])["process_model"]["unmapped_nodes"]

    assert unmapped == ["final", "mystery", "recovery", "rejection"]


def test_process_model_is_omitted_when_the_process_is_not_in_the_graph(monkeypatch):

    monkeypatch.setattr(discovery, "get_process_context", lambda key: None)

    result = discover(new_threads(1))

    assert result["process_model"] is None
    assert result["volume"]["by_amount_band"][0]["band"] == "all amounts"
    assert [s["modeled_steps"] for s in result["steps"] if "modeled_steps" in s] == []


# ============================================================
# WINDOW AND FILTERS
# ============================================================

def test_empty_window():

    result = discover([f"no-such-thread-{uuid4().hex}"])

    assert result["window"]["workflows_analyzed"] == 0
    assert result["window"]["first_started_at"] is None
    assert result["variants"] == [] and result["steps"] == []
    assert result["approvals"]["wait_seconds"] is None
    assert set(result["outcomes"].values()) == {0}


def test_limit_keeps_the_most_recent_runs():

    threads = new_threads(5)

    result = discover(threads, limit=2)

    [variant] = result["variants"]

    assert result["window"]["workflows_analyzed"] == 2
    assert set(variant["sample_thread_ids"]) == set(threads[-2:])


def test_since_and_until_select_runs_by_start_time():

    namespace = uuid4().hex[:12]
    day = timedelta(days=1)
    base = datetime(2050, 6, 1, tzinfo=UTC)

    threads = [
        emit(f"win-{namespace}-{n}", standard_events(), base=base + n * day)
        for n in range(4)
    ]

    one = discover(threads, since=base + day, until=base + 2 * day)

    assert one["window"]["workflows_analyzed"] == 2
    assert one["window"]["since"] == (base + day).isoformat()
    assert one["window"]["first_started_at"].startswith("2050-06-02")
    assert one["window"]["last_started_at"].startswith("2050-06-03")

    assert discover(threads, since=base + 2 * day)["window"]["workflows_analyzed"] == 2
    assert discover(threads, until=base)["window"]["workflows_analyzed"] == 1

    # Naive datetimes are taken as UTC.
    assert discover(
        threads, since=(base + day).replace(tzinfo=None)
    )["window"]["workflows_analyzed"] == 3


def test_invalid_arguments():

    for limit in (0, -1, discovery.MAX_LIMIT + 1):
        with pytest.raises(ValueError, match="limit must be between"):
            discover_workflows(limit=limit)

    with pytest.raises(ValueError, match="until must not be before since"):
        discover_workflows(since=BASE, until=BASE - timedelta(seconds=1))


def test_large_windows_are_loaded_in_chunks(monkeypatch):

    monkeypatch.setattr(discovery, "LOOKUP_CHUNK", 2)

    threads = new_threads(5) + new_threads(3, approval="approve")

    chunked = discover(threads)

    monkeypatch.setattr(discovery, "LOOKUP_CHUNK", 500)

    assert chunked == discover(threads)
    assert chunked["window"]["workflows_analyzed"] == 8


def test_runs_without_a_workflow_started_event_are_ignored():

    thread = emit(f"disc-{uuid4().hex[:12]}", [
        (0.0, "node_started", "employee"), (0.1, "node_completed", "employee"),
    ])

    assert discover([thread])["window"]["workflows_analyzed"] == 0


# ============================================================
# HELPERS
# ============================================================

def test_summarize():

    assert summarize([]) is None

    assert summarize([2.0]) == {"count": 1, "mean": 2.0, "median": 2.0, "p95": 2.0, "max": 2.0}

    assert summarize([5, 1, 3, 2, 4]) == {
        "count": 5, "mean": 3.0, "median": 3, "p95": 4.8, "max": 5,
    }


def test_amount_bands_follow_the_rule_wording():

    bands = amount_bands([5000, 500])

    assert [b[0] for b in bands] == ["up to 500", "above 500 up to 5000", "above 5000"]

    for amount, label in (
        (0.01, "up to 500"), (500, "up to 500"),
        (500.01, "above 500 up to 5000"), (5000, "above 500 up to 5000"),
        (5000.01, "above 5000"), (1_000_000, "above 5000"),
    ):
        assert band_for(amount, bands) == label

    assert amount_bands([]) == [("all amounts", None, None)]


# ============================================================
# END TO END: REAL WORKFLOWS
# ============================================================

def test_discovery_from_real_workflow_runs():

    start = datetime.now(UTC)

    with TestClient(app) as client:
        threads = run_real_workflows(client)

    # Bounded on both sides: synthetic runs elsewhere in this file are
    # dated in the future.
    result = discover_workflows(since=start, until=datetime.now(UTC))

    assert result["window"]["workflows_analyzed"] == 6

    assert result["outcomes"] == {
        "completed": 3, "rejected": 1, "failed": 1, "error": 0,
        "awaiting_approval": 1, "in_progress": 0,
    }

    variants = {v["path_text"]: v for v in result["variants"]}

    assert variants[STANDARD_PATH]["workflows"] == 2
    assert variants[STANDARD_PATH + " [human approval]"]["workflows"] == 1
    assert variants[
        "employee+policy+rag -> approval -> rejection -> final [human approval]"
    ]["workflows"] == 1
    assert variants["employee+policy+rag -> recovery"]["outcomes"] == {"failed": 1}
    assert variants["employee+policy+rag -> approval [human approval]"]["outcomes"] == {
        "awaiting_approval": 1
    }

    approvals = result["approvals"]

    assert (approvals["required_approval"], approvals["approved"],
            approvals["rejected"], approvals["pending"]) == (3, 1, 1, 1)
    assert approvals["wait_seconds"]["count"] == 2

    steps = {s["node"]: s for s in result["steps"]}

    assert steps["employee"]["failures"] == 1 and steps["employee"]["executions"] == 6
    assert steps["action"]["executions"] == 3
    assert result["process_model"]["unmapped_nodes"] == ["final", "recovery", "rejection"]


def test_volume_from_real_runs_uses_business_tables_and_policy_thresholds():

    start = datetime.now(UTC)

    with TestClient(app) as client:
        run_real_workflows(client)

    volume = discover_workflows(since=start, until=datetime.now(UTC))["volume"]

    # The unknown employee has no run row with an amount/department link.
    assert volume["workflows_with_run_record"] + volume["workflows_without_run_record"] == 6

    bands = {b["band"]: b for b in volume["by_amount_band"]}

    assert [(b["above"], b["up_to"]) for b in volume["by_amount_band"][:3]] == [
        (None, 500.0), (500.0, 5000.0), (5000.0, None)
    ]

    assert bands["up to 500"]["workflows"] == 3              # 400, 300 and the E999 $100 claim
    assert bands["up to 500"]["required_approval"] == 0
    assert bands["above 500 up to 5000"]["workflows"] == 2   # 750 approved, 900 rejected
    assert bands["above 500 up to 5000"]["required_approval"] == 2
    assert bands["above 500 up to 5000"]["rejected"] == 1
    assert bands["above 5000"]["workflows"] == 1             # 6000, pending

    departments = {d["department"]: d for d in volume["by_department"]}

    engineering = get_employee("E101")["department"]
    finance = get_employee("E102")["department"]

    assert departments[finance]["rejected"] == 1
    assert departments[finance]["total_amount"] == pytest.approx(750 + 900)
    assert departments[engineering]["workflows"] >= 2


def test_discovery_is_read_only():

    def counts():
        with session_scope() as session:
            return tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (AuditEvent, ContextNode, ContextEdge, WorkflowRun)
            )

    threads = new_threads(3, approval="approve")

    before = counts()

    discover_workflows(limit=500)
    discover(threads)

    assert counts() == before


# ============================================================
# API
# ============================================================

def iso(moment: datetime) -> str:

    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_api_returns_the_discovery_report():

    start = datetime.now(UTC)

    with TestClient(app) as client:

        run_real_workflows(client)

        end = datetime.now(UTC)

        response = client.get(
            "/discovery/workflows", params={"since": iso(start), "until": iso(end)}
        )

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {
        "window", "outcomes", "variants", "steps", "approvals", "volume",
        "process_model",
    }
    assert body["window"]["workflows_analyzed"] == 6
    assert body["window"]["limit"] == discovery.DEFAULT_LIMIT
    assert body["outcomes"]["completed"] == 3

    assert set(body["variants"][0]) == {
        "variant_id", "path", "path_text", "human_approval", "workflows", "share",
        "outcomes", "cycle_time_seconds", "sample_thread_ids",
    }
    assert set(body["steps"][0]) == {
        "node", "workflows", "executions", "failures", "failure_rate",
        "duration_seconds", "modeled_steps",
    }
    assert set(body["process_model"]) == {
        "process", "label", "steps", "automation_gaps", "unmapped_nodes", "thresholds",
    }
    assert [s["order"] for s in body["process_model"]["automation_gaps"]] == [1, 2, 3, 6, 8, 10]

    # The service and the API agree.
    assert body == discover_workflows(since=start, until=end)


def test_api_limit_since_and_until():

    start = datetime.now(UTC)

    with TestClient(app) as client:

        run_real_workflows(client)

        end = datetime.now(UTC)

        limited = client.get(
            "/discovery/workflows",
            params={"since": iso(start), "until": iso(end), "limit": 2},
        ).json()

        nothing = client.get(
            "/discovery/workflows",
            params={
                "since": iso(start + timedelta(days=365)),
                "until": iso(start + timedelta(days=366)),
            },
        ).json()

    assert limited["window"]["workflows_analyzed"] == 2
    assert nothing["window"]["workflows_analyzed"] == 0
    assert nothing["process_model"] is not None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": discovery.MAX_LIMIT + 1},
        {"limit": "many"},
        {"since": "yesterday"},
        {"until": "2030-13-45"},
    ],
)
def test_api_rejects_invalid_parameters(params):

    with TestClient(app) as client:

        assert client.get("/discovery/workflows", params=params).status_code == 422


def test_api_rejects_a_window_that_ends_before_it_starts():

    with TestClient(app) as client:

        response = client.get("/discovery/workflows", params={
            "since": "2030-01-02T00:00:00Z", "until": "2030-01-01T00:00:00Z",
        })

    assert response.status_code == 422
    assert response.json() == {"detail": "until must not be before since."}


def test_endpoint_is_documented():

    with TestClient(app) as client:

        paths = client.get("/openapi.json").json()["paths"]

    schema = paths["/discovery/workflows"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]

    assert schema["$ref"].endswith("WorkflowDiscoveryResponse")
