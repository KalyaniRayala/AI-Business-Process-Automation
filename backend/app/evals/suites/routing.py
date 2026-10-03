"""
Workflow routing.

Two decisions in the graph change what happens to a request: whether
partial tool results send it to recovery, and where an approval
decision sends it next. A wrong route either loses a valid request
or pays out an expense that nobody approved, so both are scored
against an explicit table of states.

Each case names the decision it exercises:

    "check": "results"          -> check_results(state)
    "check": "approval_router"  -> approval_router(state)

Deterministic: no model. Importing the workflow graph does open the
configured checkpointer, so point DATABASE_URL at SQLite to run this
suite without PostgreSQL.
"""

from __future__ import annotations

from app.evals.scorers import exact_match
from app.evals.types import CaseResult, EvalContext, Suite


CHECKS = ("results", "approval_router")


def run_case(case: dict, context: EvalContext) -> CaseResult:

    from app.services.agent_graph import approval_router, check_results

    check = case.get("check", "results")

    if check not in CHECKS:
        raise ValueError(
            f"Case {case['id']}: unknown check {check!r}, "
            f"expected one of {CHECKS}."
        )

    decide = check_results if check == "results" else approval_router

    score = exact_match(
        decide(case["state"]),
        case["expected_route"],
        label="route",
    )

    return CaseResult(
        case_id=case["id"],
        score=score.value,
        passed=score.passed,
        detail=score.detail,
        known_gap=bool(case.get("known_gap")),
    )


SUITE = Suite(
    name="routing",
    description="Recovery routing and the path taken after a decision",
    dataset="routing",
    run_case=run_case,
    threshold=1.0,
)
