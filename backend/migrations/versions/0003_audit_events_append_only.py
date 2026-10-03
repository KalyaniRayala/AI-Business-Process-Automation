"""Audit trail: make audit_events append-only

Rejects UPDATE and DELETE (and TRUNCATE on PostgreSQL) on
audit_events at the database level. INSERT is unaffected.

Both dialects raise an integrity error (SQLSTATE 23001
restrict_violation on PostgreSQL, SQLITE_CONSTRAINT on SQLite),
which SQLAlchemy reports as IntegrityError.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30
"""

from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


POSTGRES_UPGRADE = [
    """
    CREATE FUNCTION audit_events_reject_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        -- Message built by concatenation: format placeholders
        -- would be escaped by pyformat drivers.
        RAISE EXCEPTION USING
            MESSAGE = 'audit_events is append-only: '
                      || TG_OP || ' is not allowed',
            ERRCODE = 'restrict_violation';
    END;
    $$
    """,
    """
    CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_reject_change()
    """,
    """
    CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_reject_change()
    """,
]

POSTGRES_DOWNGRADE = [
    "DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events",
    "DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events",
    "DROP FUNCTION IF EXISTS audit_events_reject_change()",
]

SQLITE_UPGRADE = [
    """
    CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE ON audit_events
    BEGIN
        SELECT RAISE(ABORT, 'audit_events is append-only: UPDATE is not allowed');
    END
    """,
    """
    CREATE TRIGGER audit_events_no_delete
    BEFORE DELETE ON audit_events
    BEGIN
        SELECT RAISE(ABORT, 'audit_events is append-only: DELETE is not allowed');
    END
    """,
]

SQLITE_DOWNGRADE = [
    "DROP TRIGGER IF EXISTS audit_events_no_delete",
    "DROP TRIGGER IF EXISTS audit_events_no_update",
]


def dialect() -> str:

    # Works online and in offline (--sql) mode.
    return op.get_context().dialect.name


def run(statements: list[str]) -> None:

    for statement in statements:
        op.execute(statement)


def upgrade() -> None:

    if dialect() == "postgresql":
        run(POSTGRES_UPGRADE)

    elif dialect() == "sqlite":
        run(SQLITE_UPGRADE)


def downgrade() -> None:

    if dialect() == "postgresql":
        run(POSTGRES_DOWNGRADE)

    elif dialect() == "sqlite":
        run(SQLITE_DOWNGRADE)
