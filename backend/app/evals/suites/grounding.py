"""
Answer grounding.

The generation prompt makes two promises: answer only from the
provided context, and say so when the context does not contain the
answer. This suite holds the model to both. Each case supplies the
context itself, so a failure is the model's and not the retriever's.

Three things are scored per case:

- the facts the answer must state (`must_include`),
- the claims it must not make (`must_not_include`), which is how an
  invented threshold or approver is caught,
- whether it refuses (`expect_refusal`), for questions the context
  cannot answer.

Needs Cohere, so it runs from a cassette (`--replay`) or live
(`--live`).
"""

from __future__ import annotations

from app.evals.cassettes import MissingRecording
from app.evals.scorers import forbidden_absent, keyword_coverage, refusal_match
from app.evals.types import CaseResult, EvalContext, Suite, combine


def run_case(case: dict, context: EvalContext) -> CaseResult:

    if context.gateway is None:
        return CaseResult(
            case_id=case["id"],
            score=0.0,
            passed=False,
            skipped=True,
            skip_reason="needs Cohere: run with --replay or --live",
        )

    try:
        answer = context.gateway.answer(case["question"], case["context"])

    except MissingRecording as error:
        return CaseResult(
            case_id=case["id"],
            score=0.0,
            passed=False,
            skipped=True,
            skip_reason=str(error),
        )

    expect_refusal = bool(case.get("expect_refusal"))

    scores = [
        refusal_match(answer, expect_refusal),
        forbidden_absent(answer, case.get("must_not_include")),
    ]

    # A refusal has no facts to state, so requiring phrases in it
    # would contradict the refusal check.
    if not expect_refusal:
        scores.append(keyword_coverage(answer, case.get("must_include")))

    score = combine(scores)

    return CaseResult(
        case_id=case["id"],
        score=score.value,
        passed=score.passed,
        detail=score.detail,
        known_gap=bool(case.get("known_gap")),
    )


SUITE = Suite(
    name="grounding",
    description="Answers stay inside the context and refuse when it is silent",
    dataset="grounding",
    run_case=run_case,
    # Generation is not deterministic: one partially missed phrase
    # should show up in the report without failing the gate, while
    # a hallucination or a wrong refusal still does.
    threshold=0.9,
    requires_model=True,
)
