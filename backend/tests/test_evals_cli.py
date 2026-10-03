"""
The command line: CI depends on the exit code, so it is tested as
carefully as the scoring.
"""

import json

import pytest

from app.evals.__main__ import main


def test_list_shows_every_suite(capsys):

    assert main(["list"]) == 0

    output = capsys.readouterr().out

    for name in ("extraction", "policy", "routing", "retrieval", "grounding"):
        assert name in output

    assert "cases" in output


def test_no_command_prints_help(capsys):

    assert main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_run_passes_offline(capsys):

    assert main(["run", "--suite", "policy"]) == 0

    assert "Gate: PASSED" in capsys.readouterr().out


def test_run_is_silent_when_asked(capsys):

    assert main(["run", "--suite", "policy", "--quiet"]) == 0

    assert capsys.readouterr().out == ""


def test_an_unknown_suite_is_rejected_by_the_parser():

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "--suite", "nope"])

    assert exit_info.value.code == 2


def test_live_and_replay_cannot_be_combined():

    with pytest.raises(SystemExit):
        main(["run", "--live", "--replay"])


def test_record_without_live_is_refused(capsys):

    assert main(["run", "--record"]) == 2

    assert "--record only applies" in capsys.readouterr().err


def test_the_exit_code_reports_a_failed_gate(monkeypatch, tmp_path, capsys):
    """A failing suite must make the process exit non-zero."""

    monkeypatch.setenv("EVALS_DIR", str(tmp_path))

    datasets = tmp_path / "datasets"
    datasets.mkdir()

    # An expectation the code cannot meet, so the suite fails.
    (datasets / "policy.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "wrong",
                        "amount": 10,
                        "expected_success": True,
                        "expected_approval": "Manager approval required.",
                        "expected_approvers": ["manager"],
                        "expected_approval_required": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["run", "--suite", "policy"]) == 1

    assert "Gate: FAILED" in capsys.readouterr().out


def test_reports_are_written_where_asked(tmp_path, capsys):

    json_path = tmp_path / "reports" / "evals.json"
    markdown_path = tmp_path / "reports" / "evals.md"

    assert (
        main(
            [
                "run",
                "--suite",
                "policy",
                "--quiet",
                "--json",
                str(json_path),
                "--markdown",
                str(markdown_path),
            ]
        )
        == 0
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["suites"][0]["name"] == "policy"
    assert markdown_path.read_text(encoding="utf-8").startswith("# Eval report")


def test_offline_is_the_default_mode(tmp_path, capsys):

    json_path = tmp_path / "evals.json"

    main(["run", "--suite", "retrieval", "--quiet", "--json", str(json_path)])

    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["mode"] == "offline"
    assert payload["suites"][0]["skipped"] == payload["suites"][0]["cases"]


def test_replay_mode_is_recorded_in_the_report(tmp_path, monkeypatch, capsys):

    monkeypatch.setenv("EVALS_DIR", str(tmp_path))

    json_path = tmp_path / "evals.json"

    # No cassette file exists, so every case is skipped for want of
    # a recording rather than for want of a mode.
    main(
        [
            "run",
            "--suite",
            "grounding",
            "--quiet",
            "--json",
            str(json_path),
            "--replay",
        ]
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["mode"] == "replay"


def test_a_custom_cassette_path_is_used(tmp_path, capsys):

    cassette = tmp_path / "mine.json"

    cassette.write_text(json.dumps({"entries": {}}), encoding="utf-8")

    json_path = tmp_path / "evals.json"

    main(
        [
            "run",
            "--suite",
            "retrieval",
            "--quiet",
            "--replay",
            "--cassette",
            str(cassette),
            "--json",
            str(json_path),
        ]
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert "mine.json" in payload["suites"][0]["results"][0]["skip_reason"]
