"""
Running suites and gating on the result.

The runner owns everything the suites should not care about:
loading datasets, catching failures, building the gateway for the
chosen mode, aggregating scores and deciding whether the run passes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from app.evals.cassettes import Cassette, LiveGateway, ReplayGateway
from app.evals.datasets import DatasetError, evals_dir, load_dataset
from app.evals.suites import ALL_SUITES, get_suite
from app.evals.types import (
    CaseResult,
    EvalContext,
    EvalReport,
    Mode,
    Suite,
    SuiteResult,
)


logger = logging.getLogger(__name__)


DEFAULT_CASSETTE = "default.json"


def default_cassette_path() -> Path:

    return evals_dir() / "cassettes" / DEFAULT_CASSETTE


# ============================================================
# ONE SUITE
# ============================================================

def run_suite(suite: Suite, context: EvalContext) -> SuiteResult:
    """
    Every case in one suite. A broken dataset fails the suite; a
    case that raises fails that case only, so one bad case never
    hides the rest of the run.
    """

    try:
        cases = load_dataset(suite.dataset)

    except DatasetError as error:

        return SuiteResult(
            name=suite.name,
            description=suite.description,
            threshold=suite.threshold,
            error=str(error),
        )

    results: list[CaseResult] = []

    for case in cases:

        try:
            results.append(suite.run_case(case, context))

        except Exception as error:

            logger.exception(
                "Eval case %s in suite %s raised",
                case.get("id"),
                suite.name,
            )

            results.append(
                CaseResult(
                    case_id=str(case.get("id", "<unknown>")),
                    score=0.0,
                    passed=False,
                    detail=f"{type(error).__name__}: {error}",
                )
            )

    return SuiteResult(
        name=suite.name,
        description=suite.description,
        threshold=suite.threshold,
        results=results,
    )


# ============================================================
# A WHOLE RUN
# ============================================================

def build_context(
    mode: Mode,
    cassette: Cassette | None = None,
    record: bool = False,
) -> EvalContext:
    """The gateway the chosen mode allows, if any."""

    if mode == "offline":
        return EvalContext(mode="offline", gateway=None)

    if mode == "replay":

        if cassette is None:
            raise ValueError("Replay mode needs a cassette.")

        return EvalContext(mode="replay", gateway=ReplayGateway(cassette))

    return EvalContext(
        mode="live",
        gateway=LiveGateway(cassette if record else None),
    )


def run_evals(
    suite_names: list[str] | None = None,
    mode: Mode = "offline",
    cassette_path: Path | str | None = None,
    record: bool = False,
) -> EvalReport:
    """
    Run the named suites (all of them by default) and report.

    `record` only applies to live mode, where it writes every
    response to the cassette so the same run can be replayed.
    """

    if record and mode != "live":
        raise ValueError("Recording is only possible in live mode.")

    suites = (
        [get_suite(name) for name in suite_names]
        if suite_names
        else list(ALL_SUITES)
    )

    cassette = None

    if mode in ("replay", "live"):
        cassette = Cassette.load(
            Path(cassette_path) if cassette_path else default_cassette_path()
        )

    context = build_context(mode, cassette, record)

    started_at = datetime.now(timezone.utc)

    results = [run_suite(suite, context) for suite in suites]

    finished_at = datetime.now(timezone.utc)

    if record and cassette is not None and cassette.dirty:
        cassette.save()
        logger.info("Recorded responses to %s", cassette.path)

    return EvalReport(
        mode=mode,
        suites=results,
        started_at=started_at,
        finished_at=finished_at,
    )
