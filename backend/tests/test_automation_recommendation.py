"""
Automation recommendation: ranked, explained advice built on workflow
discovery and the context graph's process model.

Synthetic audit events (exact timings) drive most tests; hand-built
discovery reports exercise the thresholds; real workflow runs cover
the API end to end.
"""

import copy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.services import automation_recommendation_service as reco
from app.services.automation_recommendation_service import (
    build_recommendations,
    confidence_for,
    make_recommendation,
    priority_for,
    recommend_automation,
    volume_scale,
    workflows_above,
)
from app.services.context_graph_seed import PROCESS, step_key
from app.services.context_graph_service import get_process_context
from app.services.db import session_scope
from app.services.db_models import (
    AuditEvent, ContextEdge, ContextNode, Reimbursement, WorkflowRun,
)
from app.services.workflow_discovery_service import discover_workflows
from discovery_helpers import emit, new_threads, run_real_workflows, standard_events


UTC = timezone.utc


def recommend(thread_ids) -> dict:
    """Recommendations for synthetic runs (discovery limited to them)."""

    return build_recommendations(
        discover_workflows(thread_ids=thread_ids), get_process_context(PROCESS)
    )


def by_id(result) -> dict:

    return {r["recommendation_id"]: r for r in result["recommendations"]}


def manual_id(order: int) -> str:

    return f"automate_manual_step:{step_key(order)}"


def blank_discovery(analyzed: int = 100) -> dict:
    """A real (empty) discovery report with the numbers replaced."""

    report = discover_workflows(thread_ids=[f"none-{uuid4().hex}"])

    report["window"]["workflows_analyzed"] = analyzed

    return report


def duration(mean, count=10):

    return {"count": count, "mean": mean, "median": mean, "p95": mean, "max": mean}


# ============================================================
# RUBRIC
# ============================================================

def test_volume_scale_is_logarithmic_and_capped():

    assert volume_scale(0) == 0.0
    assert volume_scale(-5) == 0.0
    assert volume_scale(10) == pytest.approx(0.3472, abs=0.001)
    assert volume_scale(100) == pytest.approx(0.668, abs=0.001)
    assert volume_scale(reco.VOLUME_FULL_SCALE) == 1.0
    assert volume_scale(10_000_000) == 1.0


@pytest.mark.parametrize(
    "sample, expected",
    [(0, (0.3, "low")), (9, (0.3, "low")), (10, (0.6, "medium")),
     (49, (0.6, "medium")), (50, (0.9, "high")), (5000, (0.9, "high"))],
)
def test_confidence_follows_the_sample_size(sample, expected):

    assert confidence_for(sample) == expected


@pytest.mark.parametrize(
    "score, priority",
    [(0, "low"), (44.9, "low"), (45.0, "medium"), (69.9, "medium"),
     (70.0, "high"), (100, "high")],
)
def test_priority_thresholds(score, priority):

    assert priority_for(score) == priority


def make(**overrides):

    values = dict(
        kind="automate_manual_step", target={"kind": "node", "key": "x", "label": "x", "order": None},
        title="t", summary="s", impact=1.0, feasibility=1.0, sample=50, effort="low",
        rationale=[], metrics={}, actions=[],
    )

    return make_recommendation(**(values | overrides))


def test_score_is_the_documented_weighted_sum():

    assert reco.WEIGHTS == {"impact": 0.5, "feasibility": 0.3, "confidence": 0.2}
    assert sum(reco.WEIGHTS.values()) == pytest.approx(1.0)

    # 100 * (0.5*1 + 0.3*1 + 0.2*0.9)
    assert make()["score"] == 98.0
    assert make(impact=0.0, feasibility=0.0, sample=0)["score"] == 6.0

    rec = make(impact=0.4, feasibility=0.5, sample=10)

    assert rec["score"] == 47.0                       # 100*(0.2 + 0.15 + 0.12)
    assert rec["scores"] == {"impact": 0.4, "feasibility": 0.5, "confidence": 0.6}
    assert (rec["priority"], rec["confidence"]) == ("medium", "medium")


def test_impact_is_clamped():

    assert make(impact=5)["scores"]["impact"] == 1.0
    assert make(impact=-1)["scores"]["impact"] == 0.0


# ============================================================
# MANUAL STEPS
# ============================================================

def test_every_manual_step_gets_a_recommendation_and_nothing_else_does():

    result = recommend(new_threads(50))

    ids = set(by_id(result))

    assert {i for i in ids if i.startswith("automate_manual_step")} == {
        manual_id(n) for n in (1, 2, 3, 6, 10)
    }

    # Automated steps (4, 5, 7, 9) and healthy nodes produce none; the
    # partially automated step 8 gets its own kind.
    assert ids - {manual_id(n) for n in (1, 2, 3, 6, 10)} == {
        f"complete_partial_automation:{step_key(8)}"
    }


def test_manual_step_recommendation_content():

    rec = by_id(recommend(new_threads(50)))[manual_id(10)]

    assert rec["type"] == "automate_manual_step"
    assert rec["title"] == "Automate: Notify employee of the final decision"
    assert rec["target"] == {
        "kind": "process_step", "key": step_key(10),
        "label": "Notify employee of the final decision", "order": 10,
    }
    assert rec["effort"] == "low"
    assert rec["requires_human_decision"] is False
    assert rec["evidence"] == ["document:data/expense_process.txt#L21"]

    assert rec["metrics"] == {
        "workflows_in_window": 50, "performed_by": [],
        "uses_systems": ["Email"], "pattern": "notification",
    }

    assert rec["rationale"][0] == (
        "'Notify employee of the final decision' is a manual step: no "
        "workflow node implements it."
    )
    assert "It is performed by an unspecified role." in rec["rationale"]
    assert "50 claim(s) were processed" in rec["rationale"][2]
    assert "uses Email, which can be integrated" in rec["rationale"][3]

    assert rec["suggested_actions"][-1] == "Integrate with Email."


@pytest.mark.parametrize(
    "order, pattern, feasibility, effort",
    [
        (1, "automated intake", 0.75, "medium"),
        (2, "automated intake", 0.75, "medium"),
        (3, "rule-based validation", 0.7, "medium"),
        (6, "rule-based validation", 0.7, "medium"),
        (10, "notification", 0.9, "low"),
    ],
)
def test_step_pattern_decides_feasibility_and_effort(order, pattern, feasibility, effort):

    rec = by_id(recommend(new_threads(3)))[manual_id(order)]

    assert rec["metrics"]["pattern"] == pattern
    assert rec["scores"]["feasibility"] == feasibility
    assert rec["effort"] == effort


def test_manual_steps_rank_by_feasibility_when_volume_is_the_same():

    result = recommend(new_threads(50))

    manual = [r["recommendation_id"] for r in result["recommendations"]
              if r["type"] == "automate_manual_step"]

    # notification (0.9), then intake (0.75, tie by id), then validation (0.7)
    assert manual == [manual_id(n) for n in (10, 1, 2, 3, 6)]

    scores = [r["score"] for r in result["recommendations"]]

    assert scores == sorted(scores, reverse=True)


def test_volume_and_sample_size_drive_priority():

    big = by_id(recommend(new_threads(60)))[manual_id(10)]
    small = by_id(recommend(new_threads(3)))[manual_id(10)]

    assert big["priority"] == "high" and big["confidence"] == "high"
    assert small["priority"] == "low" and small["confidence"] == "low"
    assert big["score"] > small["score"]
    assert big["scores"]["impact"] > small["scores"]["impact"]


def test_pattern_matching():

    cases = {
        "Send the report to finance": "notification",
        "Record approved expense in accounting system": "system integration",
        "Verify employee identity": "rule-based validation",
        "Receive expense claim": "automated intake",
        "Sort the mail": "task automation",
        # Stems match at the start of a word only, not inside one.
        "Plan the staff center": "task automation",
        "Reenter the figures": "task automation",
    }

    for label, pattern in cases.items():
        assert reco.match_pattern(label)["name"] == pattern, label

    assert reco.match_pattern("Sort the mail")["effort"] == "high"


def test_a_system_to_integrate_with_raises_feasibility():

    discovery = blank_discovery(50)

    # The notification step: feasibility differs with and without a system.
    [step] = [
        copy.deepcopy(s) for s in discovery["process_model"]["steps"] if s["order"] == 10
    ]

    with_system = reco.manual_step_recommendation(step | {"uses_systems": ["Email"]}, 50)
    without = reco.manual_step_recommendation(step | {"uses_systems": []}, 50)

    assert with_system["scores"]["feasibility"] > without["scores"]["feasibility"]
    assert "names no system" in without["rationale"][3]


# ============================================================
# PARTIAL AUTOMATION
# ============================================================

def volume_with(bands, recorded=None):

    recorded = sum(b[2] for b in bands) if recorded is None else recorded

    return {
        "workflows_with_run_record": recorded,
        "workflows_without_run_record": 0,
        "by_department": [],
        "by_amount_band": [
            {"band": f"{a}-{u}", "above": a, "up_to": u, "workflows": n,
             "required_approval": 0, "rejected": 0}
            for a, u, n in bands
        ] + [{"band": "unknown amount", "above": None, "up_to": None,
              "workflows": 3, "required_approval": 0, "rejected": 0}],
    }


BANDS = [(None, 500.0, 80), (500.0, 5000.0, 15), (5000.0, None, 5)]


def test_workflows_above_a_threshold():

    volume = volume_with(BANDS)

    assert workflows_above(volume, 500.0) == 20
    assert workflows_above(volume, 5000.0) == 5
    assert workflows_above(volume, 1_000_000.0) == 0       # no band starts there


def test_partial_step_uses_the_share_of_claims_it_applies_to():

    discovery = blank_discovery(100)
    discovery["volume"] = volume_with(BANDS)

    rec = by_id(build_recommendations(discovery, get_process_context(PROCESS)))[
        f"complete_partial_automation:{step_key(8)}"
    ]

    assert rec["title"] == "Complete automation: Finance approval for expenses above $5,000"
    assert rec["metrics"] == {
        "components": ["Human approval node"], "workflows_in_window": 100,
        "threshold": 5000.0, "workflows_affected": 5, "affected_share": 0.05,
    }
    assert "5 of 100 recorded claim(s) (5%)" in rec["rationale"][1]
    assert rec["scores"]["feasibility"] == 0.85
    assert rec["effort"] == "low"
    assert rec["scores"]["impact"] == pytest.approx(0.05 * volume_scale(100), abs=0.001)
    assert rec["evidence"] == ["document:data/expense_process.txt#L17"]
    assert "separate decision" in rec["suggested_actions"][0]


def test_more_affected_claims_means_a_higher_score():

    def score(bands):
        discovery = blank_discovery(100)
        discovery["volume"] = volume_with(bands)
        return by_id(build_recommendations(
            discovery, get_process_context(PROCESS)
        ))[f"complete_partial_automation:{step_key(8)}"]["score"]

    assert score([(None, 500.0, 40), (500.0, 5000.0, 20), (5000.0, None, 40)]) > score(BANDS)


def test_partial_step_without_a_threshold_falls_back_to_a_volume_estimate():

    discovery = blank_discovery(100)

    rec = by_id(build_recommendations(discovery, None))[
        f"complete_partial_automation:{step_key(8)}"
    ]

    assert "No policy threshold" in rec["rationale"][1]
    assert rec["scores"]["impact"] == pytest.approx(0.5 * volume_scale(100), abs=0.001)


# ============================================================
# STEP FAILURES
# ============================================================

def test_a_failing_step_is_recommended_for_improvement():

    threads = new_threads(7) + new_threads(3, fail_node="employee")

    result = recommend(threads)

    rec = by_id(result)["reduce_step_failures:employee"]

    assert rec["type"] == "reduce_step_failures"
    assert rec["target"] == {"kind": "node", "key": "employee", "label": "employee", "order": None}
    assert rec["title"] == "Reduce failures in the employee step"
    assert rec["metrics"] == {
        "executions": 10, "failures": 3, "failure_rate": 0.3, "runs_ending_failed": 3,
    }
    assert rec["rationale"] == [
        "'employee' failed in 3 of 10 execution(s) (30%).",
        "3 of 10 run(s) ended as failed, so the person who submitted the "
        "claim got no result.",
    ]
    assert "Validate the employee ID" in rec["suggested_actions"][0]

    # impact: 30% >= 25% saturates, scaled by volume (10 executions)
    assert rec["scores"]["impact"] == pytest.approx(volume_scale(10), abs=0.001)
    assert rec["scores"]["feasibility"] == 0.8
    assert (rec["priority"], rec["confidence"]) == ("medium", "medium")
    assert rec["score"] == pytest.approx(53.4, abs=0.1)


def test_healthy_or_rarely_run_steps_are_left_alone():

    # 1 failure in 25 executions = 4%, below the 5% threshold.
    assert "reduce_step_failures:employee" not in by_id(
        recommend(new_threads(24) + new_threads(1, fail_node="employee"))
    )

    # 50% failing but only 4 executions.
    assert "reduce_step_failures:employee" not in by_id(
        recommend(new_threads(2) + new_threads(2, fail_node="employee"))
    )


def test_failure_thresholds_are_inclusive():

    discovery = blank_discovery(100)

    def with_step(executions, failures):

        discovery["steps"] = [{
            "node": "policy", "workflows": executions, "executions": executions,
            "failures": failures, "failure_rate": failures / executions,
            "duration_seconds": None,
        }]

        return "reduce_step_failures:policy" in by_id(
            build_recommendations(discovery, None)
        )

    assert with_step(20, 1) is True       # exactly 5%
    assert with_step(100, 4) is False
    assert with_step(5, 5) is True        # exactly 5 executions
    assert with_step(4, 4) is False


def test_unknown_nodes_get_a_generic_fix_and_known_nodes_a_specific_one():

    discovery = blank_discovery(100)

    discovery["steps"] = [
        {"node": name, "workflows": 20, "executions": 20, "failures": 10,
         "failure_rate": 0.5, "duration_seconds": None}
        for name in ("rag", "action", "mystery")
    ]

    recs = by_id(build_recommendations(discovery, None))

    assert "indexed" in recs["reduce_step_failures:rag"]["suggested_actions"][0]
    assert "retry" in recs["reduce_step_failures:action"]["suggested_actions"][0]
    assert "audit trail" in recs["reduce_step_failures:mystery"]["suggested_actions"][0]
    assert recs["reduce_step_failures:mystery"]["scores"]["feasibility"] == 0.5


# ============================================================
# APPROVAL WAIT
# ============================================================

def approval_runs(approved, rejected=0, wait=60.0):

    return (
        new_threads(approved, approval="approve", wait=wait)
        + (new_threads(rejected, approval="reject", wait=wait) if rejected else [])
        + new_threads(2)
    )


def test_long_waits_for_approvers_are_recommended_for_improvement():

    rec = by_id(recommend(approval_runs(8)))["reduce_approval_wait:approval"]

    assert rec["type"] == "reduce_approval_wait"
    assert rec["target"] == {"kind": "approval", "key": "approval", "label": "Human approval", "order": None}
    assert rec["metrics"]["wait_seconds"]["count"] == 8
    assert rec["metrics"]["wait_seconds"]["mean"] == pytest.approx(60.0, abs=0.1)
    assert rec["metrics"]["wait_share_of_cycle"] > 0.9
    assert rec["metrics"]["required_approval"] == 8
    assert rec["metrics"]["pending"] == 0

    assert "reminders" not in rec["rationale"][0]
    assert "claims that needed approval" in rec["rationale"][0]
    assert "8 of 10 run(s) needed approval; 0 are still waiting." in rec["rationale"][1]
    assert "escalate" in rec["suggested_actions"][0]
    assert "reporting lines" in rec["suggested_actions"][0]

    # Too few decisions to float delegated approval.
    assert len(rec["suggested_actions"]) == 1
    assert rec["requires_human_decision"] is False

    assert rec["confidence"] == "low"                    # 8 decisions (< 10)


def test_delegated_approval_is_only_suggested_with_enough_evidence():

    plenty = by_id(recommend(approval_runs(25, rejected=1)))["reduce_approval_wait:approval"]

    assert plenty["requires_human_decision"] is True
    assert "business decision" in plenty["suggested_actions"][1]
    assert "1 of 26 decision(s)" in plenty["rationale"][-1]

    # Same volume but too many rejections: no delegation idea.
    rejects = by_id(recommend(approval_runs(15, rejected=10)))["reduce_approval_wait:approval"]

    assert rejects["requires_human_decision"] is False
    assert len(rejects["suggested_actions"]) == 1


def test_short_waits_are_not_a_problem():

    result = recommend(approval_runs(8, wait=0.2))

    assert "reduce_approval_wait:approval" not in by_id(result)


def test_no_approvals_means_no_wait_recommendation():

    assert "reduce_approval_wait:approval" not in by_id(recommend(new_threads(5)))


def test_pending_requests_are_mentioned():

    rec = by_id(recommend(approval_runs(6) + new_threads(3, approval="pending")))[
        "reduce_approval_wait:approval"
    ]

    assert "3 are still waiting" in rec["rationale"][1]
    assert rec["metrics"]["pending"] == 3


# ============================================================
# MODEL AND WINDOW EDGE CASES
# ============================================================

def test_without_a_process_model_only_observed_problems_are_reported():

    discovery = blank_discovery(100)
    discovery["process_model"] = None

    result = build_recommendations(discovery, None)

    assert result["recommendations"] == []
    assert "step-level recommendations are unavailable" in result["warnings"][0]


def test_warnings_for_small_and_empty_windows():

    empty = build_recommendations(blank_discovery(0), get_process_context(PROCESS))
    few = build_recommendations(blank_discovery(4), get_process_context(PROCESS))
    enough = build_recommendations(blank_discovery(10), get_process_context(PROCESS))

    assert "No workflow runs were found" in empty["warnings"][0]
    assert "Only 4 run(s) were analysed; confidence is low." in few["warnings"]
    assert enough["warnings"] == []


def test_empty_window_still_lists_the_structural_gaps_with_low_confidence():

    result = recommend_automation(
        since=datetime(2099, 1, 1, tzinfo=UTC), until=datetime(2099, 1, 2, tzinfo=UTC)
    )

    assert result["basis"]["window"]["workflows_analyzed"] == 0
    assert "No workflow runs were found" in result["warnings"][0]

    assert result["summary"] == {
        "total": 6, "by_priority": {"high": 0, "medium": 0, "low": 6},
        "by_type": {"automate_manual_step": 5, "complete_partial_automation": 1},
    }

    for rec in result["recommendations"]:
        assert rec["confidence"] == "low"
        assert rec["scores"]["impact"] == 0.0


# ============================================================
# ASSEMBLY, FILTERS, METHOD
# ============================================================

def test_recommendations_are_sorted_unique_and_complete():

    result = recommend(new_threads(30) + new_threads(6, fail_node="rag") + approval_runs(8))

    recs = result["recommendations"]

    assert [r["score"] for r in recs] == sorted((r["score"] for r in recs), reverse=True)
    assert len({r["recommendation_id"] for r in recs}) == len(recs)

    for rec in recs:
        assert set(rec) == {
            "recommendation_id", "type", "title", "summary", "target", "priority",
            "score", "scores", "confidence", "effort", "rationale", "metrics",
            "suggested_actions", "evidence", "requires_human_decision",
        }
        assert rec["rationale"] and rec["suggested_actions"]
        assert rec["recommendation_id"] == f"{rec['type']}:{rec['target']['key']}"
        assert 0 <= rec["score"] <= 100


def test_service_filters_by_priority_and_summarises():

    start = datetime.now(UTC)

    with TestClient(app) as client:
        run_real_workflows(client)

    end = datetime.now(UTC)

    everything = recommend_automation(since=start, until=end)

    assert everything["basis"]["window"]["workflows_analyzed"] == 6
    assert everything["basis"]["outcomes"]["completed"] == 3
    assert everything["summary"]["total"] == len(everything["recommendations"])
    assert sum(everything["summary"]["by_priority"].values()) == everything["summary"]["total"]
    assert "Only 6 run(s) were analysed; confidence is low." in everything["warnings"]

    medium = recommend_automation(since=start, until=end, min_priority="medium")

    assert medium["summary"]["total"] < everything["summary"]["total"]
    assert all(r["priority"] in ("medium", "high") for r in medium["recommendations"])
    assert medium["summary"]["by_priority"]["low"] == 0

    # Real data: employee lookup failed once in six runs (17%).
    failure = by_id(everything)["reduce_step_failures:employee"]

    assert failure["metrics"]["failures"] == 1

    # Step 8 applies to the one claim above $5,000.
    partial = by_id(everything)[f"complete_partial_automation:{step_key(8)}"]

    assert partial["metrics"]["workflows_affected"] == 1
    assert partial["metrics"]["affected_share"] == pytest.approx(1 / 6, abs=0.001)


def test_method_describes_the_rubric():

    method = recommend_automation(
        since=datetime(2099, 1, 1, tzinfo=UTC), until=datetime(2099, 1, 2, tzinfo=UTC)
    )["method"]

    assert method["formula"] == (
        "score = 100 * (0.5 * impact + 0.3 * feasibility + 0.2 * confidence)"
    )
    assert method["weights"] == reco.WEIGHTS
    assert method["priority_thresholds"] == {"high": 70.0, "medium": 45.0}
    assert method["volume_full_scale"] == 1000
    assert any("nothing is changed or executed" in note for note in method["notes"])


def test_invalid_arguments():

    with pytest.raises(ValueError, match="min_priority must be one of"):
        recommend_automation(min_priority="urgent")

    with pytest.raises(ValueError, match="limit must be between"):
        recommend_automation(limit=0)

    with pytest.raises(ValueError, match="until must not be before since"):
        recommend_automation(since=datetime(2030, 1, 2, tzinfo=UTC), until=datetime(2030, 1, 1, tzinfo=UTC))


def test_recommending_changes_nothing():

    def counts():
        with session_scope() as session:
            return tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (AuditEvent, ContextNode, ContextEdge, WorkflowRun, Reimbursement)
            )

    new_threads(5, approval="approve")

    before = counts()

    recommend_automation()
    recommend_automation(min_priority="high")

    assert counts() == before


# ============================================================
# API
# ============================================================

def iso(moment: datetime) -> str:

    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_api_returns_ranked_recommendations():

    start = datetime.now(UTC)

    with TestClient(app) as client:

        run_real_workflows(client)

        end = datetime.now(UTC)

        response = client.get(
            "/automation/recommendations", params={"since": iso(start), "until": iso(end)}
        )

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {"basis", "summary", "recommendations", "warnings", "method"}
    assert body["basis"]["process"] == "process:expense_reimbursement"
    assert body["basis"]["window"]["workflows_analyzed"] == 6

    scores = [r["score"] for r in body["recommendations"]]

    assert scores == sorted(scores, reverse=True)

    first = body["recommendations"][0]

    assert first["type"] in {
        "automate_manual_step", "complete_partial_automation",
        "reduce_approval_wait", "reduce_step_failures",
    }
    assert set(first["scores"]) == {"impact", "feasibility", "confidence"}
    assert set(first["target"]) == {"kind", "key", "label", "order"}

    # The service and the API agree.
    assert body == recommend_automation(since=start, until=end)


def test_api_priority_filter():

    start = datetime.now(UTC)

    with TestClient(app) as client:

        run_real_workflows(client)

        end = datetime.now(UTC)

        window = {"since": iso(start), "until": iso(end)}

        everything = client.get("/automation/recommendations", params=window).json()
        medium = client.get(
            "/automation/recommendations", params=window | {"min_priority": "medium"}
        ).json()
        high = client.get(
            "/automation/recommendations", params=window | {"min_priority": "high"}
        ).json()

    assert everything["summary"]["total"] > medium["summary"]["total"] >= high["summary"]["total"]
    assert all(r["priority"] == "high" for r in high["recommendations"])


@pytest.mark.parametrize(
    "params",
    [
        {"min_priority": "urgent"},
        {"limit": 0},
        {"limit": 5001},
        {"since": "yesterday"},
        {"until": "2030-13-45"},
    ],
)
def test_api_rejects_invalid_parameters(params):

    with TestClient(app) as client:

        assert client.get("/automation/recommendations", params=params).status_code == 422


def test_api_rejects_a_window_that_ends_before_it_starts():

    with TestClient(app) as client:

        response = client.get("/automation/recommendations", params={
            "since": "2030-01-02T00:00:00Z", "until": "2030-01-01T00:00:00Z",
        })

    assert response.status_code == 422
    assert response.json() == {"detail": "until must not be before since."}


def test_endpoint_is_documented():

    with TestClient(app) as client:

        paths = client.get("/openapi.json").json()["paths"]

    schema = paths["/automation/recommendations"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]

    assert schema["$ref"].endswith("AutomationRecommendationsResponse")
