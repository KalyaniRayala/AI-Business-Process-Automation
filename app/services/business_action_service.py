from datetime import datetime
from uuid import uuid4


def record_expense_reimbursement(
    employee_id: str,
    employee_name: str,
    amount: float,
    department: str,
    manager: str
):
    """
    Record an approved expense reimbursement.

    This is currently a controlled simulation of the
    accounting-system action.
    """

    reimbursement = {
        "transaction_id": (
            f"EXP-{employee_id}-{uuid4().hex[:8].upper()}"
        ),
        "employee_id": employee_id,
        "employee_name": employee_name,
        "amount": amount,
        "department": department,
        "manager": manager,
        "status": "approved",
        "recorded_at": datetime.now().isoformat()
    }

    return {
        "success": True,
        "message": "Expense reimbursement recorded successfully.",
        "reimbursement": reimbursement
    }