"""
Audit trail: append-only, queryable record of business-process events.

Two ways to write an event:

- add_event(session, ...)   inside the caller's transaction, so the
                            event commits or rolls back together with
                            the business change it describes.
- record_event(...)         best-effort in its own transaction; a
                            database failure is logged and never
                            interrupts the workflow.

Invalid arguments (unknown event type, actor type or status) always
raise, because they are programming errors.

This module only adds events; there is deliberately no update or
delete function.
"""

import json
import logging
import re
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.db import session_scope
from app.services.db_models import AuditEvent


logger = logging.getLogger(__name__)


# ============================================================
# VOCABULARY
# ============================================================

class AuditEventType(StrEnum):

    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_RESUMED = "workflow_resumed"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_REJECTED = "workflow_rejected"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_RECOVERY = "workflow_recovery"
    WORKFLOW_ERROR = "workflow_error"

    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"

    EMPLOYEE_LOOKUP = "employee_lookup"
    POLICY_EVALUATED = "policy_evaluated"
    DOCUMENT_RETRIEVAL = "document_retrieval"
    TOOL_EXECUTION = "tool_execution"

    APPROVAL_NOT_REQUIRED = "approval_not_required"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_DECISION_REJECTED = "approval_decision_rejected"

    BUSINESS_ACTION_EXECUTED = "business_action_executed"
    BUSINESS_ACTION_FAILED = "business_action_failed"

    CONNECTOR_SYNC_STARTED = "connector_sync_started"
    CONNECTOR_SYNC_COMPLETED = "connector_sync_completed"
    CONNECTOR_SYNC_FAILED = "connector_sync_failed"


ACTOR_TYPES = {"system", "human", "agent"}

STATUSES = {"success", "failure", "pending", "info"}


# ============================================================
# DETAILS SANITIZATION
# ============================================================

MAX_DETAILS_BYTES = 8192

MAX_ERROR_MESSAGE_LENGTH = 2000

SENSITIVE_KEY = re.compile(
    r"password|passwd|secret|token|api[_-]?key|authorization|credential",
    re.IGNORECASE
)


def sanitize_details(details: dict | None) -> dict | None:
    """
    Make details safe to store: JSON-compatible, no values under
    sensitive-looking keys, and bounded in size.
    """

    if details is None:
        return None

    if not isinstance(details, dict):
        raise ValueError("Audit details must be a dict.")

    def redact(value):

        if isinstance(value, dict):
            return {
                str(key): (
                    "[redacted]"
                    if SENSITIVE_KEY.search(str(key))
                    else redact(item)
                )
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [redact(item) for item in value]

        return value

    # Round-trip through JSON: Decimal, datetime, UUID etc.
    # become strings.
    safe = json.loads(
        json.dumps(redact(details), default=str)
    )

    size = len(json.dumps(safe).encode("utf-8"))

    if size > MAX_DETAILS_BYTES:

        logger.warning(
            "Audit details too large (%d bytes); stored a summary.",
            size
        )

        return {
            "truncated": True,
            "original_size_bytes": size,
            "keys": sorted(safe.keys())[:50]
        }

    return safe


# ============================================================
# WRITE
# ============================================================

def validate_event(event_type, status: str, actor_type: str) -> str:
    """
    Check the controlled values; return the event type string.
    """

    event_type = AuditEventType(event_type).value

    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"Invalid audit actor_type: {actor_type!r}")

    if status not in STATUSES:
        raise ValueError(f"Invalid audit status: {status!r}")

    return event_type


def add_event(
    session: Session,
    *,
    event_type: AuditEventType | str,
    message: str,
    status: str = "info",
    actor_type: str = "system",
    actor_id: str | None = None,
    thread_id: str | None = None,
    node: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
    error: BaseException | None = None,
    error_type: str | None = None,
    error_message: str | None = None
) -> AuditEvent:
    """
    Add an audit event to the caller's session (same transaction).
    """

    event_type = validate_event(event_type, status, actor_type)

    if error is not None:
        error_type = error_type or type(error).__name__
        error_message = error_message or str(error)

    if error_message is not None:
        error_message = error_message[:MAX_ERROR_MESSAGE_LENGTH]

    event = AuditEvent(
        event_type=event_type,
        message=message,
        status=status,
        actor_type=actor_type,
        actor_id=actor_id,
        thread_id=thread_id,
        node=node,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        details=sanitize_details(details),
        error_type=error_type,
        error_message=error_message
    )

    session.add(event)

    return event


def record_event(**event) -> bool:
    """
    Best-effort audit write in its own transaction.

    Returns False (and logs) if the database write fails; never
    raises for database errors. Argument errors still raise.
    """

    # Validate first so programming errors are not swallowed.
    validate_event(
        event["event_type"],
        event.get("status", "info"),
        event.get("actor_type", "system")
    )

    try:

        with session_scope() as session:
            add_event(session, **event)

    except Exception:

        logger.exception(
            "Could not record audit event %s for thread %s",
            event.get("event_type"),
            event.get("thread_id")
        )

        return False

    return True


# ============================================================
# READ
# ============================================================

MAX_PAGE_SIZE = 500


def list_events(
    thread_id: str | None = None,
    event_type: AuditEventType | str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    after_id: int | None = None,
    limit: int = 100
) -> list[dict]:
    """
    Return audit events oldest first, in recording order.

    Paginate with after_id = the last returned "id".
    """

    limit = max(1, min(limit, MAX_PAGE_SIZE))

    query = select(AuditEvent)

    if thread_id is not None:
        query = query.where(AuditEvent.thread_id == thread_id)

    if event_type is not None:
        query = query.where(
            AuditEvent.event_type == AuditEventType(event_type).value
        )

    if entity_type is not None:
        query = query.where(AuditEvent.entity_type == entity_type)

    if entity_id is not None:
        query = query.where(AuditEvent.entity_id == str(entity_id))

    if after_id is not None:
        query = query.where(AuditEvent.id > after_id)

    # Recording order. Ordering by id (not occurred_at) keeps
    # after_id pagination exact even when concurrent transactions
    # commit slightly out of timestamp order.
    query = query.order_by(AuditEvent.id).limit(limit)

    with session_scope() as session:

        return [
            to_dict(event)
            for event in session.scalars(query)
        ]


def to_dict(event: AuditEvent) -> dict:

    return {
        "id": event.id,
        "event_id": str(event.event_id),
        "occurred_at": event.occurred_at.isoformat(),
        "thread_id": event.thread_id,
        "event_type": event.event_type,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "node": event.node,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "status": event.status,
        "message": event.message,
        "details": event.details,
        "error_type": event.error_type,
        "error_message": event.error_message
    }
