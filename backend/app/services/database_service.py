from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.services.audit_service import AuditEventType, add_event
from app.services.db import session_scope
from app.services.db_models import (
    ApprovalDecision,
    Employee,
    WorkflowRun,
)


ALEMBIC_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "alembic.ini"
)


EMPLOYEES = [
    {
        "employee_id": "E101",
        "name": "Anita",
        "department": "Engineering",
        "manager": "Ravi",
        "annual_budget": 50000
    },
    {
        "employee_id": "E102",
        "name": "Rahul",
        "department": "Finance",
        "manager": "Priya",
        "annual_budget": 75000
    },
    {
        "employee_id": "E103",
        "name": "Sneha",
        "department": "Marketing",
        "manager": "Arjun",
        "annual_budget": 40000
    },
    {
        "employee_id": "E104",
        "name": "Kiran",
        "department": "Engineering",
        "manager": "Ravi",
        "annual_budget": 60000
    }
]


def run_migrations():
    """
    Bring the database schema up to date with Alembic.
    """

    command.upgrade(
        Config(str(ALEMBIC_CONFIG_PATH)),
        "head"
    )


def initialize_database():
    """
    Apply schema migrations and insert
    initial enterprise data.
    """

    run_migrations()

    with session_scope() as session:

        for employee in EMPLOYEES:

            # Insert only missing employees (idempotent seed).
            if session.get(Employee, employee["employee_id"]) is None:

                session.add(
                    Employee(**employee)
                )


def get_employee(employee_id: str):
    """
    Retrieve an employee using employee ID.
    """

    with session_scope() as session:

        employee = session.get(
            Employee,
            employee_id
        )

        if employee is None:
            return None

        return {
            "employee_id": employee.employee_id,
            "name": employee.name,
            "department": employee.department,
            "manager": employee.manager,
            "annual_budget": float(employee.annual_budget)
        }


# ============================================================
# WORKFLOW RUNS
# ============================================================

def create_workflow_run(thread_id: str, question: str):
    """
    Register a workflow run (idempotent).
    """

    with session_scope() as session:

        if session.get(WorkflowRun, thread_id) is None:

            session.add(
                WorkflowRun(
                    thread_id=thread_id,
                    question=question,
                    status="running"
                )
            )

            # Same transaction as the run row. The question is not
            # copied; it is stored on workflow_runs.
            add_event(
                session,
                event_type=AuditEventType.WORKFLOW_STARTED,
                message="Workflow started.",
                thread_id=thread_id,
                entity_type="workflow_run",
                entity_id=thread_id
            )


def update_workflow_run(thread_id: str, **fields):
    """
    Update the stored summary of a workflow run.

    Accepted fields: status, approval_status, failed_node,
    error_message, employee_id, expense_amount.
    """

    with session_scope() as session:

        run = session.get(WorkflowRun, thread_id)

        if run is None:
            return

        for name, value in fields.items():
            setattr(run, name, value)


def to_utc_iso(value: datetime) -> str:
    """
    ISO 8601 in UTC. PostgreSQL returns timestamps in the session time
    zone and SQLite returns naive UTC; this makes both look the same.
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat()


WORKFLOW_STATUSES = (
    "running", "awaiting_approval", "success", "rejected", "failed"
)

MAX_WORKFLOW_PAGE_SIZE = 200


def list_workflow_runs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0
) -> dict:
    """
    Stored summaries of workflow runs, most recently started first.

    Optionally only runs with this status. Returns the page and the
    total number of matching runs.
    """

    if status is not None and status not in WORKFLOW_STATUSES:
        raise ValueError(f"Invalid workflow status: {status!r}")

    limit = max(1, min(limit, MAX_WORKFLOW_PAGE_SIZE))
    offset = max(0, offset)

    query = select(WorkflowRun)
    total = select(func.count()).select_from(WorkflowRun)

    if status is not None:
        query = query.where(WorkflowRun.status == status)
        total = total.where(WorkflowRun.status == status)

    # thread_id keeps the order stable for runs created in the
    # same instant.
    query = (
        query
        .order_by(WorkflowRun.created_at.desc(), WorkflowRun.thread_id)
        .limit(limit)
        .offset(offset)
    )

    with session_scope() as session:

        return {
            "workflows": [
                {
                    "thread_id": run.thread_id,
                    "question": run.question,
                    "employee_id": run.employee_id,
                    "expense_amount": (
                        float(run.expense_amount)
                        if run.expense_amount is not None
                        else None
                    ),
                    "status": run.status,
                    "approval_status": run.approval_status,
                    "failed_node": run.failed_node,
                    "error_message": run.error_message,
                    "created_at": to_utc_iso(run.created_at),
                    "updated_at": to_utc_iso(run.updated_at),
                }
                for run in session.scalars(query)
            ],
            "total": session.scalar(total),
        }


def get_workflow_run(thread_id: str):
    """
    Retrieve the stored summary of a workflow run.
    """

    with session_scope() as session:

        run = session.get(WorkflowRun, thread_id)

        if run is None:
            return None

        return {
            "thread_id": run.thread_id,
            "question": run.question,
            "employee_id": run.employee_id,
            "expense_amount": (
                float(run.expense_amount)
                if run.expense_amount is not None
                else None
            ),
            "status": run.status,
            "approval_status": run.approval_status,
            "failed_node": run.failed_node,
            "error_message": run.error_message
        }


# ============================================================
# APPROVAL DECISIONS
# ============================================================

def record_approval_decision(
    thread_id: str,
    decision: str,
    approver: str | None = None,
    comment: str | None = None
) -> bool:
    """
    Store a human approval decision.

    Returns False if the workflow was already decided. The
    unique constraint on thread_id also covers concurrent
    requests that both pass the existence check.
    """

    try:

        with session_scope() as session:

            existing = session.scalar(
                select(ApprovalDecision).where(
                    ApprovalDecision.thread_id == thread_id
                )
            )

            if existing is not None:
                return False

            approval_decision = ApprovalDecision(
                thread_id=thread_id,
                decision=decision,
                approver=approver,
                comment=comment
            )

            session.add(approval_decision)

            # Assigns the decision id; a duplicate decision fails
            # here and rolls back the event with it.
            session.flush()

            approved = decision == "approve"

            add_event(
                session,
                event_type=(
                    AuditEventType.APPROVAL_APPROVED
                    if approved
                    else AuditEventType.APPROVAL_REJECTED
                ),
                message=(
                    "Expense approved by the approver."
                    if approved
                    else "Expense rejected by the approver."
                ),
                status="success",
                actor_type="human",
                actor_id=approver,
                thread_id=thread_id,
                node="approval",
                entity_type="approval_decision",
                entity_id=approval_decision.id,
                details={
                    "decision": decision,
                    # The comment itself stays on approval_decisions.
                    "comment_provided": bool(comment),
                    # Approver identity is caller-supplied until
                    # authentication exists.
                    "approver_authenticated": False
                }
            )

    except IntegrityError:

        return False

    return True


def get_approval_decision(thread_id: str):
    """
    Retrieve the approval decision for a workflow run.
    """

    with session_scope() as session:

        decision = session.scalar(
            select(ApprovalDecision).where(
                ApprovalDecision.thread_id == thread_id
            )
        )

        if decision is None:
            return None

        return {
            "thread_id": decision.thread_id,
            "decision": decision.decision,
            "approver": decision.approver,
            "comment": decision.comment,
            "decided_at": decision.decided_at.isoformat()
        }


def to_money(amount) -> Decimal:
    """
    Convert an amount to a 2-decimal Decimal for NUMERIC columns.
    """

    return Decimal(str(amount)).quantize(Decimal("0.01"))
