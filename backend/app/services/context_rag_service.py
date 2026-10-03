"""
CG5: combine Context Graph facts with retrieved document chunks
before LLM generation.

Graph context is informational: lookup_expense_policy remains the
authoritative approval logic, and a graph lookup failure never fails
an answer (the document context is still used).
"""

import logging
import re

from app.services.context_graph_service import get_applicable_rules
from app.services.llm_service import generate_answer
from app.services.rag_service import retrieve_context
from app.services.tool_service import (
    EXPENSE_PROCESS_KEY,
    describe_rules,
    get_business_context,
)


logger = logging.getLogger(__name__)


GRAPH_HEADER = "Context graph:"
DOCUMENTS_HEADER = "Business documents:"

# Same pattern as employee_node in agent_graph.
EMPLOYEE_ID_IN_TEXT = re.compile(r"\bE\d+\b")


# ============================================================
# QUESTION PARSING (same rules as employee_node / policy_node)
# ============================================================

def extract_employee_id(question: str) -> str | None:

    match = EMPLOYEE_ID_IN_TEXT.search(question.upper())

    return match.group() if match else None


def extract_expense_amount(question: str) -> float | None:

    words = (
        question
        .replace("$", "")
        .replace(",", "")
        .replace("?", "")
        .split()
    )

    for word in words:

        try:
            value = float(word)
        except ValueError:
            continue

        if value > 0:
            return value

    return None


# ============================================================
# GRAPH CONTEXT
# ============================================================

def retrieve_graph_context(
    employee_id: str | None = None,
    expense_amount: float | None = None
) -> str:
    """
    Graph facts relevant to an employee and/or expense amount, as
    prompt text. Empty when nothing relevant is found or the graph
    lookup fails.
    """

    try:

        if employee_id:

            context = get_business_context(employee_id, expense_amount)

            return format_graph_context(context) if context["success"] else ""

        if expense_amount is not None:

            rules = get_applicable_rules(EXPENSE_PROCESS_KEY, expense_amount)

            if rules is None:
                return ""

            return format_graph_context({
                "employee": None,
                "expense_amount": expense_amount,
                **describe_rules(rules),
            })

    except Exception:

        logger.warning("Context graph retrieval failed", exc_info=True)

    return ""


def format_graph_context(context: dict) -> str:
    """Prompt text for a get_business_context()-shaped result."""

    lines = []

    employee = context.get("employee")

    if employee:

        departments = ", ".join(employee["departments"]) or "not recorded"

        managers = ", ".join(
            f"{m['name']} ({', '.join(m['roles'])})" if m["roles"] else m["name"]
            for m in employee["managers"]
        ) or "not recorded"

        lines.append(
            f"Employee {employee['employee_id']}: department {departments}; "
            f"manager {managers}."
        )

    amount = context.get("expense_amount")

    if amount is not None:

        approvals = context["required_approvals"]

        lines.append(
            f"Expense amount ${amount:,.2f}: "
            + (
                f"approval required from {', '.join(approvals)}."
                if approvals else "no approval required."
            )
        )

        for rule in context["applicable_rules"]:

            lines.append(
                f"Rule {rule['rule']}: {rule['description']} "
                f"(threshold ${rule['threshold']:,}; "
                f"approvers: {', '.join(rule['approver_roles'])}; "
                f"process steps: {'; '.join(rule['approval_steps'])}; "
                f"evidence: {', '.join(rule['evidence'])})."
            )

    return "\n".join(lines)


# ============================================================
# WORKFLOW DECISION SUPPORT (CG6)
# ============================================================

def policy_approvers(policy_result: dict) -> list[str] | None:
    """
    Approver roles implied by lookup_expense_policy's approval text,
    in the same order the graph uses. None if the policy lookup
    failed.
    """

    if not isinstance(policy_result, dict) or policy_result.get("success") is False:
        return None

    text = str(policy_result.get("approval", "")).lower()

    if not text or text.startswith("no approval"):
        return []

    return [
        role for role, word in (("Manager", "manager"), ("Finance", "finance"))
        if word in text
    ]


def check_policy_consistency(policy_result: dict, business_context: dict) -> dict | None:
    """
    Compare the approvers the graph requires with those the
    authoritative policy tool requires. Informational only: the
    policy tool still decides whether approval is needed.

    None when either side is unavailable.
    """

    if not isinstance(business_context, dict) or not business_context.get("success"):
        return None

    graph_approvers = business_context.get("required_approvals")
    tool_approvers = policy_approvers(policy_result)

    if graph_approvers is None or tool_approvers is None:
        return None

    return {
        "consistent": graph_approvers == tool_approvers,
        "graph_approvers": graph_approvers,
        "policy_approvers": tool_approvers,
    }


def summarize_for_approval(policy_result: dict, business_context: dict) -> dict | None:
    """
    What a human approver should know from the graph: who must
    approve, the employee's manager and the rules with their
    document evidence. None when graph context is unavailable.
    """

    if not isinstance(business_context, dict) or not business_context.get("success"):
        return None

    employee = business_context["employee"]
    rules = business_context.get("applicable_rules") or []

    return {
        "departments": employee["departments"],
        "managers": employee["managers"],
        "required_approvals": business_context.get("required_approvals"),
        "rules": [
            {"rule": r["rule"], "description": r["description"], "evidence": r["evidence"]}
            for r in rules
        ],
        "policy_check": check_policy_consistency(policy_result, business_context),
    }


# ============================================================
# COMBINED CONTEXT
# ============================================================

def combine_context(document_chunks: list[str], graph_context: str) -> str:
    """
    Graph facts plus document chunks. Without graph facts this is
    exactly the document-only context used by answer_question.
    """

    documents = "\n\n".join(document_chunks)

    if not graph_context:
        return documents

    if not documents:
        return f"{GRAPH_HEADER}\n{graph_context}"

    return (
        f"{GRAPH_HEADER}\n{graph_context}\n\n"
        f"{DOCUMENTS_HEADER}\n{documents}"
    )


def build_question_context(question: str, top_k: int = 3) -> dict:

    documents = retrieve_context(question, top_k)

    graph_context = retrieve_graph_context(
        employee_id=extract_employee_id(question),
        expense_amount=extract_expense_amount(question),
    )

    return {
        "documents": documents,
        "graph_context": graph_context,
        "context": combine_context(documents, graph_context),
    }


def answer_question_with_graph(question: str, top_k: int = 3) -> str:
    """answer_question, with relevant Context Graph facts added."""

    context = build_question_context(question, top_k)["context"]

    return generate_answer(
        question=question,
        context=context
    )
