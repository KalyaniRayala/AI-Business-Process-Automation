"""Result and configuration types shared by every eval suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal

from app.evals.cassettes import ModelGateway


# Offline  : deterministic suites only; model suites are skipped.
# Replay   : model suites answer from recorded cassettes.
# Live     : model suites call Cohere (costs money, needs a real key).
Mode = Literal["offline", "replay", "live"]


# ============================================================
# SCORES
# ============================================================

@dataclass(frozen=True)
class Score:
    """
    One measurement: `value` in 0..1, with a short human-readable
    explanation used in the failure report.
    """

    value: float
    detail: str = ""

    def __post_init__(self) -> None:

        if not 0.0 <= self.value <= 1.0:
            raise ValueError(
                f"Score value must be between 0 and 1, got {self.value}."
            )

    @property
    def passed(self) -> bool:
        """A score counts as a pass only when it is perfect."""

        return self.value == 1.0


def combine(scores: list[Score]) -> Score:
    """Mean of several measurements, keeping the failing details."""

    if not scores:
        raise ValueError("Cannot combine an empty list of scores.")

    value = sum(score.value for score in scores) / len(scores)

    failures = [
        score.detail
        for score in scores
        if not score.passed and score.detail
    ]

    return Score(value, "; ".join(failures))


# ============================================================
# CASES
# ============================================================

@dataclass(frozen=True)
class CaseResult:
    """The outcome of scoring one dataset case."""

    case_id: str
    score: float
    passed: bool
    detail: str = ""
    skipped: bool = False
    skip_reason: str = ""

    # A case that documents a behaviour we know is wrong. It is
    # reported but kept out of the gate, so the dataset can record
    # the gap without blocking every run.
    known_gap: bool = False

    @property
    def counts_towards_gate(self) -> bool:

        return not self.skipped and not self.known_gap

    def to_dict(self) -> dict:

        return {
            "case_id": self.case_id,
            "score": round(self.score, 4),
            "passed": self.passed,
            "detail": self.detail,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "known_gap": self.known_gap,
        }


# ============================================================
# SUITES
# ============================================================

@dataclass(frozen=True)
class EvalContext:
    """What a suite needs to know about the run it is part of."""

    mode: Mode = "offline"

    # The only way a suite may reach Cohere. None in offline mode.
    gateway: ModelGateway | None = None

    @property
    def model_available(self) -> bool:
        """Whether suites that need Cohere can produce a result."""

        return self.gateway is not None


@dataclass(frozen=True)
class Suite:
    """
    A named set of cases scored the same way.

    `run_case` returns the result for one dataset case; the runner
    handles loading, aggregation and gating.
    """

    name: str
    description: str
    dataset: str
    run_case: Callable[[dict, EvalContext], CaseResult]

    # Mean score the suite must reach for the gate to pass.
    threshold: float = 1.0

    # True when the suite calls Cohere (directly or through RAG).
    requires_model: bool = False


@dataclass(frozen=True)
class SuiteResult:
    """Aggregated results for one suite."""

    name: str
    description: str
    threshold: float
    results: list[CaseResult] = field(default_factory=list)
    error: str = ""

    @property
    def scored(self) -> list[CaseResult]:

        return [result for result in self.results if result.counts_towards_gate]

    @property
    def skipped(self) -> list[CaseResult]:

        return [result for result in self.results if result.skipped]

    @property
    def known_gaps(self) -> list[CaseResult]:

        return [
            result
            for result in self.results
            if result.known_gap and not result.skipped
        ]

    @property
    def failures(self) -> list[CaseResult]:

        return [result for result in self.scored if not result.passed]

    @property
    def mean_score(self) -> float:
        """Mean score of the cases that count towards the gate."""

        scored = self.scored

        if not scored:
            return 0.0

        return sum(result.score for result in scored) / len(scored)

    @property
    def pass_rate(self) -> float:

        scored = self.scored

        if not scored:
            return 0.0

        return sum(1 for result in scored if result.passed) / len(scored)

    @property
    def gate_passed(self) -> bool:
        """
        A suite passes when it ran without error and its mean score
        reaches the threshold. A suite with nothing to score (every
        case skipped) passes: skipping is reported separately, and a
        missing model must not look like a quality failure.
        """

        if self.error:
            return False

        if not self.scored:
            return True

        # Tolerate float accumulation, not a genuine shortfall.
        return self.mean_score >= self.threshold - 1e-9

    def to_dict(self) -> dict:

        return {
            "name": self.name,
            "description": self.description,
            "threshold": self.threshold,
            "error": self.error,
            "cases": len(self.results),
            "scored": len(self.scored),
            "skipped": len(self.skipped),
            "known_gaps": len(self.known_gaps),
            "failed": len(self.failures),
            "mean_score": round(self.mean_score, 4),
            "pass_rate": round(self.pass_rate, 4),
            "gate_passed": self.gate_passed,
            "results": [result.to_dict() for result in self.results],
        }


# ============================================================
# REPORT
# ============================================================

@dataclass(frozen=True)
class EvalReport:
    """Everything one `python -m app.evals run` produced."""

    mode: Mode
    suites: list[SuiteResult]
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    finished_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def gate_passed(self) -> bool:

        return all(suite.gate_passed for suite in self.suites)

    @property
    def duration_seconds(self) -> float:

        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict:

        return {
            "mode": self.mode,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "gate_passed": self.gate_passed,
            "suites": [suite.to_dict() for suite in self.suites],
        }
