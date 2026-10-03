"""
Expense policy decisions.

`lookup_expense_policy` is the authoritative approval logic, and the
approval node reads its decision through `approval_is_required`.
This suite scores both together, on the amounts that matter: the
exact thresholds, the values just either side of them, and the
amounts the tool must reject.

Deterministic: no model, no database.
"""

from __future__ import annotations

from app.evals.scorers import exact_match, set_match, text_match
from app.evals.types import CaseResult, EvalContext, Suite, combine


# Approver roles named in the policy tool's wording.
KNOWN_APPROVERS = ("manager", "finance")


def approvers_in(approval_text: str) -> list[str]:
    """
    Roles the approval sentence asks for. The sentence is also what
    the approver sees, so it has to stay machine-readable.
    """

    lowered = str(approval_text).lower()

    if lowered.startswith("no approval"):
        return []

    return [role for role in KNOWN_APPROVERS if role in lowered]


def run_case(case: dict, context: EvalContext) -> CaseResult:

    from app.services.tool_service import (
        approval_is_required,
        lookup_expense_policy,
    )

    result = lookup_expense_policy(case["amount"])

    scores = [
        exact_match(
            result.get("success"),
            case["expected_success"],
            label="success",
        )
    ]

    if case["expected_success"]:

        scores.append(
            text_match(
                result.get("approval"),
                case["expected_approval"],
                label="approval",
            )
        )

        scores.append(
            set_match(
                approvers_in(result.get("approval", "")),
                case.get("expected_approvers", []),
                label="approvers",
            )
        )

        scores.append(
            exact_match(
                approval_is_required(result),
                case["expected_approval_required"],
                label="approval_required",
            )
        )

    else:

        # A rejected amount must never be read as "no approval
        # needed" and pass straight through to the business action.
        scores.append(
            exact_match(
                approval_is_required(result),
                False,
                label="approval_required",
            )
        )

    score = combine(scores)

    return CaseResult(
        case_id=case["id"],
        score=score.value,
        passed=score.passed,
        detail=score.detail,
        known_gap=bool(case.get("known_gap")),
    )


SUITE = Suite(
    name="policy",
    description="Approval thresholds and the approval gate they drive",
    dataset="policy",
    run_case=run_case,
    threshold=1.0,
)
