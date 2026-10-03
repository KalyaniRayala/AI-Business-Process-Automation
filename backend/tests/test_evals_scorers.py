"""The scoring functions: every score the report shows comes from these."""

import pytest

from app.evals.scorers import (
    exact_match,
    forbidden_absent,
    is_refusal,
    keyword_coverage,
    numeric_match,
    rank_of_first_hit,
    refusal_match,
    set_match,
    snippet_recall,
    text_match,
)
from app.evals.types import Score, combine


# ============================================================
# SCORE
# ============================================================

def test_only_a_perfect_score_passes():

    assert Score(1.0).passed is True
    assert Score(0.999).passed is False
    assert Score(0.0).passed is False


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_score_must_be_a_fraction(value):

    with pytest.raises(ValueError):
        Score(value)


def test_combine_averages_and_keeps_failing_details():

    combined = combine([Score(1.0), Score(0.0, "amount wrong")])

    assert combined.value == 0.5
    assert combined.detail == "amount wrong"


def test_combine_rejects_an_empty_list():

    with pytest.raises(ValueError):
        combine([])


# ============================================================
# VALUE SCORERS
# ============================================================

def test_exact_match_treats_none_as_a_value():

    assert exact_match(None, None).passed is True
    assert exact_match("E101", None).passed is False
    assert exact_match(None, "E101").passed is False


def test_exact_match_detail_names_both_values():

    detail = exact_match("E102", "E101", label="employee_id").detail

    assert "employee_id" in detail
    assert "E101" in detail and "E102" in detail


def test_text_match_ignores_case_and_spacing():

    assert text_match("No  approval required.", "no approval required.").passed


def test_numeric_match_uses_a_tolerance():

    assert numeric_match(0.1 + 0.2, 0.3).passed is True
    assert numeric_match(500.0, 500).passed is True
    assert numeric_match(500.01, 500).passed is False


def test_numeric_match_requires_none_on_both_sides():

    assert numeric_match(None, None).passed is True
    assert numeric_match(None, 500).passed is False
    assert numeric_match(2024.0, 300).passed is False


def test_set_match_ignores_order_and_case():

    assert set_match(["Finance", "manager"], ["manager", "finance"]).passed


def test_set_match_gives_partial_credit_for_an_overlap():

    score = set_match(["manager"], ["manager", "finance"])

    assert score.value == pytest.approx(0.5)
    assert "missing ['finance']" in score.detail


def test_set_match_reports_an_unexpected_approver():

    score = set_match(["manager", "director"], ["manager"])

    assert score.passed is False
    assert "unexpected ['director']" in score.detail


def test_two_empty_collections_match():

    assert set_match([], []).passed is True


# ============================================================
# TEXT SCORERS
# ============================================================

def test_keyword_coverage_scores_the_fraction_present():

    score = keyword_coverage("Manager approval is needed.", ["manager", "finance"])

    assert score.value == pytest.approx(0.5)
    assert "finance" in score.detail


def test_keyword_coverage_with_no_requirements_passes():

    assert keyword_coverage("anything", []).passed is True


def test_forbidden_absent_catches_an_invented_approver():

    score = forbidden_absent("The director must sign off.", ["director"])

    assert score.passed is False
    assert "director" in score.detail


def test_forbidden_absent_passes_when_nothing_is_present():

    assert forbidden_absent("Manager approval.", ["director"]).passed is True


@pytest.mark.parametrize(
    "answer",
    [
        "I could not find this information in the provided documents.",
        "I'm sorry, but I could not find this information in the documents.",
        "That is not in the provided documents.",
    ],
)
def test_refusals_are_recognised(answer):

    assert is_refusal(answer) is True


def test_a_real_answer_is_not_a_refusal():

    assert is_refusal("Expenses above $500 require manager approval.") is False


def test_refusal_match_both_ways():

    refusal = "I could not find this information in the provided documents."

    assert refusal_match(refusal, True).passed is True
    assert refusal_match("Manager approval.", False).passed is True

    assert "expected a refusal" in refusal_match("Manager approval.", True).detail
    assert "refused" in refusal_match(refusal, False).detail


# ============================================================
# RETRIEVAL SCORERS
# ============================================================

def test_snippet_recall_searches_every_chunk():

    score = snippet_recall(
        ["Step 1. Submit a claim.", "7. Expenses above $500 require manager approval."],
        ["expenses above $500 require manager approval"],
    )

    assert score.passed is True


def test_snippet_recall_scores_a_partial_hit():

    score = snippet_recall(
        ["7. Expenses above $500 require manager approval."],
        [
            "Expenses above $500 require manager approval",
            "Expenses above $5,000 require finance approval",
        ],
    )

    assert score.value == pytest.approx(0.5)
    assert "finance" in score.detail


def test_rank_of_first_hit_counts_from_one():

    chunks = ["unrelated", "the receipt is required", "also unrelated"]

    assert rank_of_first_hit(chunks, ["receipt is required"]) == 2
    assert rank_of_first_hit(chunks, ["nothing like this"]) is None
