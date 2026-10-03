"""
Shared helpers for the workflow discovery and automation
recommendation tests: synthetic audit events with exact timestamps.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.db import session_scope
from app.services.db_models import AuditEvent


UTC = timezone.utc

# Synthetic runs live far in the future so they never overlap the
# real runs other tests record; each test uses its own thread ids.
BASE = datetime(2030, 1, 1, tzinfo=UTC)

PARALLEL = ("employee", "policy", "rag")


def standard_events(
    *,
    finish_order=PARALLEL,
    approval=None,          # None | "approve" | "reject" | "pending"
    wait=60.0,
    fail_node=None,
    terminal=True,
):
    """(seconds, event_type, node) for one run, in recording order."""

    t = 0.0
    rows = [(t, "workflow_started", None)]

    t += 0.1

    for node in PARALLEL:
        rows.append((t, "node_started", node))

    for node in finish_order:
        t += 0.1
        rows.append((t, "node_failed" if node == fail_node else "node_completed", node))

    if fail_node:

        rows += [
            (t + 0.1, "node_started", "recovery"),
            (t + 0.2, "node_completed", "recovery"),
            (t + 0.3, "workflow_recovery", "recovery"),
            (t + 0.4, "workflow_failed", fail_node),
        ]

        return rows

    t += 0.1
    rows.append((t, "node_started", "approval"))

    if approval is None:

        t += 0.1
        rows.append((t, "node_completed", "approval"))

    else:

        t += 0.1
        rows.append((t, "approval_requested", "approval"))

        if approval == "pending":
            return rows

        t += wait
        rows.append((
            t, "approval_approved" if approval == "approve" else "approval_rejected",
            "approval",
        ))
        rows.append((t + 0.01, "workflow_resumed", "approval"))

        t += 0.1
        rows.append((t, "node_started", "approval"))
        t += 0.1
        rows.append((t, "node_completed", "approval"))

    if approval == "reject":

        t += 0.1
        rows += [(t, "node_started", "rejection"), (t + 0.1, "node_completed", "rejection")]
        t += 0.1

    else:

        t += 0.1
        rows += [(t, "node_started", "action"), (t + 0.3, "node_completed", "action")]
        t += 0.3

    t += 0.1
    rows += [(t, "node_started", "final"), (t + 0.4, "node_completed", "final")]
    t += 0.4

    if terminal:
        rows.append((
            t + 0.1,
            "workflow_rejected" if approval == "reject" else "workflow_completed",
            "final",
        ))

    return rows


def emit(thread_id: str, rows, *, base: datetime = BASE) -> str:
    """Insert synthetic audit events (inserts are allowed; the table
    is append-only)."""

    with session_scope() as session:

        for seconds, event_type, node in rows:

            session.add(AuditEvent(
                thread_id=thread_id,
                event_type=event_type,
                node=node,
                actor_type="system",
                status="info",
                message="synthetic",
                occurred_at=base + timedelta(seconds=seconds),
            ))

    return thread_id


def new_threads(count: int, **kwargs) -> list[str]:

    namespace = uuid4().hex[:12]

    return [
        emit(f"disc-{namespace}-{n}", standard_events(**kwargs))
        for n in range(count)
    ]


def run_real_workflows(client) -> list[str]:

    def start(question):
        return client.post("/workflows", json={"question": question}).json()["thread_id"]

    def decide(thread_id, decision):
        client.post(f"/workflows/{thread_id}/decision", json={"decision": decision})

    threads = [
        start("For employee E101, process a $400 expense."),
        start("For employee E103, process a $300 expense."),
    ]

    approved = start("For employee E102, process a $750 expense.")
    decide(approved, "approve")

    rejected = start("For employee E102, process a $900 expense.")
    decide(rejected, "reject")

    threads += [
        approved, rejected,
        start("For employee E999, process a $100 expense."),          # recovery
        start("For employee E101, process a $6000 expense."),         # left pending
    ]

    return threads
