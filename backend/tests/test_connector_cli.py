"""
python -m app.services.connectors: list and sync commands.
"""

import json

import httpx
import pytest

from app.services.connectors import __main__ as cli
from app.services.context_graph_service import get_node
from connector_fakes import (
    ALICE, BOB, CLIENT_SECRET, make_connector, make_user, purge,
)


ENV_NAMES = ("ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_CLIENT_SECRET")


@pytest.fixture(autouse=True)
def clean_graph():

    purge()
    yield
    purge()


@pytest.fixture
def unconfigured(monkeypatch):

    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_list_reports_missing_configuration(unconfigured, capsys):

    assert cli.main(["list"]) == 0

    assert capsys.readouterr().out.strip() == (
        "entra: missing ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET"
    )


def test_list_reports_configured_without_printing_values(monkeypatch, capsys):

    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-id")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "client-id")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", CLIENT_SECRET)

    assert cli.main(["list"]) == 0

    out = capsys.readouterr().out

    assert out.strip() == "entra: configured"
    assert CLIENT_SECRET not in out


def test_sync_without_configuration_exits_with_a_clear_message(unconfigured, capsys):

    assert cli.main(["sync", "entra"]) == 2

    captured = capsys.readouterr()

    assert "Configuration error: Entra connector is not configured" in captured.err
    assert captured.out == ""


def test_sync_of_an_unknown_connector(capsys):

    assert cli.main(["sync", "nope"]) == 2

    assert "Unknown connector 'nope'" in capsys.readouterr().err


def test_sync_applies_the_directory_and_prints_a_summary(monkeypatch, capsys):

    connector, _ = make_connector([[
        make_user(ALICE, "Alice Ng", department="Finance"),
        make_user(BOB, "Bob Rao", department="Finance", manager=ALICE),
    ]])

    monkeypatch.setattr(cli, "create_connector", lambda name: connector)

    assert cli.main(["sync", "entra"]) == 0

    summary = json.loads(capsys.readouterr().out)

    assert summary["connector"] == "entra"
    assert summary["nodes_created"] == 2
    assert get_node(f"person:entra/{ALICE}")["label"] == "Alice Ng"


def test_sync_failure_exits_non_zero_without_secrets(monkeypatch, capsys):

    connector, graph = make_connector([[make_user(ALICE, "Alice")]])
    graph.graph_failures.append(httpx.Response(403))

    monkeypatch.setattr(cli, "create_connector", lambda name: connector)

    assert cli.main(["sync", "entra"]) == 1

    captured = capsys.readouterr()

    assert captured.err.startswith("Sync failed: Microsoft Graph denied access")
    assert CLIENT_SECRET not in captured.err + captured.out


def test_empty_directory_needs_an_explicit_flag(monkeypatch, capsys):

    first, _ = make_connector([[make_user(ALICE, "Alice")]])
    empty, _ = make_connector([[]])

    monkeypatch.setattr(cli, "create_connector", lambda name: first)
    assert cli.main(["sync", "entra"]) == 0

    monkeypatch.setattr(cli, "create_connector", lambda name: empty)
    assert cli.main(["sync", "entra"]) == 1
    assert "refusing to deactivate" in capsys.readouterr().err

    assert cli.main(["sync", "entra", "--allow-empty"]) == 0
    assert get_node(f"person:entra/{ALICE}")["is_active"] is False
