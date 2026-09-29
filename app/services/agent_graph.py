from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

from app.services.tool_service import (
    lookup_employee,
    lookup_expense_policy,
    search_business_documents,
)

from app.services.llm_service import generate_answer

from app.services.tool_executor import execute_with_retry

from app.services.business_action_service import (
    record_expense_reimbursement
)


# ============================================================
# STATE
# ============================================================

class AgentState(TypedDict, total=False):

    question: str

    employee_data: dict
    policy_result: dict
    document_context: str

    answer: str

    status: str
    error_type: str
    error_message: str
    failed_node: str

    approval_required: bool
    approval_status: str

    action_result: dict


# ============================================================
# ANALYZE NODE
# ============================================================

def analyze_question(state: AgentState):

    return {}


# ============================================================
# EMPLOYEE NODE
# ============================================================

def employee_node(state: AgentState):

    import re

    question = state["question"]

    match = re.search(
        r"\bE\d+\b",
        question.upper()
    )

    if not match:

        return {
            "employee_data": {
                "success": False,
                "error": "Employee ID not found."
            }
        }

    employee_id = match.group()

    result = execute_with_retry(
        lookup_employee,
        retries=2,
        employee_id=employee_id
    )

    return {
        "employee_data": result
    }


# ============================================================
# POLICY NODE
# ============================================================

def policy_node(state: AgentState):

    question = state["question"]

    amount = None

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

            if value > 0:

                amount = value
                break

        except ValueError:

            continue

    if amount is None:

        return {
            "policy_result": {
                "success": False,
                "error": "Expense amount not found."
            }
        }

    result = execute_with_retry(
        lookup_expense_policy,
        retries=2,
        expense_amount=amount
    )

    return {
        "policy_result": result
    }


# ============================================================
# RAG NODE
# ============================================================

def rag_node(state: AgentState):

    question = state["question"]

    result = execute_with_retry(
        search_business_documents,
        retries=2,
        query=question
    )

    # Leave context empty when the search failed or found
    # nothing, so check_results can route to recovery.
    if (
        not isinstance(result, dict)
        or result.get("success") is False
        or not result.get("results")
    ):

        return {
            "document_context": ""
        }

    return {
        "document_context": str(result)
    }


# ============================================================
# SYNCHRONIZE NODE
# ============================================================

def synchronize_node(state: AgentState):

    return {}


# ============================================================
# CHECK RESULTS
# ============================================================

def check_results(state: AgentState):

    employee_data = state.get(
        "employee_data",
        {}
    )

    policy_result = state.get(
        "policy_result",
        {}
    )

    document_context = state.get(
        "document_context",
        ""
    )

    if (
        isinstance(employee_data, dict)
        and employee_data.get("success") is False
    ):

        return "recovery"

    if (
        isinstance(policy_result, dict)
        and policy_result.get("success") is False
    ):

        return "recovery"

    if not document_context:

        return "recovery"

    return "approval"


# ============================================================
# RECOVERY NODE
# ============================================================

def recovery_node(state: AgentState):

    errors = []

    failed_node = "unknown"

    employee_data = state.get(
        "employee_data",
        {}
    )

    policy_result = state.get(
        "policy_result",
        {}
    )

    document_context = state.get(
        "document_context",
        ""
    )

    if (
        isinstance(employee_data, dict)
        and employee_data.get("success") is False
    ):

        failed_node = "employee"

        errors.append(
            employee_data.get(
                "error",
                "Employee lookup failed."
            )
        )

    if (
        isinstance(policy_result, dict)
        and policy_result.get("success") is False
    ):

        failed_node = "policy"

        errors.append(
            policy_result.get(
                "error",
                "Policy lookup failed."
            )
        )

    if not document_context:

        failed_node = "rag"

        errors.append(
            "Document search returned no context."
        )

    return {

        "status": "failed",

        "error_type": "tool_execution_error",

        "error_message": " | ".join(errors),

        "failed_node": failed_node,

        "answer": (
            "I was unable to complete the request. "
            f"The {failed_node} operation could not be completed."
        )
    }


# ============================================================
# HUMAN APPROVAL NODE
# ============================================================

def approval_node(state: AgentState):

    policy_result = state.get(
        "policy_result",
        {}
    )

    approval_text = ""

    if isinstance(policy_result, dict):

        approval_text = str(
            policy_result.get(
                "approval",
                ""
            )
        ).lower()

    # --------------------------------------------------------
    # No approval required
    # --------------------------------------------------------

    # "No approval required." also contains "approval required",
    # so check for the negative wording explicitly.
    if (
        "approval required" not in approval_text
        or approval_text.startswith("no approval")
    ):

        return {

            "approval_required": False,

            "approval_status": "not_required"
        }

    # --------------------------------------------------------
    # Pause workflow
    # --------------------------------------------------------

    decision = interrupt(

        {
            "type": "approval_required",

            "message": (
                "This expense requires approval "
                "before the workflow can continue."
            ),

            "employee": state.get(
                "employee_data",
                {}
            ),

            "policy": policy_result
        }

    )

    # --------------------------------------------------------
    # Approved
    # --------------------------------------------------------

    if decision == "approve":

        return {

            "approval_required": True,

            "approval_status": "approved"
        }

    # --------------------------------------------------------
    # Rejected
    # --------------------------------------------------------

    return {

        "approval_required": True,

        "approval_status": "rejected"
    }


# ============================================================
# APPROVAL ROUTER
# ============================================================

def approval_router(state: AgentState):

    status = state.get(
        "approval_status",
        ""
    )

    if status == "approved":

        return "action"

    if status == "rejected":

        return "rejection"

    # No approval required: record the reimbursement directly
    if status == "not_required":

        return "action"

    return "final"


# ============================================================
# BUSINESS ACTION NODE
# ============================================================

def action_node(state: AgentState):

    print("\n🔥 ACTION NODE EXECUTED")

    employee_data = state.get(
        "employee_data",
        {}
    )

    employee = employee_data.get(
        "employee",
        {}
    )

    if not employee:

        return {

            "status": "error",

            "error_type": "employee_data_missing",

            "error_message": (
                "Employee information is missing."
            ),

            "failed_node": "action",

            "action_result": {
                "success": False,
                "error": "Employee information is missing."
            }
        }

    policy_result = state.get(
        "policy_result",
        {}
    )

    amount = policy_result.get(
        "expense_amount",
        0
    )

    # --------------------------------------------------------
    # Execute actual business action
    # --------------------------------------------------------

    result = record_expense_reimbursement(

        employee_id=employee.get(
            "employee_id"
        ),

        employee_name=employee.get(
            "name"
        ),

        amount=float(amount),

        department=employee.get(
            "department"
        ),

        manager=employee.get(
            "manager"
        )
    )

    print(
        "\n🔥 ACTION RESULT FROM ACTION NODE"
    )

    print(result)

    return {

        "action_result": result
    }


# ============================================================
# REJECTION NODE
# ============================================================

def rejection_node(state: AgentState):

    employee_data = state.get(
        "employee_data",
        {}
    )

    employee = employee_data.get(
        "employee",
        {}
    )

    employee_name = employee.get(
        "name",
        "Employee"
    )

    return {

        "status": "rejected",

        "approval_status": "rejected",

        "answer": (
            f"The expense approval request for "
            f"{employee_name} was rejected by the "
            f"authorized approver."
        )
    }


# ============================================================
# FINAL NODE
# ============================================================

def final_node(state: AgentState):

    print("\n🔥 FINAL NODE EXECUTED")

    question = state.get(
        "question",
        ""
    )

    approval_status = state.get(
        "approval_status",
        "not_required"
    )

    action_result = state.get(
        "action_result",
        {}
    )

    print(
        "\n🔥 ACTION RESULT INSIDE FINAL NODE"
    )

    print(action_result)

    # --------------------------------------------------------
    # Build context for LLM
    # --------------------------------------------------------

    context = f"""

Employee Information:

{state.get("employee_data", {})}


Expense Policy:

{state.get("policy_result", {})}


Business Document Context:

{state.get("document_context", "")}


Human Approval Status:

{approval_status}


Business Action Result:

{action_result}

"""

    answer = generate_answer(

        question=question,

        context=context
    )

    # --------------------------------------------------------
    # Approval message
    # --------------------------------------------------------

    if approval_status == "approved":

        answer += (

            "\n\nApproval Status: "
            "Manager approval has been granted."
        )

    elif approval_status == "rejected":

        answer += (

            "\n\nApproval Status: "
            "Manager approval has been rejected."
        )

    # --------------------------------------------------------
    # Business action result
    # --------------------------------------------------------

    if (
        isinstance(action_result, dict)
        and action_result.get("success")
    ):

        reimbursement = action_result.get(
            "reimbursement",
            {}
        )

        transaction_id = reimbursement.get(
            "transaction_id",
            "N/A"
        )

        answer += (

            "\n\nBusiness Action: "
            "Expense reimbursement recorded successfully."

            f"\nTransaction ID: {transaction_id}"
        )

    # --------------------------------------------------------
    # Final status: keep rejection / action failure visible
    # --------------------------------------------------------

    if approval_status == "rejected":

        return {

            "status": "rejected",

            "answer": answer
        }

    if (
        isinstance(action_result, dict)
        and action_result.get("success") is False
    ):

        return {

            "status": "failed",

            "answer": answer
        }

    return {

        "status": "success",

        "error_type": "",

        "error_message": "",

        "failed_node": "",

        "answer": answer,

        "action_result": action_result
    }


# ============================================================
# BUILD GRAPH
# ============================================================

graph = StateGraph(AgentState)


# ============================================================
# REGISTER NODES
# ============================================================

graph.add_node(
    "analyze",
    analyze_question
)

graph.add_node(
    "employee",
    employee_node
)

graph.add_node(
    "policy",
    policy_node
)

graph.add_node(
    "rag",
    rag_node
)

graph.add_node(
    "synchronize",
    synchronize_node
)

graph.add_node(
    "check_results",
    lambda state: {}
)

graph.add_node(
    "recovery",
    recovery_node
)

graph.add_node(
    "approval",
    approval_node
)

graph.add_node(
    "action",
    action_node
)

graph.add_node(
    "rejection",
    rejection_node
)

graph.add_node(
    "final",
    final_node
)


# ============================================================
# START
# ============================================================

graph.add_edge(
    START,
    "analyze"
)


# ============================================================
# PARALLEL PROCESSING
# ============================================================

graph.add_edge(
    "analyze",
    "employee"
)

graph.add_edge(
    "analyze",
    "policy"
)

graph.add_edge(
    "analyze",
    "rag"
)


# ============================================================
# SYNCHRONIZATION
# ============================================================

graph.add_edge(
    "employee",
    "synchronize"
)

graph.add_edge(
    "policy",
    "synchronize"
)

graph.add_edge(
    "rag",
    "synchronize"
)


graph.add_edge(
    "synchronize",
    "check_results"
)


# ============================================================
# CHECK RESULTS ROUTER
# ============================================================

graph.add_conditional_edges(

    "check_results",

    check_results,

    {

        "approval": "approval",

        "recovery": "recovery"
    }
)


# ============================================================
# APPROVAL ROUTER
# ============================================================

graph.add_conditional_edges(

    "approval",

    approval_router,

    {

        "action": "action",

        "rejection": "rejection",

        "final": "final"
    }
)


# ============================================================
# IMPORTANT:
# DO NOT ADD:
#
# graph.add_edge("approval", "final")
#
# because approval already has conditional routing.
# ============================================================


# ============================================================
# ACTION → FINAL
# ============================================================

graph.add_edge(
    "action",
    "final"
)


# ============================================================
# REJECTION → FINAL
# ============================================================

graph.add_edge(
    "rejection",
    "final"
)


# ============================================================
# END NODES
# ============================================================

graph.add_edge(
    "final",
    END
)

graph.add_edge(
    "recovery",
    END
)


# ============================================================
# CHECKPOINTING
# ============================================================

checkpointer = MemorySaver()

agent_graph = graph.compile(
    checkpointer=checkpointer
)