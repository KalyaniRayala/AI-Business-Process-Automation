"""Audit trail: audit_events table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


BIG_INTEGER_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:

    op.create_table(
        "audit_events",
        sa.Column("id", BIG_INTEGER_ID, autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(100), nullable=True),
        sa.Column("node", sa.String(50), nullable=True),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", JSON_DOCUMENT, nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('system', 'human', 'agent')",
            # Expanded by the naming convention to
            # ck_audit_events_actor_type_valid
            name="actor_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failure', 'pending', 'info')",
            # Expanded to ck_audit_events_status_valid
            name="status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.UniqueConstraint("event_id", name="uq_audit_events_event_id"),
    )

    op.create_index(
        "ix_audit_events_thread_id_occurred_at",
        "audit_events",
        ["thread_id", "occurred_at"],
    )

    op.create_index(
        "ix_audit_events_event_type_occurred_at",
        "audit_events",
        ["event_type", "occurred_at"],
    )

    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["entity_type", "entity_id"],
    )

    op.create_index(
        "ix_audit_events_occurred_at",
        "audit_events",
        ["occurred_at"],
    )


def downgrade() -> None:

    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index(
        "ix_audit_events_event_type_occurred_at",
        table_name="audit_events",
    )
    op.drop_index(
        "ix_audit_events_thread_id_occurred_at",
        table_name="audit_events",
    )
    op.drop_table("audit_events")
