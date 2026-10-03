"""
Aggregation, gating and reporting: what turns case results into a
pass or a fail, and what the report says about it.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.evals import report as report_module
from app.evals.cassettes import Cassette
from app.evals.runner import build_context, default_cassette_path, run_evals, run_suite
from app.evals.types import CaseResult, EvalContext, EvalReport, Suite, SuiteResult


def case(case_id="c", score=1.0, **kwargs):

    return CaseResult(
        case_id=case_id,
        score=score,
        passed=kwargs.pop("passed", score == 1.0),
        **kwargs,
    )


def suite_result(results, threshold=1.0, error=""):

    return SuiteResult(
        name="demo",
        description="a demo suite",
        threshold=threshold,
        results=results,
        error=error,
    )


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    """A temporary EVALS_DIR holding one dataset the tests control."""

    monkeypatch.setenv("EVALS_DIR", str(tmp_path))

    directory = tmp_path / "datasets"
    directory.mkdir()

    def write(name, cases):
        (directory / f"{name}.json").write_text(
            json.dumps({"cases": cases}), encoding="utf-8"
        )

    return write


# ============================================================
# AGGREGATION
# ============================================================

def test_mean_and_pass_rate_ignore_skipped_and_known_gaps():

    result = suite_result(
        [
            case("a", 1.0),
            case("b", 0.5),
            case("c", 0.0, skipped=True),
            case("d", 0.0, known_gap=True),
        ]
    )

    assert [item.case_id for item in result.scored] == ["a", "b"]
    assert result.mean_score == pytest.approx(0.75)
    assert result.pass_rate == pytest.approx(0.5)
    assert [item.case_id for item in result.failures] == ["b"]


def test_a_suite_with_nothing_scored_passes_the_gate():

    result = suite_result([case("a", 0.0, skipped=True)])

    assert result.scored == []
    assert result.mean_score == 0.0
    assert result.gate_passed is True


def test_a_dataset_error_fails_the_gate():

    assert suite_result([], error="Dataset not found").gate_passed is False


def test_the_gate_compares_against_the_threshold():

    results = [case("a", 1.0), case("b", 0.8, passed=False)]

    assert suite_result(results, threshold=0.9).gate_passed is True
    assert suite_result(results, threshold=0.95).gate_passed is False


def test_a_score_exactly_on_the_threshold_passes():

    results = [case("a", 1.0), case("b", 0.8, passed=False)]

    assert suite_result(results, threshold=0.9).mean_score == pytest.approx(0.9)
    assert suite_result(results, threshold=0.9).gate_passed is True


def test_a_known_gap_that_was_skipped_is_not_counted_twice():

    result = suite_result([case("a", 0.0, skipped=True, known_gap=True)])

    assert result.known_gaps == []
    assert len(result.skipped) == 1


def test_the_report_fails_if_any_suite_fails():

    passing = suite_result([case("a", 1.0)])
    failing = suite_result([case("b", 0.0)])

    assert EvalReport(mode="offline", suites=[passing]).gate_passed is True
    assert EvalReport(mode="offline", suites=[passing, failing]).gate_passed is False


# ============================================================
# RUNNING A SUITE
# ============================================================

def test_run_suite_scores_every_case(dataset):

    dataset("demo", [{"id": "a"}, {"id": "b"}])

    suite = Suite(
        name="demo",
        description="",
        dataset="demo",
        run_case=lambda case_, context: CaseResult(case_["id"], 1.0, True),
    )

    result = run_suite(suite, EvalContext())

    assert [item.case_id for item in result.results] == ["a", "b"]
    assert result.gate_passed is True


def test_a_case_that_raises_fails_only_itself(dataset):

    dataset("demo", [{"id": "boom"}, {"id": "fine"}])

    def run_case(case_, context):

        if case_["id"] == "boom":
            raise RuntimeError("exploded")

        return CaseResult(case_["id"], 1.0, True)

    suite = Suite(name="demo", description="", dataset="demo", run_case=run_case)

    result = run_suite(suite, EvalContext())

    failed, passed = result.results

    assert failed.passed is False
    assert "RuntimeError: exploded" in failed.detail
    assert passed.passed is True


def test_a_missing_dataset_becomes_a_suite_error(dataset):

    suite = Suite(
        name="demo",
        description="",
        dataset="absent",
        run_case=lambda case_, context: CaseResult("x", 1.0, True),
    )

    result = run_suite(suite, EvalContext())

    assert "Dataset not found" in result.error
    assert result.gate_passed is False


def test_the_suite_sees_the_run_context(dataset):

    dataset("demo", [{"id": "a"}])

    seen = []

    suite = Suite(
        name="demo",
        description="",
        dataset="demo",
        run_case=lambda case_, context: (
            seen.append(context.mode) or CaseResult("a", 1.0, True)
        ),
    )

    run_suite(suite, EvalContext(mode="replay"))

    assert seen == ["replay"]


# ============================================================
# MODES
# ============================================================

def test_offline_has_no_gateway():

    context = build_context("offline")

    assert context.gateway is None
    assert context.model_available is False


def test_replay_needs_a_cassette():

    with pytest.raises(ValueError, match="needs a cassette"):
        build_context("replay")


def test_replay_uses_the_cassette(tmp_path):

    cassette = Cassette.load(tmp_path / "c.json")

    context = build_context("replay", cassette)

    assert context.model_available is True
    assert context.gateway.cassette is cassette


def test_live_records_only_when_asked(tmp_path):

    cassette = Cassette.load(tmp_path / "c.json")

    assert build_context("live", cassette, record=False).gateway.cassette is None
    assert build_context("live", cassette, record=True).gateway.cassette is cassette


def test_recording_outside_live_mode_is_refused():

    with pytest.raises(ValueError, match="only possible in live mode"):
        run_evals(mode="offline", record=True)


def test_the_default_cassette_sits_under_the_evals_folder(tmp_path, monkeypatch):

    monkeypatch.setenv("EVALS_DIR", str(tmp_path))

    assert default_cassette_path() == tmp_path / "cassettes" / "default.json"


# ============================================================
# A WHOLE RUN
# ============================================================

def test_run_evals_runs_the_named_suites_only():

    report = run_evals(suite_names=["policy"])

    assert [suite.name for suite in report.suites] == ["policy"]
    assert report.gate_passed is True


def test_run_evals_runs_everything_by_default():

    report = run_evals()

    assert [suite.name for suite in report.suites] == [
        "extraction",
        "policy",
        "routing",
        "retrieval",
        "grounding",
    ]


def test_an_unknown_suite_name_is_rejected():

    with pytest.raises(KeyError):
        run_evals(suite_names=["nope"])


def test_the_report_dictionary_is_json_serialisable():

    report = run_evals(suite_names=["policy"])

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["mode"] == "offline"
    assert payload["gate_passed"] is True
    assert payload["suites"][0]["name"] == "policy"
    assert payload["suites"][0]["cases"] == len(payload["suites"][0]["results"])


# ============================================================
# REPORT RENDERING
# ============================================================

def sample_report():

    started = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    return EvalReport(
        mode="offline",
        suites=[
            suite_result([case("good", 1.0)]),
            SuiteResult(
                name="other",
                description="another suite",
                threshold=1.0,
                results=[
                    case("bad", 0.0, detail="amount: expected 300, got 2024"),
                    case("gap", 0.0, known_gap=True, detail="not parsed"),
                    case("later", 0.0, skipped=True, skip_reason="needs Cohere"),
                ],
            ),
        ],
        started_at=started,
        finished_at=started + timedelta(seconds=2),
    )


def test_text_report_shows_every_suite_and_the_gate():

    text = report_module.to_text(sample_report())

    assert "demo" in text and "other" in text
    assert "FAIL  other/bad: amount: expected 300, got 2024" in text
    assert "GAP   other/gap" in text
    assert "SKIP  other: 1 case(s) - needs Cohere" in text
    assert text.strip().endswith("Gate: FAILED")


def test_text_report_is_ascii_so_any_console_can_print_it():

    report_module.to_text(sample_report()).encode("ascii")


def test_a_suite_with_no_scored_cases_shows_no_score():

    report = EvalReport(
        mode="offline",
        suites=[suite_result([case("x", 0.0, skipped=True, skip_reason="no key")])],
    )

    text = report_module.to_text(report)

    assert "n/a" in text
    assert "SKIPPED" in text
    assert text.strip().endswith("Gate: PASSED")


def test_markdown_report_has_a_row_per_suite():

    markdown = report_module.to_markdown(sample_report())

    assert markdown.startswith("# Eval report")
    assert "| demo | PASS |" in markdown
    assert "| other | FAIL |" in markdown
    assert "`bad` (failed" in markdown
    assert "`gap` (known gap" in markdown


def test_markdown_reports_a_dataset_error():

    report = EvalReport(
        mode="offline",
        suites=[suite_result([], error="Dataset not found: x.json")],
    )

    assert "Dataset error: Dataset not found" in report_module.to_markdown(report)


def test_json_report_round_trips():

    payload = json.loads(report_module.to_json(sample_report()))

    assert payload["gate_passed"] is False
    assert payload["duration_seconds"] == 2.0
