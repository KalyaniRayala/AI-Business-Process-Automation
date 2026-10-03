import sqlite3

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, inspect, select

from app.services.database_service import (
    ALEMBIC_CONFIG_PATH,
    initialize_database,
)
from app.services.db import create_database_engine, engine, session_scope
from app.services.db_models import (
    Base,
    Employee,
    include_managed_tables,
)


EXPECTED_TABLES = {
    "alembic_version",
    "employees",
    "workflow_runs",
    "approval_decisions",
    "reimbursements",
}


def test_migrations_create_all_tables():

    tables = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES <= tables


def test_models_match_migrations():
    """The Alembic migration and the SQLAlchemy models must not drift."""

    with engine.connect() as connection:

        differences = compare_metadata(
            MigrationContext.configure(
                connection,
                opts={"include_name": include_managed_tables}
            ),
            Base.metadata
        )

    assert differences == []


def test_seeding_is_idempotent():

    initialize_database()
    initialize_database()

    with session_scope() as session:

        count = session.scalar(
            select(func.count()).select_from(Employee)
        )

    assert count == 4


def test_migration_adopts_legacy_sqlite_database(tmp_path):
    """An existing data/business.db from the sqlite3 code keeps its data."""

    legacy_path = tmp_path / "legacy.db"

    with sqlite3.connect(legacy_path) as legacy:

        legacy.execute("""
            CREATE TABLE employees (
                employee_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                manager TEXT NOT NULL,
                annual_budget REAL NOT NULL
            )
        """)

        legacy.execute(
            "INSERT INTO employees VALUES "
            "('E900', 'Legacy', 'Ops', 'Boss', 1000)"
        )

    legacy_engine = create_database_engine(
        f"sqlite:///{legacy_path.as_posix()}"
    )

    try:

        with legacy_engine.begin() as connection:

            config = Config(str(ALEMBIC_CONFIG_PATH))
            config.attributes["connection"] = connection

            command.upgrade(config, "head")

        assert EXPECTED_TABLES <= set(
            inspect(legacy_engine).get_table_names()
        )

        with legacy_engine.connect() as connection:

            name = connection.scalar(
                select(Employee.name).where(
                    Employee.employee_id == "E900"
                )
            )

            # The adopted table now matches a fresh install.
            differences = compare_metadata(
                MigrationContext.configure(
                    connection,
                    opts={"include_name": include_managed_tables}
                ),
                Base.metadata
            )

        assert name == "Legacy"
        assert differences == []

    finally:

        legacy_engine.dispose()
