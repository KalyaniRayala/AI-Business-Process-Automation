"""
Node-level audit events, observed from outside the graph.

The API runs the graph with stream_mode="tasks", which reports when
each node starts and what it returned. NodeAuditor turns those task
reports into node_started / node_completed / node_failed events.
The graph's nodes and routing are not modified.

Auditing is best-effort: observer errors and database errors are
logged and never interrupt the workflow.
"""

import logging

from app.services.audit_service import AuditEventType, record_event


logger = logging.getLogger(__name__)


# Business nodes. The no-op plumbing nodes (analyze, synchronize,
# check_results) are not audited.
AUDITED_NODES = {
    "employee",
    "policy",
    "rag",
    "approval",
    "action",
    "rejection",
    "final",
    "recovery",
}


class NodeAuditor:

    def __init__(self, thread_id: str, resumed: bool = False):

        self.thread_id = thread_id
        self.resumed = resumed

        # Tasks that started but have not reported a result.
        self.pending: dict[str, str] = {}

    # --------------------------------------------------------
    # Stream handling
    # --------------------------------------------------------

    def observe(self, task: dict) -> None:
        """
        Handle one stream_mode="tasks" item.
        """

        try:

            name = task.get("name")

            if name not in AUDITED_NODES:
                return

            if "input" in task:
                self.started(task["id"], name)
            else:
                self.finished(task["id"], name, task)

        except Exception:

            logger.exception(
                "Could not audit task for thread %s", self.thread_id
            )

    def fail_pending(self, error: BaseException) -> None:
        """
        The graph raised: nodes that started but never finished
        are the ones that failed.
        """

        try:

            for name in self.pending.values():

                self.record(
                    AuditEventType.NODE_FAILED,
                    name,
                    status="failure",
                    message=f"Node '{name}' raised an error.",
                    details={"raised": True},
                    error=error
                )

            self.pending.clear()

        except Exception:

            logger.exception(
                "Could not audit failed nodes for thread %s",
                self.thread_id
            )

    # --------------------------------------------------------
    # Events
    # --------------------------------------------------------

    def started(self, task_id: str, name: str) -> None:

        self.pending[task_id] = name

        self.record(
            AuditEventType.NODE_STARTED,
            name,
            status="info",
            message=f"Node '{name}' started."
        )

    def finished(self, task_id: str, name: str, task: dict) -> None:

        self.pending.pop(task_id, None)

        # Paused for human approval: the API records
        # approval_requested for this.
        if task.get("interrupts"):
            return

        result = task.get("result") or {}

        failure = node_failure(name, result)

        if failure is not None:

            self.record(
                AuditEventType.NODE_FAILED,
                name,
                status="failure",
                message=f"Node '{name}' reported a failure.",
                details={"raised": False},
                error_message=failure
            )

            return

        entity_type, entity_id = node_entity(name, result)

        self.record(
            AuditEventType.NODE_COMPLETED,
            name,
            status="success",
            message=f"Node '{name}' completed.",
            details=node_summary(name, result),
            entity_type=entity_type,
            entity_id=entity_id
        )

    def record(self, event_type, name: str, *, details=None, **fields):

        if self.resumed:
            details = {**(details or {}), "resumed": True}

        record_event(
            event_type=event_type,
            thread_id=self.thread_id,
            node=name,
            details=details,
            **fields
        )


# ============================================================
# RESULT INTERPRETATION (read-only views of node outputs)
# ============================================================

def node_failure(name: str, result: dict) -> str | None:
    """
    Error message if the node's own output reports a failure.
    """

    checks = {
        "employee": "employee_data",
        "policy": "policy_result",
        "action": "action_result",
    }

    key = checks.get(name)

    if key:

        output = result.get(key) or {}

        if output.get("success") is False:
            return str(output.get("error") or "Operation failed.")

    if name == "rag" and not result.get("document_context"):
        return "Document search returned no context."

    return None


def node_entity(name: str, result: dict) -> tuple[str | None, str | None]:

    if name == "employee":

        employee = (result.get("employee_data") or {}).get("employee") or {}

        if employee.get("employee_id"):
            return "employee", employee["employee_id"]

    if name == "action":

        reimbursement = (
            (result.get("action_result") or {}).get("reimbursement") or {}
        )

        if reimbursement.get("transaction_id"):
            return "reimbursement", reimbursement["transaction_id"]

    return None, None


def node_summary(name: str, result: dict) -> dict | None:
    """
    Small, non-sensitive summary. Documents, answers and personal
    data are not copied into the audit trail.
    """

    if name == "policy":

        policy = result.get("policy_result") or {}

        return {
            "expense_amount": policy.get("expense_amount"),
            "approval": policy.get("approval")
        }

    if name == "rag":

        return {
            "context_characters": len(result.get("document_context") or "")
        }

    if name == "approval":

        return {
            "approval_status": result.get("approval_status"),
            "approval_required": result.get("approval_required")
        }

    if name == "rejection":

        return {"approval_status": result.get("approval_status")}

    if name == "recovery":

        return {"failed_node": result.get("failed_node")}

    if name == "final":

        return {"workflow_status": result.get("status")}

    return None
