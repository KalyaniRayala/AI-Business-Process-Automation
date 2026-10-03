import atexit
import logging

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.engine import make_url

from app.services.db import DATABASE_URL


logger = logging.getLogger(__name__)


# ============================================================
# CONNECTION STRING
# ============================================================

def is_postgres_url(url: str) -> bool:

    return make_url(url).get_backend_name() == "postgresql"


def to_psycopg_conninfo(url: str) -> str:
    """
    Convert a SQLAlchemy URL (postgresql+psycopg://...) into
    the plain postgresql:// form psycopg expects.
    """

    return (
        make_url(url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


# ============================================================
# CHECKPOINTER
# ============================================================

def create_checkpointer(url: str = DATABASE_URL) -> BaseCheckpointSaver:
    """
    PostgreSQL: durable checkpoints, so workflows waiting for
    approval survive restarts and are shared across workers.

    Otherwise (SQLite default): in-memory checkpoints.
    """

    if not is_postgres_url(url):

        return MemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    # Settings required by PostgresSaver.
    pool = ConnectionPool(
        conninfo=to_psycopg_conninfo(url),
        min_size=1,
        max_size=10,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=True
    )

    # The pool lives as long as the process (the compiled graph
    # is module-level), so close it only at interpreter exit.
    atexit.register(pool.close)

    return PostgresSaver(pool)


def setup_checkpointer(checkpointer: BaseCheckpointSaver) -> None:
    """
    Create or upgrade the checkpoint tables (idempotent).

    PostgresSaver manages its own tables; they are not part of
    the Alembic migrations.
    """

    if hasattr(checkpointer, "setup"):

        checkpointer.setup()

        logger.info("PostgreSQL checkpoint tables are ready.")
