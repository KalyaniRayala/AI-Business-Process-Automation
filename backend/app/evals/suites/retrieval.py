"""
Retrieval quality.

Scores what ChromaDB returns for a question: recall of the policy
sentences an answer would need. A retrieval miss is invisible in the
unit tests, because they stub the search, but it is the most common
reason for a wrong or refused answer in production.

Needs Cohere embeddings and the ingested documents, so it runs from
a cassette (`--replay`) or live (`--live`). The chunks it scores
come from whatever is in the Chroma store, so a live run measures
the retrieval you actually have.
"""

from __future__ import annotations

from app.evals.cassettes import MissingRecording
from app.evals.scorers import rank_of_first_hit, snippet_recall
from app.evals.types import CaseResult, EvalContext, Suite


DEFAULT_TOP_K = 3


def run_case(case: dict, context: EvalContext) -> CaseResult:

    if context.gateway is None:
        return CaseResult(
            case_id=case["id"],
            score=0.0,
            passed=False,
            skipped=True,
            skip_reason="needs Cohere: run with --replay or --live",
        )

    top_k = int(case.get("top_k", DEFAULT_TOP_K))

    try:
        chunks = context.gateway.retrieve(case["query"], top_k)

    except MissingRecording as error:
        return CaseResult(
            case_id=case["id"],
            score=0.0,
            passed=False,
            skipped=True,
            skip_reason=str(error),
        )

    expected = case.get("must_retrieve", [])

    score = snippet_recall(chunks, expected)

    rank = rank_of_first_hit(chunks, expected)

    detail = score.detail

    if score.passed and rank is not None and rank > 1:
        detail = f"found at rank {rank} of {len(chunks)}"

    return CaseResult(
        case_id=case["id"],
        score=score.value,
        passed=score.passed,
        detail=detail,
        known_gap=bool(case.get("known_gap")),
    )


SUITE = Suite(
    name="retrieval",
    description="Recall of the policy text a question needs",
    dataset="retrieval",
    run_case=run_case,
    threshold=1.0,
    requires_model=True,
)
