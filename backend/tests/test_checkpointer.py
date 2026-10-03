from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.services import agent_graph as agent_graph_module
from app.services.agent_graph import agent_graph, checkpointer
from app.services.checkpointer import (
    create_checkpointer,
    is_postgres_url,
    setup_checkpointer,
    to_psycopg_conninfo,
)
from app.services.db import engine


USING_POSTGRES = engine.url.get_backend_name() == "postgresql"

QUESTION_750 = "For employee E102, process a $750 expense."


# ============================================================
# CONFIGURATION (no database needed)
# ============================================================

def test_sqlite_url_uses_memory_saver():

    assert isinstance(
        create_checkpointer("sqlite:///data/business.db"),
        MemorySaver
    )


def test_postgres_url_detection():

    assert is_postgres_url("postgresql+psycopg://u:p@localhost/bpa")
    assert is_postgres_url("postgresql://u:p@localhost/bpa")
    assert not is_postgres_url("sqlite:///data/business.db")


def test_conninfo_drops_sqlalchemy_driver_and_keeps_password():

    conninfo = to_psycopg_conninfo(
        "postgresql+psycopg://bpa_app:s3cret@db.example:5433/bpa"
    )

    assert conninfo == "postgresql://bpa_app:s3cret@db.example:5433/bpa"


def test_graph_uses_checkpointer_for_configured_database():

    if USING_POSTGRES:

        from langgraph.checkpoint.postgres import PostgresSaver

        assert isinstance(checkpointer, PostgresSaver)

    else:

        assert isinstance(checkpointer, MemorySaver)


# ============================================================
# DURABILITY (PostgreSQL only)
# ============================================================

@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Needs TEST_DATABASE_URL pointing to PostgreSQL."
)
def test_paused_workflow_survives_restart():
    """
    Start a workflow, then resume it from a brand-new
    checkpointer and compiled graph, as after a server restart.
    """

    config = {"configurable": {"thread_id": str(uuid4())}}

    paused = agent_graph.invoke(
        {"question": QUESTION_750},
        config=config
    )

    assert "__interrupt__" in paused

    restarted_checkpointer = create_checkpointer(str(
        engine.url.render_as_string(hide_password=False)
    ))

    setup_checkpointer(restarted_checkpointer)

    restarted_graph = agent_graph_module.graph.compile(
        checkpointer=restarted_checkpointer
    )

    snapshot = restarted_graph.get_state(config)

    assert snapshot.tasks[0].interrupts[0].value["type"] == (
        "approval_required"
    )

    result = restarted_graph.invoke(
        Command(resume="approve"),
        config=config
    )

    assert result["status"] == "success"
    assert result["action_result"]["success"] is True
