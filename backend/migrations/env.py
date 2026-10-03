from alembic import context

from app.services.db import DATABASE_URL, engine
from app.services.db_models import Base, include_managed_tables


config = context.config

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Generate SQL without a database connection
    (alembic upgrade head --sql).
    """

    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        include_name=include_managed_tables,
        literal_binds=True,
        render_as_batch=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_with_connection(connection) -> None:

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Ignore tables owned by others (LangGraph checkpoints).
        include_name=include_managed_tables,
        # Batch mode lets future ALTERs work on SQLite.
        render_as_batch=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:

    # Tests can pass their own connection.
    connection = config.attributes.get("connection")

    if connection is not None:

        run_with_connection(connection)

        return

    with engine.connect() as connection:

        run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
