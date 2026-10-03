import re

from app.services.database_service import get_employee


def lookup_expense_policy(expense_amount: float):

    if expense_amount < 0:
        return {
            "success": False,
            "error": "Expense amount cannot be negative."
        }

    if expense_amount > 5000:
        approval = "Manager approval and finance approval required."

    elif expense_amount > 500:
        approval = "Manager approval required."

    else:
        approval = "No approval required."

    return {
        "success": True,
        "expense_amount": expense_amount,
        "approval": approval
    }


def approval_is_required(policy_result) -> bool:
    """
    Whether a lookup_expense_policy() result means the workflow must
    pause for a human. The approval node and the policy eval both
    read the decision from here, so they cannot drift apart.
    """

    approval_text = ""

    if isinstance(policy_result, dict):
        approval_text = str(policy_result.get("approval", "")).lower()

    # "No approval required." also contains "approval required",
    # so check for the negative wording explicitly.
    if "approval required" not in approval_text:
        return False

    return not approval_text.startswith("no approval")
expense_policy_tool = {
    "type": "function",
    "function": {
        "name": "lookup_expense_policy",
        "description": (
            "Check whether an expense requires manager "
            "or finance approval based on its amount."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expense_amount": {
                    "type": "number",
                    "description": "The expense amount."
                }
            },
            "required": ["expense_amount"]
        }
    }
}
def lookup_employee(employee_id: str):

    employee = get_employee(employee_id)

    if employee is None:
        return {
            "success": False,
            "error": f"Employee {employee_id} was not found."
        }

    return {
        "success": True,
        "employee": employee
    }
employee_lookup_tool = {
    "type": "function",
    "function": {
        "name": "lookup_employee",
        "description": (
            "Look up an employee's information from the company "
            "database using the employee ID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "The employee ID, such as E102."
                }
            },
            "required": ["employee_id"]
        }
    }
}
from app.services.rag_service import retrieve_context


def search_business_documents(query: str) -> dict:
    """
    Search the business documents using the existing
    ChromaDB + Cohere retrieval pipeline.
    """

    contexts = retrieve_context(
        query,
        top_k=3
    )

    return {
        "query": query,
        "results": contexts
    }
document_search_tool = {
    "type": "function",
    "function": {
        "name": "search_business_documents",
        "description": (
            "Search the company's uploaded business documents "
            "for policies, processes, rules, approvals, "
            "responsibilities, and other business information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The information to search for in "
                        "the business documents."
                    )
                }
            },
            "required": ["query"]
        }
    }
}
from app.services.context_graph_service import (
    get_applicable_rules,
    get_employee_context,
)


EXPENSE_PROCESS_KEY = "process:expense_reimbursement"

# Same shape as employee ids in the employees table (e.g. E102).
EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


def get_business_context(
    employee_id: str,
    expense_amount: float | None = None
) -> dict:
    """
    Business context from the Context Graph: the employee's
    department and manager, and (when an amount is given) the
    expense policy rules that apply, who must approve, which
    process steps they govern, and the document evidence.

    Read-only and informational: lookup_expense_policy remains the
    authoritative approval logic.
    """

    if (
        not isinstance(employee_id, str)
        or not EMPLOYEE_ID_PATTERN.match(employee_id)
    ):
        return {
            "success": False,
            "error": f"Invalid employee id: {employee_id!r}."
        }

    if expense_amount is not None and expense_amount < 0:
        return {
            "success": False,
            "error": "Expense amount cannot be negative."
        }

    context = get_employee_context(employee_id)

    if context is None or not context["employee"]["is_active"]:
        return {
            "success": False,
            "error": f"Employee {employee_id} was not found in the context graph."
        }

    result = {
        "success": True,
        "source": "context_graph",
        "employee": {
            "employee_id": employee_id,
            "departments": [d["label"] for d in context["departments"]],
            "managers": [
                {
                    "name": manager["person"]["label"],
                    "roles": [role["label"] for role in manager["roles"]],
                }
                for manager in context["managers"]
            ],
        },
        "process": EXPENSE_PROCESS_KEY,
        "expense_amount": expense_amount,
        "approval_required": None,
        "required_approvals": None,
        "applicable_rules": None,
    }

    if expense_amount is None:
        return result

    rules = get_applicable_rules(EXPENSE_PROCESS_KEY, expense_amount)

    if rules is None:
        return {
            "success": False,
            "error": f"Process {EXPENSE_PROCESS_KEY} was not found in the context graph."
        }

    result.update(describe_rules(rules))

    return result


def describe_rules(rules: list[dict]) -> dict:
    """Approval summary of get_applicable_rules() output."""

    required = []

    for rule in rules:
        for role in rule["approver_roles"]:
            if role["label"] not in required:
                required.append(role["label"])

    return {
        "approval_required": bool(required),
        "required_approvals": required,
        "applicable_rules": [
            {
                "rule": rule["rule"]["node_key"],
                "description": rule["rule"]["label"],
                "threshold": rule["threshold"],
                "approver_roles": [role["label"] for role in rule["approver_roles"]],
                "approval_steps": [step["label"] for step in rule["governs_steps"]],
                "evidence": rule["evidence"],
            }
            for rule in rules
        ],
    }
business_context_tool = {
    "type": "function",
    "function": {
        "name": "get_business_context",
        "description": (
            "Get business context for an employee from the company "
            "context graph: department, manager, and for an expense "
            "amount the applicable approval rules, required approver "
            "roles, governed process steps and document evidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "employee_id": {
                    "type": "string",
                    "description": "The employee ID, such as E102."
                },
                "expense_amount": {
                    "type": "number",
                    "description": (
                        "Optional expense amount to evaluate the "
                        "approval rules for."
                    )
                }
            },
            "required": ["employee_id"]
        }
    }
}
