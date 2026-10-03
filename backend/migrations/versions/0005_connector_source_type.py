"""Allow 'connector' as a context graph source type

Enterprise connectors write nodes and edges with source_type
'connector'. Only the CHECK constraints change; no data is touched
on upgrade.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""

from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


OLD_SOURCE_TYPES_SQL = (
    "source_type IN ('system_of_record', 'document', 'curated', 'derived')"
)

NEW_SOURCE_TYPES_SQL = (
    "source_type IN ('system_of_record', 'document', 'curated', 'derived', "
    "'connector')"
)

# Constraint names after the metadata naming convention
# ("ck_<table>_<name>"). op.f() keeps them from being expanded twice.
TABLES = ("context_nodes", "context_edges")


def constraint_name(table: str) -> str:

    return f"ck_{table}_source_type_valid"


EDGE_COLUMNS = (
    "id, from_node_id, to_node_id, edge_type, properties, source_type, "
    "source_ref, confidence, created_at, updated_at"
)


def replace_constraint(sql: str) -> None:

    # SQLite cannot alter a CHECK constraint, so batch mode rebuilds
    # the table. Dropping context_nodes makes SQLite's implicit DELETE
    # cascade to context_edges (foreign keys are enforced), so keep a
    # copy of the edges and restore it. PostgreSQL alters in place.
    sqlite = op.get_context().dialect.name == "sqlite"

    if sqlite:

        op.execute(
            f"CREATE TABLE context_edges_backup AS "
            f"SELECT {EDGE_COLUMNS} FROM context_edges"
        )

    for table in TABLES:

        with op.batch_alter_table(table) as batch:

            batch.drop_constraint(
                op.f(constraint_name(table)), type_="check"
            )

            batch.create_check_constraint(
                op.f(constraint_name(table)), sql
            )

    if sqlite:

        op.execute(
            f"INSERT INTO context_edges ({EDGE_COLUMNS}) "
            f"SELECT {EDGE_COLUMNS} FROM context_edges_backup"
        )

        op.execute("DROP TABLE context_edges_backup")


def upgrade() -> None:

    replace_constraint(NEW_SOURCE_TYPES_SQL)


def downgrade() -> None:

    # The old constraint cannot hold connector rows. They are a
    # rebuildable projection of an external system, so remove them
    # (their edges first) and re-run the connector sync after
    # upgrading again.
    op.execute("DELETE FROM context_edges WHERE source_type = 'connector'")

    op.execute(
        "DELETE FROM context_edges WHERE from_node_id IN "
        "(SELECT id FROM context_nodes WHERE source_type = 'connector') "
        "OR to_node_id IN "
        "(SELECT id FROM context_nodes WHERE source_type = 'connector')"
    )

    op.execute("DELETE FROM context_nodes WHERE source_type = 'connector'")

    replace_constraint(OLD_SOURCE_TYPES_SQL)
