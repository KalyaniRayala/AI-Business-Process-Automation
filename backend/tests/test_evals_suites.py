"""
The suites themselves: that each one scores the behaviour it claims
to, and that the datasets shipped with the project still describe
the code as it is today.
"""

from pathlib import Path

import pytest

from app.evals.cassettes import Cassette, MissingRecording, ReplayGateway
from app.evals.datasets import load_dataset
from app.evals.runner import run_suite
from app.evals.suites import extraction, get_suite, grounding, policy, retrieval, routing
from app.evals.suites.policy import approvers_in
from app.evals.types import EvalContext


OFFLINE = EvalContext(mode="offline")

DETERMINISTIC = (extraction.SUITE, policy.SUITE, routing.SUITE)
MODEL_SUITES = (retrieval.SUITE, grounding.SUITE)


class FakeGateway:
    """A gateway that returns whatever the test wants."""

    def __init__(self, answer_text="", chunks=()):
        self.answer_text = answer_text
        self.chunks = list(chunks)
        self.calls = []

    def answer(self, question, context):
        self.calls.append(("answer", question, context))
        return self.answer_text

    def retrieve(self, query, top_k):
        self.calls.append(("retrieve", query, top_k))
        return self.chunks


def replay_context(entries):
    """A replay context backed by an in-memory cassette."""

    cassette = Cassette(path=Path("unused.json"), entries=dict(entries))

    return EvalContext(mode="replay", gateway=ReplayGateway(cassette))


# ============================================================
# REGISTRY
# ============================================================

def test_every_suite_is_registered_under_its_own_name():

    for suite in DETERMINISTIC + MODEL_SUITES:
        assert get_suite(suite.name) is suite


def test_an_unknown_suite_lists_the_known_ones():

    with pytest.raises(KeyError, match="extraction"):
        get_suite("nope")


def test_only_the_model_suites_declare_they_need_cohere():

    assert [suite.name for suite in DETERMINISTIC if suite.requires_model] == []

    assert all(suite.requires_model for suite in MODEL_SUITES)


# ============================================================
# THE SHIPPED DATASETS AGAINST THE REAL CODE
# ============================================================

@pytest.mark.parametrize("suite", DETERMINISTIC, ids=lambda suite: suite.name)
def test_deterministic_suite_meets_its_gate(suite):

    result = run_suite(suite, OFFLINE)

    assert not result.error
    assert result.scored, "nothing was scored"
    assert result.gate_passed, [
        (failure.case_id, failure.detail) for failure in result.failures
    ]


@pytest.mark.parametrize("suite", DETERMINISTIC, ids=lambda suite: suite.name)
def test_known_gaps_still_fail(suite):
    """
    A gap that has been fixed must lose its marker, or the dataset
    would keep excusing behaviour that is now correct.
    """

    result = run_suite(suite, OFFLINE)

    fixed = [case.case_id for case in result.known_gaps if case.passed]

    assert not fixed, (
        f"{suite.name}: these cases pass now and should no longer be "
        f"marked known_gap: {fixed}"
    )


@pytest.mark.parametrize("suite", DETERMINISTIC, ids=lambda suite: suite.name)
def test_deterministic_suites_are_repeatable(suite):

    first = run_suite(suite, OFFLINE)
    second = run_suite(suite, OFFLINE)

    assert [case.to_dict() for case in first.results] == [
        case.to_dict() for case in second.results
    ]


@pytest.mark.parametrize("suite", MODEL_SUITES, ids=lambda suite: suite.name)
def test_model_suites_skip_without_a_gateway(suite):

    result = run_suite(suite, OFFLINE)

    assert len(result.skipped) == len(result.results)
    assert not result.scored

    # Skipping is not a quality failure.
    assert result.gate_passed is True
    assert "Cohere" in result.skipped[0].skip_reason


# ============================================================
# EXTRACTION
# ============================================================

def test_extraction_scores_both_fields_separately():

    case = {
        "id": "half-right",
        "question": "E101 spent 750.",
        "expected_employee_id": "E999",
        "expected_amount": 750,
    }

    result = extraction.SUITE.run_case(case, OFFLINE)

    assert result.score == pytest.approx(0.5)
    assert result.passed is False
    assert "employee_id" in result.detail


def test_extraction_passes_a_correct_case():

    case = {
        "id": "right",
        "question": "Can E101 claim 750?",
        "expected_employee_id": "E101",
        "expected_amount": 750,
    }

    assert extraction.SUITE.run_case(case, OFFLINE).passed is True


def test_extraction_marks_a_known_gap():

    case = {
        "id": "gap",
        "question": "E101 claimed from 2024 for 300 dollars.",
        "expected_employee_id": "E101",
        "expected_amount": 300,
        "known_gap": True,
    }

    result = extraction.SUITE.run_case(case, OFFLINE)

    assert result.passed is False
    assert result.known_gap is True
    assert result.counts_towards_gate is False


# ============================================================
# POLICY
# ============================================================

@pytest.mark.parametrize(
    "text, expected",
    [
        ("No approval required.", []),
        ("Manager approval required.", ["manager"]),
        ("Manager approval and finance approval required.", ["manager", "finance"]),
    ],
)
def test_approvers_are_read_from_the_policy_sentence(text, expected):

    assert approvers_in(text) == expected


def test_policy_case_passes_on_the_real_rules():

    case = {
        "id": "mid",
        "amount": 750,
        "expected_success": True,
        "expected_approval": "Manager approval required.",
        "expected_approvers": ["manager"],
        "expected_approval_required": True,
    }

    assert policy.SUITE.run_case(case, OFFLINE).passed is True


def test_policy_case_fails_on_a_wrong_expectation():

    case = {
        "id": "wrong",
        "amount": 750,
        "expected_success": True,
        "expected_approval": "Manager approval and finance approval required.",
        "expected_approvers": ["manager", "finance"],
        "expected_approval_required": True,
    }

    result = policy.SUITE.run_case(case, OFFLINE)

    assert result.passed is False
    assert "approval" in result.detail


def test_policy_scores_a_rejected_amount():

    case = {
        "id": "negative",
        "amount": -1,
        "expected_success": False,
        "expected_approval": None,
        "expected_approvers": [],
        "expected_approval_required": False,
    }

    assert policy.SUITE.run_case(case, OFFLINE).passed is True


def test_the_policy_dataset_covers_both_thresholds():

    amounts = {case["amount"] for case in load_dataset("policy")}

    assert {500, 500.01, 5000, 5000.01} <= amounts


# ============================================================
# ROUTING
# ============================================================

def test_routing_scores_check_results():

    case = {
        "id": "ok",
        "check": "results",
        "state": {
            "employee_data": {"success": True},
            "policy_result": {"success": True},
            "document_context": "something",
        },
        "expected_route": "approval",
    }

    assert routing.SUITE.run_case(case, OFFLINE).passed is True


def test_routing_scores_the_approval_router():

    case = {
        "id": "rejected",
        "check": "approval_router",
        "state": {"approval_status": "rejected"},
        "expected_route": "rejection",
    }

    assert routing.SUITE.run_case(case, OFFLINE).passed is True


def test_routing_defaults_to_check_results():

    case = {"id": "default", "state": {}, "expected_route": "recovery"}

    assert routing.SUITE.run_case(case, OFFLINE).passed is True


def test_routing_rejects_an_unknown_check():

    case = {"id": "bad", "check": "nonsense", "state": {}, "expected_route": "x"}

    with pytest.raises(ValueError, match="unknown check"):
        routing.SUITE.run_case(case, OFFLINE)


# ============================================================
# RETRIEVAL
# ============================================================

def test_retrieval_passes_when_the_snippet_comes_back():

    gateway = FakeGateway(
        chunks=["7. Expenses above $500 require manager approval."]
    )

    case = {
        "id": "hit",
        "query": "manager approval?",
        "top_k": 3,
        "must_retrieve": ["Expenses above $500 require manager approval"],
    }

    result = retrieval.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is True
    assert gateway.calls == [("retrieve", "manager approval?", 3)]


def test_retrieval_reports_the_missing_snippet():

    gateway = FakeGateway(chunks=["Something unrelated."])

    case = {
        "id": "miss",
        "query": "manager approval?",
        "must_retrieve": ["Expenses above $500 require manager approval"],
    }

    result = retrieval.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is False
    assert "did not return" in result.detail


def test_retrieval_notes_a_late_rank_even_when_it_passes():

    gateway = FakeGateway(chunks=["unrelated", "the receipt is required"])

    case = {
        "id": "late",
        "query": "receipt?",
        "must_retrieve": ["receipt is required"],
    }

    result = retrieval.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is True
    assert "rank 2" in result.detail


def test_retrieval_uses_the_default_top_k():

    gateway = FakeGateway(chunks=["anything"])

    retrieval.SUITE.run_case(
        {"id": "k", "query": "q"}, EvalContext(mode="live", gateway=gateway)
    )

    assert gateway.calls[0][2] == retrieval.DEFAULT_TOP_K


def test_retrieval_skips_a_case_with_no_recording():

    context = replay_context({})

    result = retrieval.SUITE.run_case({"id": "unrecorded", "query": "q"}, context)

    assert result.skipped is True
    assert "--live" in result.skip_reason


# ============================================================
# GROUNDING
# ============================================================

GROUNDING_CONTEXT = "7. Expenses above $500 require manager approval."


def test_grounding_passes_a_grounded_answer():

    gateway = FakeGateway(answer_text="Yes, manager approval is required.")

    case = {
        "id": "grounded",
        "question": "Do I need approval for $750?",
        "context": GROUNDING_CONTEXT,
        "must_include": ["manager"],
        "must_not_include": ["director"],
    }

    result = grounding.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is True


def test_grounding_catches_an_invented_approver():

    gateway = FakeGateway(answer_text="The director must approve this.")

    case = {
        "id": "hallucinated",
        "question": "Who approves?",
        "context": GROUNDING_CONTEXT,
        "must_include": ["manager"],
        "must_not_include": ["director"],
    }

    result = grounding.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is False
    assert "director" in result.detail


def test_grounding_requires_a_refusal_when_the_context_is_silent():

    gateway = FakeGateway(answer_text="The per diem is $75 per day.")

    case = {
        "id": "should-refuse",
        "question": "What is the per diem?",
        "context": GROUNDING_CONTEXT,
        "expect_refusal": True,
    }

    result = grounding.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is False
    assert "expected a refusal" in result.detail


def test_grounding_accepts_a_refusal_when_one_is_expected():

    gateway = FakeGateway(
        answer_text="I could not find this information in the provided documents."
    )

    case = {
        "id": "refused",
        "question": "What is the per diem?",
        "context": GROUNDING_CONTEXT,
        "expect_refusal": True,
        "must_not_include": ["per diem is"],
    }

    result = grounding.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is True


def test_grounding_does_not_require_facts_inside_a_refusal():
    """A refusal case must not also demand must_include phrases."""

    for case in load_dataset("grounding"):

        if case.get("expect_refusal"):
            assert "must_include" not in case, case["id"]


def test_grounding_fails_a_refusal_the_context_could_have_answered():

    gateway = FakeGateway(
        answer_text="I could not find this information in the provided documents."
    )

    case = {
        "id": "over-refused",
        "question": "Do I need approval for $750?",
        "context": GROUNDING_CONTEXT,
        "must_include": ["manager"],
    }

    result = grounding.SUITE.run_case(
        case, EvalContext(mode="live", gateway=gateway)
    )

    assert result.passed is False
    assert "refused" in result.detail


def test_grounding_skips_without_a_recording():

    result = grounding.SUITE.run_case(
        {"id": "unrecorded", "question": "q", "context": "c"},
        replay_context({}),
    )

    assert result.skipped is True


def test_replay_raises_for_an_unknown_request():

    gateway = replay_context({}).gateway

    with pytest.raises(MissingRecording):
        gateway.answer("q", "c")
