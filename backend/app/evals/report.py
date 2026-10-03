"""Rendering an EvalReport as text, Markdown or JSON."""

from __future__ import annotations

import json

from app.evals.types import EvalReport, SuiteResult


def percent(value: float) -> str:

    return f"{value * 100:.1f}%"


def score_text(suite: SuiteResult) -> str:
    """A suite with nothing scored has no score, not a score of zero."""

    return percent(suite.mean_score) if suite.scored else "n/a"


def suite_status(suite: SuiteResult) -> str:

    if suite.error:
        return "ERROR"

    if not suite.scored:
        return "SKIPPED"

    return "PASS" if suite.gate_passed else "FAIL"


# ============================================================
# TERMINAL
# ============================================================

def to_text(report: EvalReport) -> str:
    """Console summary: one line per suite, then the problems."""

    lines = [
        f"Evals ({report.mode} mode) in {report.duration_seconds:.2f}s",
        "",
    ]

    for suite in report.suites:

        lines.append(
            f"  [{suite_status(suite):>7}] {suite.name:<12} "
            f"score {score_text(suite):>6}  "
            f"(threshold {percent(suite.threshold)}, "
            f"{len(suite.scored)} scored, "
            f"{len(suite.failures)} failed, "
            f"{len(suite.skipped)} skipped, "
            f"{len(suite.known_gaps)} known gaps)"
        )

        if suite.error:
            lines.append(f"            {suite.error}")

    detail_lines: list[str] = []

    for suite in report.suites:

        for result in suite.failures:
            detail_lines.append(
                f"  FAIL  {suite.name}/{result.case_id}: {result.detail}"
            )

        for result in suite.known_gaps:

            if not result.passed:
                detail_lines.append(
                    f"  GAP   {suite.name}/{result.case_id}: {result.detail}"
                )

        # One line per suite is enough for skips: they share a reason.
        if suite.skipped:
            reason = suite.skipped[0].skip_reason
            detail_lines.append(
                f"  SKIP  {suite.name}: {len(suite.skipped)} case(s) - {reason}"
            )

    if detail_lines:
        lines.extend(["", *detail_lines])

    lines.extend(
        ["", "Gate: " + ("PASSED" if report.gate_passed else "FAILED")]
    )

    return "\n".join(lines)


# ============================================================
# MARKDOWN
# ============================================================

def to_markdown(report: EvalReport) -> str:

    lines = [
        "# Eval report",
        "",
        f"- Mode: `{report.mode}`",
        f"- Finished: {report.finished_at.isoformat()}",
        f"- Duration: {report.duration_seconds:.2f}s",
        f"- Gate: **{'passed' if report.gate_passed else 'failed'}**",
        "",
        "| Suite | Status | Score | Threshold | Scored | Failed | Skipped | Known gaps |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for suite in report.suites:

        lines.append(
            f"| {suite.name} | {suite_status(suite)} "
            f"| {score_text(suite)} | {percent(suite.threshold)} "
            f"| {len(suite.scored)} | {len(suite.failures)} "
            f"| {len(suite.skipped)} | {len(suite.known_gaps)} |"
        )

    for suite in report.suites:

        noteworthy = [
            result
            for result in suite.results
            if not result.passed and not result.skipped
        ]

        if not noteworthy and not suite.error:
            continue

        lines.extend(["", f"## {suite.name}", ""])

        if suite.error:
            lines.extend([f"Dataset error: {suite.error}", ""])

        for result in noteworthy:

            kind = "known gap" if result.known_gap else "failed"

            lines.append(
                f"- `{result.case_id}` ({kind}, score "
                f"{percent(result.score)}): {result.detail}"
            )

    return "\n".join(lines) + "\n"


# ============================================================
# JSON
# ============================================================

def to_json(report: EvalReport) -> str:

    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
