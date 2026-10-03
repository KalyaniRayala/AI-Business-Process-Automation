"""
CG5: Context Graph facts combined with retrieved document context
before LLM generation (graph-only, RAG-only and combined).
"""

from uuid import uuid4

import pytest
from langgraph.types import Command
from sqlalchemy import func, select

from app.services import agent_graph as agent_graph_module
from app.services import context_rag_service, rag_service
from app.services.agent_graph import agent_graph, employee_node, policy_node
from app.services.context_rag_service import (
    DOCUMENTS_HEADER,
    GRAPH_HEADER,
    answer_question_with_graph,
    build_question_context,
    combine_context,
    extract_employee_id,
    extract_expense_amount,
    retrieve_graph_context,
)
from app.services.db import session_scope
from app.services.db_models import ContextEdge, ContextNode


CHUNKS = [
    "7. Expenses above $500 require manager approval.",
    "8. Expenses above $5000 require finance approval.",
]

E102_750 = (
    "Employee E102: department Finance; manager Priya (Manager).\n"
    "Expense amount $750.00: approval required from Manager.\n"
    "Rule policy_rule:expense_approval_over_500: Expenses above $500 "
    "require manager approval (threshold $500; approvers: Manager; "
    "process steps: Check expense reimbursement policy; Manager approval "
    "for expenses above $500; evidence: document:data/expense_process.txt#L15)."
)


def graph_size() -> tuple[int, int]:

    with session_scope() as session:
        return (
            session.scalar(select(func.count()).select_from(ContextNode)),
            session.scalar(select(func.count()).select_from(ContextEdge)),
        )


class Contexts(list):
    """LLM contexts, plus the stubbed document chunks."""


@pytest.fixture
def llm_contexts(monkeypatch):
    """Stub retrieval + LLM for the standalone RAG services and
    record the context each LLM call receives."""

    contexts = Contexts()
    documents = list(CHUNKS)

    def fake_retrieve(query, top_k=3):
        return list(documents)

    def fake_generate(question, context):
        contexts.append(context)
        return "Stubbed answer."

    for module in (context_rag_service, rag_service):
        monkeypatch.setattr(module, "retrieve_context", fake_retrieve)
        monkeypatch.setattr(module, "generate_answer", fake_generate)

    contexts.documents = documents
    return contexts


@pytest.fixture
def workflow_contexts(monkeypatch):
    """Record the context the workflow's final LLM call receives."""

    contexts = []

    def fake_generate(question, context):
        contexts.append(context)
        return "Stubbed answer."

    monkeypatch.setattr(agent_graph_module, "generate_answer", fake_generate)

    return contexts


def run(question: str, decision: str | None = None) -> dict:

    config = {"configurable": {"thread_id": str(uuid4())}}

    result = agent_graph.invoke({"question": question}, config=config)

    if decision:
        result = agent_graph.invoke(Command(resume=decision), config=config)

    return result


def graph_section(context: str) -> str:

    return context.split("Context Graph:", 1)[1].split("Human Approval Status:", 1)[0].strip()


# ============================================================
# QUESTION PARSING MATCHES THE WORKFLOW NODES
# ============================================================

@pytest.mark.parametrize(
    "question",
    [
        "For employee E102, process a $750 expense.",
        "e101 needs $6,000 reimbursed?",
        "Can E103 claim 0 or 400 dollars?",
        "What is the reimbursement process?",
        "E999 spent $12.50",
    ],
)
def test_question_parsing_matches_workflow_nodes(question):

    policy = policy_node({"question": question})["policy_result"]
    employee = employee_node({"question": question})["employee_data"]

    assert extract_expense_amount(question) == policy.get("expense_amount")

    expected_id = (
        employee["employee"]["employee_id"] if employee["success"]
        else (None if employee["error"] == "Employee ID not found." else "E999")
    )

    assert extract_employee_id(question) == expected_id


# ============================================================
# GRAPH CONTEXT
# ============================================================

def test_employee_and_amount_graph_context():

    assert retrieve_graph_context("E102", 750.0) == E102_750


def test_amount_only_graph_context():

    text = retrieve_graph_context(expense_amount=6000.0)

    assert text.splitlines()[0] == (
        "Expense amount $6,000.00: approval required from Manager, Finance."
    )
    assert "Employee" not in text
    assert [line.split(": ")[0] for line in text.splitlines()[1:]] == [
        "Rule policy_rule:expense_approval_over_500",
        "Rule policy_rule:expense_approval_over_5000",
    ]


def test_employee_only_and_no_approval_graph_context():

    assert retrieve_graph_context("E104") == (
        "Employee E104: department Engineering; manager Ravi (Manager)."
    )
    assert retrieve_graph_context("E103", 400.0).splitlines()[1] == (
        "Expense amount $400.00: no approval required."
    )


@pytest.mark.parametrize(
    "employee_id, amount",
    [(None, None), ("E999", 750.0), ("E102;drop", None)],
)
def test_no_relevant_graph_context(employee_id, amount):

    assert retrieve_graph_context(employee_id, amount) == ""


def test_graph_failure_returns_no_context(monkeypatch):

    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(context_rag_service, "get_business_context", broken)
    monkeypatch.setattr(context_rag_service, "get_applicable_rules", broken)

    assert retrieve_graph_context("E102", 750.0) == ""
    assert retrieve_graph_context(expense_amount=750.0) == ""


# ============================================================
# COMBINING CONTEXT
# ============================================================

def test_combine_context():

    documents = "\n\n".join(CHUNKS)

    assert combine_context(CHUNKS, "") == documents
    assert combine_context([], "facts") == f"{GRAPH_HEADER}\nfacts"
    assert combine_context(CHUNKS, "facts") == (
        f"{GRAPH_HEADER}\nfacts\n\n{DOCUMENTS_HEADER}\n{documents}"
    )
    assert combine_context([], "") == ""


# ============================================================
# STANDALONE RAG: RAG-ONLY, GRAPH-ONLY, COMBINED
# ============================================================

def test_rag_only_matches_existing_answer_question(llm_contexts):

    question = "What is the reimbursement process?"

    assert answer_question_with_graph(question) == "Stubbed answer."

    rag_service.answer_question(question)

    [with_graph, rag_only] = llm_contexts

    assert with_graph == rag_only == "\n\n".join(CHUNKS)


def test_graph_only_when_no_documents_are_found(llm_contexts):

    llm_contexts.documents.clear()

    result = build_question_context("For employee E102, process a $750 expense.")

    assert result["documents"] == []
    assert result["context"] == f"{GRAPH_HEADER}\n{E102_750}"

    answer_question_with_graph("For employee E102, process a $750 expense.")

    assert llm_contexts == [result["context"]]


def test_combined_graph_and_document_context(llm_contexts):

    answer_question_with_graph("For employee E102, process a $750 expense.")

    assert llm_contexts == [
        f"{GRAPH_HEADER}\n{E102_750}\n\n{DOCUMENTS_HEADER}\n" + "\n\n".join(CHUNKS)
    ]


def test_context_retrieval_is_read_only(llm_contexts):

    before = graph_size()

    answer_question_with_graph("For employee E101, process a $6000 expense.")
    answer_question_with_graph("E999 spent $12.50")

    assert graph_size() == before


# ============================================================
# WORKFLOW: FINAL LLM CALL GETS GRAPH + DOCUMENT CONTEXT
# ============================================================

def test_workflow_combines_graph_and_document_context(workflow_contexts):

    result = run("For employee E102, process a $750 expense.", decision="approve")

    assert result["status"] == "success"
    assert result["approval_status"] == "approved"

    [context] = workflow_contexts

    assert graph_section(context) == E102_750
    assert "Business Document Context:" in context
    assert "7. Expenses above $500 require manager approval." in context


def test_workflow_without_approval(workflow_contexts):

    result = run("For employee E103, process a $400 expense.")

    assert result["status"] == "success"
    assert result["approval_status"] == "not_required"
    assert graph_section(workflow_contexts[0]).splitlines() == [
        "Employee E103: department Marketing; manager Arjun (Manager).",
        "Expense amount $400.00: no approval required.",
    ]


def test_workflow_graph_failure_keeps_rag_only_behaviour(monkeypatch, workflow_contexts):

    def broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(agent_graph_module, "get_business_context", broken)

    result = run("For employee E103, process a $400 expense.")

    assert result["status"] == "success"
    assert result["action_result"]["success"] is True
    assert graph_section(workflow_contexts[0]) == "No context graph information found."


@pytest.mark.parametrize("document_results", [[]])
def test_graph_context_does_not_replace_missing_documents(workflow_contexts):
    """Recovery on empty document search is unchanged by CG5."""

    result = run("For employee E102, process a $750 expense.")

    assert result["status"] == "failed"
    assert result["failed_node"] == "rag"
    assert workflow_contexts == []
