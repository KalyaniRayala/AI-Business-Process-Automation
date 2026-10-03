"""
Where the measurements are taken.

Two places matter: the calls that leave the process (the model
provider, which is the slowest and the only one that costs money)
and the workflow nodes (where a run spends its time and where it
fails).

The workflow observer reads the same task stream the audit
observer already consumes, so nodes and routing are untouched.
"""

from __future__ import annotations

import functools
import logging
import time
from datetime import datetime, timezone

from app.observability import metrics
from app.observability.spans import record_span


logger = logging.getLogger(__name__)


PROVIDER = "cohere"


# ============================================================
# MODEL CALLS
# ============================================================

def measure_model_call(operation: str, provider: str = PROVIDER):
    """
    Time a call to the model provider, count its outcome, and keep
    it in recent activity. The wrapped function's behaviour, result
    and exceptions are unchanged.
    """

    def decorator(function):

        @functools.wraps(function)
        def wrapper(*args, **kwargs):

            started_at = datetime.now(timezone.utc)
            started = time.perf_counter()

            status = "success"
            error = None

            try:
                return function(*args, **kwargs)

            except BaseException as raised:
                status = "error"
                error = raised
                raise

            finally:

                duration = time.perf_counter() - started

                try:
                    metrics.model_calls.inc(
                        provider=provider, operation=operation, status=status
                    )
                    metrics.model_duration.observe(
                        duration, provider=provider, operation=operation
                    )

                except Exception:
                    logger.exception("Could not record the %s call", operation)

                record_span(
                    operation=f"{provider}.{operation}",
                    status=status,
                    duration=duration,
                    started_at=started_at,
                    attributes={"provider": provider, "operation": operation},
                    error=error,
                )

        return wrapper

    return decorator


# ============================================================
# WORKFLOW NODES
# ============================================================

class WorkflowObserver:
    """
    Times each node of one graph run.

    Fed from `stream_mode="tasks"`, exactly like NodeAuditor: a
    task with an `input` has started, anything else has finished.
    Measuring is best-effort and never interrupts the workflow.
    """

    def __init__(self, thread_id: str, operation: str = "run") -> None:

        self.thread_id = thread_id
        self.operation = operation

        # task id -> (node name, started_at, perf counter)
        self.pending: dict[str, tuple[str, datetime, float]] = {}

    def observe(self, task: dict) -> None:

        try:

            from app.services.workflow_audit import AUDITED_NODES, node_failure

            name = task.get("name")

            if name not in AUDITED_NODES:
                return

            if "input" in task:
                self.pending[task["id"]] = (
                    name,
                    datetime.now(timezone.utc),
                    time.perf_counter(),
                )
                return

            start = self.pending.pop(task["id"], None)

            if start is None:
                return

            _, started_at, started = start

            if task.get("interrupts"):
                # Paused for a human: the time that follows is
                # waiting, not work, so it is not a node duration.
                status = "paused"
            else:
                result = task.get("result") or {}
                status = (
                    "failure" if node_failure(name, result) is not None else "success"
                )

            self.finish(name, status, started_at, time.perf_counter() - started)

        except Exception:

            logger.exception(
                "Could not measure a task for thread %s", self.thread_id
            )

    def fail_pending(self, error: BaseException) -> None:
        """The graph raised: whatever had started is what failed."""

        try:

            for name, started_at, started in list(self.pending.values()):

                self.finish(
                    name,
                    "error",
                    started_at,
                    time.perf_counter() - started,
                    error=error,
                )

            self.pending.clear()

        except Exception:

            logger.exception(
                "Could not measure the failed nodes for thread %s",
                self.thread_id,
            )

    def finish(
        self,
        name: str,
        status: str,
        started_at: datetime,
        duration: float,
        error: BaseException | None = None,
    ) -> None:

        try:
            metrics.workflow_nodes.inc(node=name, status=status)

            if status != "paused":
                metrics.workflow_node_duration.observe(duration, node=name)

        except Exception:
            logger.exception("Could not record the %s node", name)

        record_span(
            operation=f"workflow.node.{name}",
            status=status,
            duration=duration,
            started_at=started_at,
            attributes={"node": name, "thread_id": self.thread_id},
            error=error,
        )


def record_workflow_run(outcome: str, duration: float, operation: str = "run") -> None:
    """Count a finished graph run or resume, by how it ended."""

    try:
        metrics.workflow_runs.inc(outcome=outcome)
        metrics.workflow_duration.observe(duration, operation=operation)

    except Exception:
        logger.exception("Could not record the workflow outcome %s", outcome)


def set_pending_approvals(count: int) -> None:
    """Publish the size of the approval queue as a gauge."""

    try:
        metrics.approvals_pending.set(count)

    except Exception:
        logger.exception("Could not record the pending approval count")
