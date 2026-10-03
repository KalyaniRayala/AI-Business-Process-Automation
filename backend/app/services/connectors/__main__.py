"""
Command line: python -m app.services.connectors <command>

    list               connectors and whether they are configured
    sync <name>        run a connector and apply it to the context graph

Output never includes secrets: only variable names and sync counts.
"""

import argparse
import json
import logging
import os
import sys

from app.services.connectors import (
    CONNECTORS,
    ConnectorConfigError,
    ConnectorError,
    create_connector,
    sync_connector,
)


def list_connectors() -> int:

    for name, variables in CONNECTORS.items():

        missing = [v for v in variables if not (os.getenv(v) or "").strip()]

        state = "configured" if not missing else f"missing {', '.join(missing)}"

        print(f"{name}: {state}")

    return 0


def run_sync(name: str, allow_empty: bool) -> int:

    # Same preparation as the API lifespan: schema, employees, and
    # the curated graph the connector links to (roles, departments).
    from app.services.context_graph_seed import sync_context_graph
    from app.services.database_service import initialize_database

    try:

        connector = create_connector(name)

    except ConnectorConfigError as error:

        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    initialize_database()
    sync_context_graph()

    try:

        summary = sync_connector(connector, allow_empty=allow_empty)

    except ConnectorError as error:

        print(f"Sync failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))

    return 0


def main(argv=None) -> int:

    parser = argparse.ArgumentParser(prog="python -m app.services.connectors")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list")

    sync = commands.add_parser("sync")
    sync.add_argument("name")
    sync.add_argument(
        "--allow-empty", action="store_true",
        help="accept an empty result even if the connector owns data",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    if args.command == "list":
        return list_connectors()

    return run_sync(args.name, args.allow_empty)


if __name__ == "__main__":

    sys.exit(main())
