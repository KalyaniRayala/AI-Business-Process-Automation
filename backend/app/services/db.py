import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker


load_dotenv()


# ============================================================
# DATABASE URL
# ============================================================

# SQLite by default; set DATABASE_URL to use PostgreSQL, e.g.
# postgresql+psycopg://user:password@localhost:5432/bpa
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///data/business.db"
)


# ============================================================
# ENGINE
# ============================================================

def create_database_engine(url: str) -> Engine:
    """
    Create a SQLAlchemy engine for the given database URL.
    """

    parsed_url = make_url(url)

    if parsed_url.get_backend_name() != "sqlite":

        return create_engine(
            url,
            pool_pre_ping=True
        )

    if parsed_url.database not in (None, "", ":memory:"):

        Path(parsed_url.database).parent.mkdir(
            parents=True,
            exist_ok=True
        )

    engine = create_engine(url)

    # SQLite does not enforce foreign keys unless asked to,
    # which PostgreSQL always does.
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = create_database_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False
)


# ============================================================
# SESSION
# ============================================================

@contextmanager
def session_scope():
    """
    Provide a transactional session: commit on success,
    roll back on error.
    """

    session: Session = SessionLocal()

    try:

        yield session

        session.commit()

    except Exception:

        session.rollback()

        raise

    finally:

        session.close()
