"""
The suite registry.

Each module exposes a `SUITE`. Importing a suite module must not
import the Cohere-backed services, so `python -m app.evals list`
works without a key; those imports happen inside the scorers.
"""

from app.evals.suites import extraction, grounding, policy, retrieval, routing
from app.evals.types import Suite


ALL_SUITES: tuple[Suite, ...] = (
    extraction.SUITE,
    policy.SUITE,
    routing.SUITE,
    retrieval.SUITE,
    grounding.SUITE,
)

SUITES_BY_NAME: dict[str, Suite] = {suite.name: suite for suite in ALL_SUITES}


def get_suite(name: str) -> Suite:

    try:
        return SUITES_BY_NAME[name]

    except KeyError:
        known = ", ".join(SUITES_BY_NAME)

        raise KeyError(
            f"Unknown suite {name!r}. Available suites: {known}."
        ) from None


__all__ = ["ALL_SUITES", "SUITES_BY_NAME", "get_suite"]
