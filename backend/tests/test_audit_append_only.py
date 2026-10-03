"""
audit_events is append-only at the database level (migration 0003).

Runs on SQLite offline and on PostgreSQL via TEST_DATABASE_URL.
"""

import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.services.audit_service import add_event, list_events, record_event
from app.services.database_service import ALEMBIC_CONFIG_PATH
from app.services.db import create_database_engine, engine, session_scope
from app.services.db_models import AuditEvent


USING_POSTGRES = engine.url.get_backend_name() == "postgresql"


@pytest.fixture
def event_id() -> int:
    """A freshly inserted audit event."""

    thread_id = f"append-only-{uuid.uuid4()}"

    assert record_event(
        event_type="workflow_started",
        message="Original message.",
        thread_id=thread_id
    ) is True

    [event] = list_events(thread_id=thread_id)

    return event["id"]


def stored_message(event_id: int) -> str | None:

    with session_scope() as session:
        return session.scalar(
            select(AuditEvent.message).where(AuditEvent.id == event_id)
        )


# ============================================================
# INSERT
# ============================================================

def test_insert_succeeds():

    thread_id = f"append-only-{uuid.uuid4()}"

    assert record_event(
        event_type="workflow_started",
        message="Best-effort insert.",
        thread_id=thread_id
    ) is True

    with session_scope() as session:
        add_event(
            session,
            event_type="workflow_completed",
            message="Transactional insert.",
            status="success",
            thread_id=thread_id
        )

    assert [e["message"] for e in list_events(thread_id=thread_id)] == [
        "Best-effort insert.",
        "Transactional insert.",
    ]


# ============================================================
# UPDATE
# ============================================================

def test_orm_update_is_rejected(event_id):

    with pytest.raises(IntegrityError, match="append-only"):
        with session_scope() as session:
            session.get(AuditEvent, event_id).message = "Tampered."

    assert stored_message(event_id) == "Original message."


def test_sql_update_is_rejected(event_id):

    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_events SET status = 'success' WHERE id = :id"),
                {"id": event_id}
            )

    assert stored_message(event_id) == "Original message."


# ============================================================
# DELETE
# ============================================================

def test_orm_delete_is_rejected(event_id):

    with pytest.raises(IntegrityError, match="append-only"):
        with session_scope() as session:
            session.delete(session.get(AuditEvent, event_id))

    assert stored_message(event_id) == "Original message."


def test_sql_delete_is_rejected(event_id):

    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": event_id}
            )

    assert stored_message(event_id) == "Original message."


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="TRUNCATE exists only on PostgreSQL."
)
def test_truncate_is_rejected_on_postgres(event_id):

    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE audit_events"))

    assert stored_message(event_id) == "Original message."


def test_other_tables_are_not_affected():
    """Only audit_events is protected; workflow_runs stays updatable."""

    from app.services.database_service import (
        create_workflow_run,
        get_workflow_run,
        update_workflow_run,
    )

    thread_id = f"append-only-{uuid.uuid4()}"

    create_workflow_run(thread_id, "question")
    update_workflow_run(thread_id, status="success")

    assert get_workflow_run(thread_id)["status"] == "success"


# ============================================================
# MIGRATION
# ============================================================

def test_triggers_are_installed():

    with engine.connect() as connection:

        if USING_POSTGRES:

            names = set(connection.scalars(text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'audit_events'::regclass AND NOT tgisinternal"
            )))

            assert names == {
                "audit_events_no_update_delete",
                "audit_events_no_truncate",
            }

        else:

            names = set(connection.scalars(text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'audit_events'"
            )))

            assert names == {
                "audit_events_no_update",
                "audit_events_no_delete",
            }


def test_migration_downgrade_removes_and_upgrade_restores_protection(tmp_path):
    """On a separate SQLite database, so the shared test database keeps its protection."""

    database = create_database_engine(
        f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    )

    def migrate(action, revision):

        with database.begin() as connection:
            config = Config(str(ALEMBIC_CONFIG_PATH))
            config.attributes["connection"] = connection
            action(config, revision)

    def try_update() -> bool:

        try:
            with database.begin() as connection:
                connection.execute(text("UPDATE audit_events SET message = 'x'"))
            return True
        except IntegrityError:
            return False

    try:

        migrate(command.upgrade, "head")

        with database.begin() as connection:
            connection.execute(text(
                "INSERT INTO audit_events "
                "(event_id, occurred_at, event_type, actor_type, status, message) "
                "VALUES (:event_id, CURRENT_TIMESTAMP, 'workflow_started', 'system', 'info', 'm')"
            ), {"event_id": uuid.uuid4().hex})

        assert try_update() is False

        migrate(command.downgrade, "0002")

        assert try_update() is True

        migrate(command.upgrade, "head")

        assert try_update() is False

    finally:

        database.dispose()
