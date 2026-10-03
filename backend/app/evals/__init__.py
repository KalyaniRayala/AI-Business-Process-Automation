"""
Evals: dataset-driven quality measurement for the AI-dependent parts
of the platform.

The pytest suite stubs the LLM and the retriever, so it proves the
plumbing works but says nothing about answer or retrieval quality.
These evals score behaviour against labelled datasets instead:

- deterministic suites (extraction, policy, routing) need nothing but
  the code, so they run anywhere, including CI,
- model suites (retrieval, grounding) need Cohere, so they either
  replay recorded responses or run live with `--live`.

Run with `python -m app.evals` from `backend/`.
"""

from app.evals.types import (
    CaseResult,
    EvalContext,
    EvalReport,
    Score,
    Suite,
    SuiteResult,
)


__all__ = [
    "CaseResult",
    "EvalContext",
    "EvalReport",
    "Score",
    "Suite",
    "SuiteResult",
]
