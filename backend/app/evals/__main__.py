"""
Command line for the evals. Run from `backend/`:

    python -m app.evals list
    python -m app.evals run
    python -m app.evals run --suite policy --suite routing
    python -m app.evals run --replay
    python -m app.evals run --live --record
    python -m app.evals run --json ../eval-report.json --markdown ../eval-report.md

The process exits 1 when the gate fails, so CI can use it directly.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.evals.datasets import DatasetError, load_dataset
from app.evals.report import to_json, to_markdown, to_text
from app.evals.runner import default_cassette_path, run_evals
from app.evals.suites import ALL_SUITES, SUITES_BY_NAME


def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog="python -m app.evals",
        description="Quality evaluations for the AI parts of the platform.",
    )

    commands = parser.add_subparsers(dest="command")

    commands.add_parser("list", help="Show the available suites.")

    run = commands.add_parser("run", help="Run suites and print a report.")

    run.add_argument(
        "--suite",
        action="append",
        choices=sorted(SUITES_BY_NAME),
        help="Suite to run (repeatable). Default: all of them.",
    )

    mode = run.add_mutually_exclusive_group()

    mode.add_argument(
        "--replay",
        action="store_true",
        help="Answer model suites from the cassette instead of skipping them.",
    )

    mode.add_argument(
        "--live",
        action="store_true",
        help="Call Cohere for the model suites. Uses your API quota.",
    )

    run.add_argument(
        "--record",
        action="store_true",
        help="With --live, save the responses to the cassette.",
    )

    run.add_argument(
        "--cassette",
        type=Path,
        help=f"Cassette file. Default: {default_cassette_path()}",
    )

    run.add_argument("--json", type=Path, help="Write the JSON report here.")

    run.add_argument(
        "--markdown", type=Path, help="Write the Markdown report here."
    )

    run.add_argument(
        "--quiet",
        action="store_true",
        help="Print nothing; use the exit code.",
    )

    return parser


def command_list() -> int:

    print("Available eval suites:\n")

    for suite in ALL_SUITES:

        try:
            cases = str(len(load_dataset(suite.dataset)))

        except DatasetError:
            cases = "dataset missing"

        needs = "Cohere" if suite.requires_model else "offline"

        print(f"  {suite.name:<12} {suite.description}")
        print(
            f"  {'':<12} {cases} cases, {needs}, "
            f"threshold {suite.threshold * 100:.0f}%\n"
        )

    return 0


def command_run(arguments: argparse.Namespace) -> int:

    if arguments.record and not arguments.live:
        print(
            "--record only applies to --live runs.",
            file=sys.stderr,
        )
        return 2

    mode = "live" if arguments.live else "replay" if arguments.replay else "offline"

    report = run_evals(
        suite_names=arguments.suite,
        mode=mode,
        cassette_path=arguments.cassette,
        record=arguments.record,
    )

    if arguments.json:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(to_json(report), encoding="utf-8")

    if arguments.markdown:
        arguments.markdown.parent.mkdir(parents=True, exist_ok=True)
        arguments.markdown.write_text(to_markdown(report), encoding="utf-8")

    if not arguments.quiet:
        print(to_text(report))

    return 0 if report.gate_passed else 1


def main(argv: list[str] | None = None) -> int:

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = build_parser()

    arguments = parser.parse_args(argv)

    if arguments.command == "list":
        return command_list()

    if arguments.command == "run":
        return command_run(arguments)

    parser.print_help()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
