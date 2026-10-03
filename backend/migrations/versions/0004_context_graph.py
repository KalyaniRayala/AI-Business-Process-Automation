"""Context graph: context_nodes and context_edges

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


BIG_INTEGER_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

SOURCE_TYPES_SQL = (
    "source_type IN ('system_of_record', 'document', 'curated', 'derived')"
)


def upgrade() -> None:

    op.create_table(
        "context_nodes",
        sa.Column("id", BIG_INTEGER_ID, autoincrement=True, nullable=False),
        sa.Column("node_key", sa.String(200), nullable=False),
        sa.Column("node_type", sa.String(50), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("properties", JSON_DOCUMENT, nullable=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_ref", sa.String(300), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            SOURCE_TYPES_SQL,
            # Expanded by the naming convention to
            # ck_context_nodes_source_type_valid
            name="source_type_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_context_nodes"),
        sa.UniqueConstraint("node_key", name="uq_context_nodes_node_key"),
    )

    op.create_index(
        "ix_context_nodes_node_type",
        "context_nodes",
        ["node_type"],
    )

    op.create_table(
        "context_edges",
        sa.Column("id", BIG_INTEGER_ID, autoincrement=True, nullable=False),
        sa.Column("from_node_id", BIG_INTEGER_ID, nullable=False),
        sa.Column("to_node_id", BIG_INTEGER_ID, nullable=False),
        sa.Column("edge_type", sa.String(50), nullable=False),
        sa.Column("properties", JSON_DOCUMENT, nullable=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_ref", sa.String(300), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_node_id <> to_node_id",
            # Expanded to ck_context_edges_no_self_loop
            name="no_self_loop",
        ),
        sa.CheckConstraint(
            SOURCE_TYPES_SQL,
            # Expanded to ck_context_edges_source_type_valid
            name="source_type_valid",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            # Expanded to ck_context_edges_confidence_range
            name="confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["from_node_id"],
            ["context_nodes.id"],
            name="fk_context_edges_from_node_id_context_nodes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_node_id"],
            ["context_nodes.id"],
            name="fk_context_edges_to_node_id_context_nodes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_context_edges"),
        sa.UniqueConstraint(
            "from_node_id",
            "edge_type",
            "to_node_id",
            name="uq_context_edges_from_type_to",
        ),
    )

    op.create_index(
        "ix_context_edges_from_node_id_edge_type",
        "context_edges",
        ["from_node_id", "edge_type"],
    )

    op.create_index(
        "ix_context_edges_to_node_id_edge_type",
        "context_edges",
        ["to_node_id", "edge_type"],
    )


def downgrade() -> None:

    op.drop_index(
        "ix_context_edges_to_node_id_edge_type",
        table_name="context_edges",
    )
    op.drop_index(
        "ix_context_edges_from_node_id_edge_type",
        table_name="context_edges",
    )
    op.drop_table("context_edges")

    op.drop_index("ix_context_nodes_node_type", table_name="context_nodes")
    op.drop_table("context_nodes")
