"""
Observability: logs, metrics and spans.

The platform already records *what* it decided (the audit trail)
and *how well* it decides (the evals). This layer answers the
operational questions: is it up, how fast is it, what is failing
right now, and which request was it.

- `context`        correlation ids carried through a request
- `logging_setup`  structured or human logs, with redaction
- `metrics`        counters, gauges and histograms; Prometheus text
- `spans`          timing, recent activity and recent errors
- `middleware`     HTTP instrumentation
- `health`         readiness checks and the dashboard summary
- `instrumentation` wrappers for model calls and workflow nodes

Nothing here changes a decision the platform makes. Every recorder
is best-effort: a failure to measure must never fail the work.
"""

from app.observability.context import (
    REQUEST_ID_HEADER,
    current_request_id,
    current_thread_id,
    request_context,
    workflow_context,
)
from app.observability.logging_setup import configure_logging
from app.observability.spans import record_error, record_span, span


__all__ = [
    "REQUEST_ID_HEADER",
    "configure_logging",
    "current_request_id",
    "current_thread_id",
    "record_error",
    "record_span",
    "request_context",
    "span",
    "workflow_context",
]
