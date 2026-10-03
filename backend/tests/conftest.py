"""
Shared test setup.

Runs before any app module is imported, so the suite:
- never uses the real Cohere key (a dummy key is forced),
- never touches data/business.db or chroma_db/ (temp paths),
- never makes network calls (Cohere clients are replaced).
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from dotenv import dotenv_values


# ============================================================
# ENVIRONMENT (must be set before importing app modules)
# ============================================================

# Chroma can keep its SQLite file open until the process exits on
# Windows, so also clear directories left behind by earlier runs.
for stale_dir in Path(tempfile.gettempdir()).glob("bpa-tests-*"):
    shutil.rmtree(stale_dir, ignore_errors=True)

TEST_DIR = Path(tempfile.mkdtemp(prefix="bpa-tests-"))

os.environ["COHERE_API_KEY"] = "test-dummy-key"

# Optional: run the suite against a dedicated, disposable
# PostgreSQL database instead of a temporary SQLite file.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if TEST_DATABASE_URL:

    configured_url = dotenv_values(
        Path(__file__).resolve().parents[1] / ".env"
    ).get("DATABASE_URL")

    if TEST_DATABASE_URL == configured_url:
        raise RuntimeError(
            "TEST_DATABASE_URL must point to a separate test "
            "database, not the DATABASE_URL in .env."
        )

    os.environ["DATABASE_URL"] = TEST_DATABASE_URL

else:

    os.environ["DATABASE_URL"] = (
        f"sqlite:///{(TEST_DIR / 'business.db').as_posix()}"
    )
os.environ["CHROMA_PATH"] = str(TEST_DIR / "chroma_db")


def pytest_sessionfinish(session, exitstatus):

    # Release database and Chroma file handles first; Windows
    # cannot delete files that are still open.
    try:
        from app.services.db import engine

        engine.dispose()

    except Exception:
        pass

    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()

    except Exception:
        pass

    shutil.rmtree(TEST_DIR, ignore_errors=True)


# ============================================================
# NETWORK GUARD
# ============================================================

class _NoNetworkClient:
    """Fails loudly if any test reaches a real Cohere client."""

    def __getattr__(self, name):
        raise RuntimeError(
            f"Test attempted a real Cohere call: client.{name}"
        )


@pytest.fixture(autouse=True)
def block_cohere(monkeypatch):

    from app.services import (
        embedding_services,
        llm_service,
        structured_output_service,
    )

    monkeypatch.setattr(llm_service, "client", _NoNetworkClient())
    monkeypatch.setattr(embedding_services, "co", _NoNetworkClient())
    monkeypatch.setattr(
        structured_output_service, "client", _NoNetworkClient()
    )


# ============================================================
# DATABASE
# ============================================================

@pytest.fixture(scope="session", autouse=True)
def seeded_database():

    from sqlalchemy.engine import make_url

    from app.services.database_service import initialize_database
    from app.services.db import engine

    # Never run tests against the real database.
    if TEST_DATABASE_URL:
        assert engine.url == make_url(TEST_DATABASE_URL)
    else:
        assert TEST_DIR in Path(engine.url.database).parents

    initialize_database()

    # Same startup step as the app lifespan.
    from app.services.context_graph_seed import sync_context_graph

    sync_context_graph()

    # Graph tests run without the app lifespan, so prepare
    # PostgresSaver tables here too (no-op for MemorySaver).
    from app.services.agent_graph import checkpointer
    from app.services.checkpointer import setup_checkpointer

    setup_checkpointer(checkpointer)


# ============================================================
# STUBBED RAG + LLM FOR THE GRAPH
# ============================================================

DOCUMENT_CHUNK = "7. Expenses above $500 require manager approval."


@pytest.fixture
def document_results():
    """Chunks returned by the stubbed document search."""

    return [DOCUMENT_CHUNK]


@pytest.fixture(autouse=True)
def stub_graph_services(monkeypatch, document_results):

    from app.services import agent_graph

    def fake_search(query: str) -> dict:
        return {
            "query": query,
            "results": document_results
        }

    def fake_generate_answer(question: str, context: str) -> str:
        return "Stubbed answer."

    monkeypatch.setattr(
        agent_graph, "search_business_documents", fake_search
    )
    monkeypatch.setattr(
        agent_graph, "generate_answer", fake_generate_answer
    )
