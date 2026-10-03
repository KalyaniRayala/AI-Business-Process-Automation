"""
Question parsing.

Every downstream step depends on pulling the employee id and the
expense amount out of free text: a missed amount sends the workflow
to recovery, and a wrong amount applies the wrong approval rule.
The parsing is regex and float() based, so this suite is where its
limits are written down.

Deterministic: no model, no database.
"""

from __future__ import annotations

from app.evals.scorers import exact_match, numeric_match
from app.evals.types import CaseResult, EvalContext, Suite, combine


def run_case(case: dict, context: EvalContext) -> CaseResult:

    from app.services.context_rag_service import (
        extract_employee_id,
        extract_expense_amount,
    )

    question = case["question"]

    employee_score = exact_match(
        extract_employee_id(question),
        case.get("expected_employee_id"),
        label="employee_id",
    )

    amount_score = numeric_match(
        extract_expense_amount(question),
        case.get("expected_amount"),
        label="amount",
    )

    score = combine([employee_score, amount_score])

    return CaseResult(
        case_id=case["id"],
        score=score.value,
        passed=score.passed,
        detail=score.detail,
        known_gap=bool(case.get("known_gap")),
    )


SUITE = Suite(
    name="extraction",
    description="Employee id and expense amount parsed from the question",
    dataset="extraction",
    run_case=run_case,
    threshold=1.0,
)
