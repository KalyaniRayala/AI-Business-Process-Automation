import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.services.audit_service import (
    AuditEventType,
    add_event,
    record_event,
)
from app.services.database_service import to_money
from app.services.db import session_scope
from app.services.db_models import Reimbursement, WorkflowRun


logger = logging.getLogger(__name__)


def record_expense_reimbursement(
    employee_id: str,
    employee_name: str,
    amount: float,
    department: str,
    manager: str,
    thread_id: str | None = None,
    question: str | None = None
):
    """
    Record an approved expense reimbursement in the database.

    When thread_id is given the action is idempotent: a workflow
    records at most one reimbursement, and repeating the call
    returns the existing record.
    """

    try:

        existing = find_reimbursement(thread_id)

        if existing is not None:

            return build_result(
                existing,
                employee_name,
                "Expense reimbursement was already recorded."
            )

        with session_scope() as session:

            # Workflows started outside the API (e.g. scripts)
            # have no run row yet; the reimbursement links to one.
            if (
                thread_id is not None
                and session.get(WorkflowRun, thread_id) is None
            ):

                session.add(
                    WorkflowRun(
                        thread_id=thread_id,
                        question=question or "",
                        status="running"
                    )
                )

                # Insert the run before the reimbursement that
                # references it.
                session.flush()

            reimbursement = Reimbursement(
                transaction_id=(
                    f"EXP-{employee_id}-{uuid4().hex[:8].upper()}"
                ),
                thread_id=thread_id,
                employee_id=employee_id,
                amount=to_money(amount),
                department=department,
                manager=manager,
                status="approved",
                recorded_at=datetime.now(timezone.utc)
            )

            session.add(reimbursement)

            # Same transaction: the event exists only if the
            # reimbursement was recorded. Idempotent repeats return
            # above and add no second event.
            add_event(
                session,
                event_type=AuditEventType.BUSINESS_ACTION_EXECUTED,
                message="Expense reimbursement recorded.",
                status="success",
                thread_id=thread_id,
                node="action",
                entity_type="reimbursement",
                entity_id=reimbursement.transaction_id,
                details={
                    "employee_id": employee_id,
                    "amount": float(reimbursement.amount)
                }
            )

        return build_result(
            reimbursement,
            employee_name,
            "Expense reimbursement recorded successfully."
        )

    except IntegrityError as error:

        # A concurrent call recorded it first.
        existing = find_reimbursement(thread_id)

        if existing is not None:

            return build_result(
                existing,
                employee_name,
                "Expense reimbursement was already recorded."
            )

        logger.exception("Could not record expense reimbursement")

        return action_failed(thread_id, employee_id, amount, error)

    except SQLAlchemyError as error:

        logger.exception("Could not record expense reimbursement")

        return action_failed(thread_id, employee_id, amount, error)


def action_failed(
    thread_id: str | None,
    employee_id: str,
    amount: float,
    error: Exception
) -> dict:
    """
    Audit the failure (best-effort: the database may be the
    cause) and return the existing failure result.
    """

    record_event(
        event_type=AuditEventType.BUSINESS_ACTION_FAILED,
        message="Expense reimbursement could not be recorded.",
        status="failure",
        thread_id=thread_id,
        node="action",
        details={
            "employee_id": employee_id,
            "amount": amount
        },
        error=error
    )

    return {
        "success": False,
        "error": "Could not record the expense reimbursement."
    }


def find_reimbursement(thread_id: str | None):
    """
    Return the reimbursement already recorded for a workflow.
    """

    if thread_id is None:
        return None

    with session_scope() as session:

        return session.scalar(
            select(Reimbursement).where(
                Reimbursement.thread_id == thread_id
            )
        )


def build_result(
    reimbursement: Reimbursement,
    employee_name: str,
    message: str
) -> dict:

    return {
        "success": True,
        "message": message,
        "reimbursement": {
            "transaction_id": reimbursement.transaction_id,
            "employee_id": reimbursement.employee_id,
            "employee_name": employee_name,
            "amount": float(reimbursement.amount),
            "department": reimbursement.department,
            "manager": reimbursement.manager,
            "status": reimbursement.status,
            "recorded_at": reimbursement.recorded_at.isoformat()
        }
    }
