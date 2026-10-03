"""
Migration 0005: 'connector' becomes a valid context graph source type.
Runs on separate SQLite databases so the shared test database keeps
its schema.
"""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services.database_service import ALEMBIC_CONFIG_PATH
from app.services.db import create_database_engine


@pytest.fixture
def database(tmp_path):

    engine = create_database_engine(
        f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    )

    yield engine

    engine.dispose()


def migrate(engine, action, revision):

    with engine.begin() as connection:

        config = Config(str(ALEMBIC_CONFIG_PATH))
        config.attributes["connection"] = connection

        action(config, revision)


def add_node(engine, key: str, source_type: str) -> bool:
    """True if the database accepted the node."""

    try:

        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO context_nodes (node_key, node_type, label, source_type) "
                "VALUES (:key, 'person', 'x', :source_type)"
            ), {"key": key, "source_type": source_type})

        return True

    except IntegrityError:

        return False


def add_edge(engine, from_key: str, to_key: str, source_type: str) -> bool:

    try:

        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO context_edges "
                "(from_node_id, to_node_id, edge_type, source_type) "
                "VALUES ((SELECT id FROM context_nodes WHERE node_key = :a), "
                "(SELECT id FROM context_nodes WHERE node_key = :b), "
                "'reports_to', :source_type)"
            ), {"a": from_key, "b": to_key, "source_type": source_type})

        return True

    except IntegrityError:

        return False


def count(engine, table: str, where: str = "1=1") -> int:

    with engine.connect() as connection:
        return connection.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {where}")
        ).scalar()


def test_connector_source_type_is_rejected_before_0005_and_accepted_after(database):

    migrate(database, command.upgrade, "0004")

    assert add_node(database, "person:x/1", "connector") is False
    assert add_node(database, "person:x/1", "curated") is True

    migrate(database, command.upgrade, "head")

    assert add_node(database, "person:x/2", "connector") is True
    assert add_node(database, "person:x/3", "system_of_record") is True
    assert add_node(database, "person:x/4", "guess") is False

    # Existing rows survive the upgrade.
    assert count(database, "context_nodes", "node_key = 'person:x/1'") == 1


def test_upgrade_keeps_existing_nodes_and_edges(database):
    """SQLite rebuilds the tables; the edges must not be lost to a
    foreign-key cascade while the nodes table is replaced."""

    migrate(database, command.upgrade, "0004")

    add_node(database, "person:x/a", "curated")
    add_node(database, "person:x/b", "curated")
    add_edge(database, "person:x/a", "person:x/b", "curated")

    migrate(database, command.upgrade, "head")

    assert count(database, "context_nodes") == 2
    assert count(database, "context_edges") == 1
    assert count(database, "context_edges", "edge_type = 'reports_to'") == 1

    # The edge still points at the same nodes.
    assert count(
        database, "context_edges e JOIN context_nodes a ON a.id = e.from_node_id "
        "JOIN context_nodes b ON b.id = e.to_node_id",
        "a.node_key = 'person:x/a' AND b.node_key = 'person:x/b'"
    ) == 1

    # No leftover helper table.
    with database.connect() as connection:
        tables = {row[0] for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )}

    assert "context_edges_backup" not in tables


def test_edges_accept_the_connector_source_type_too(database):

    migrate(database, command.upgrade, "head")

    add_node(database, "person:x/a", "curated")
    add_node(database, "person:x/b", "connector")

    assert add_edge(database, "person:x/b", "person:x/a", "connector") is True
    assert add_edge(database, "person:x/a", "person:x/b", "guess") is False


def test_downgrade_removes_connector_rows_and_upgrade_restores_support(database):

    migrate(database, command.upgrade, "head")

    add_node(database, "person:x/curated", "curated")
    add_node(database, "person:x/other", "curated")
    add_node(database, "person:x/connector", "connector")

    # A connector edge, and a curated edge pointing at a connector node.
    add_edge(database, "person:x/curated", "person:x/other", "curated")
    add_edge(database, "person:x/connector", "person:x/curated", "connector")
    add_edge(database, "person:x/other", "person:x/connector", "curated")

    migrate(database, command.downgrade, "0004")

    assert count(database, "context_nodes", "source_type = 'connector'") == 0
    assert count(database, "context_nodes") == 2
    assert count(database, "context_edges") == 1      # only the curated pair

    assert add_node(database, "person:x/new", "connector") is False

    migrate(database, command.upgrade, "head")

    assert add_node(database, "person:x/new", "connector") is True
