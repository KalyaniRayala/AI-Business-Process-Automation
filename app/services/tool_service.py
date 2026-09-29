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