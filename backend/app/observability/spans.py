"""
Spans and recent activity.

`span()` times a block of work, records it in the metrics, logs it
once it finishes, and keeps it in a small in-memory ring buffer.
That buffer is what makes the dashboard useful without running a
tracing backend: the slowest recent operations and the last errors
are right there, with the request id that produced them.

Two deliberate limits: the buffer holds the last few hundred
entries and is lost on restart, and a span is a flat measurement,
not a tree. The call sites are the ones a tracer would wrap, so
swapping in OpenTelemetry later is a change here, not everywhere.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.observability import metrics
from app.observability.context import current_context


logger = logging.getLogger(__name__)


# How many recent spans and errors to keep. Small on purpose: this
# is a live view, not storage. The audit trail is the record.
RECENT_SPANS = 200
RECENT_ERRORS = 50

# Spans at or above this take a warning instead of a debug line.
SLOW_SECONDS = 5.0


@dataclass
class SpanRecord:

    operation: str
    status: str
    duration_seconds: float
    started_at: str
    attributes: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    thread_id: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:

        payload = {
            "operation": self.operation,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 6),
            "started_at": self.started_at,
            "attributes": self.attributes,
        }

        for key in ("request_id", "thread_id", "error"):

            value = getattr(self, key)

            if value:
                payload[key] = value

        return payload


class RecentActivity:
    """A thread-safe ring buffer of the latest records."""

    def __init__(self, capacity: int) -> None:

        self.entries: deque[SpanRecord] = deque(maxlen=capacity)
        self.lock = threading.Lock()

    def add(self, record: SpanRecord) -> None:

        with self.lock:
            self.entries.append(record)

    def latest(self, limit: int = 20) -> list[SpanRecord]:
        """The most recent records, newest first."""

        with self.lock:
            entries = list(self.entries)

        return list(reversed(entries))[:limit]

    def slowest(self, limit: int = 10) -> list[SpanRecord]:

        with self.lock:
            entries = list(self.entries)

        return sorted(
            entries, key=lambda record: record.duration_seconds, reverse=True
        )[:limit]

    def clear(self) -> None:

        with self.lock:
            self.entries.clear()


recent_spans = RecentActivity(RECENT_SPANS)
recent_errors = RecentActivity(RECENT_ERRORS)


# ============================================================
# SPANS
# ============================================================

class Span:
    """The handle a `with span(...)` block receives."""

    def __init__(self, operation: str, attributes: dict[str, Any]) -> None:

        self.operation = operation
        self.attributes = dict(attributes)
        self.status = "success"

    def set(self, **attributes: Any) -> None:
        """Add attributes once the work has produced them."""

        self.attributes.update(attributes)


@contextmanager
def span(operation: str, **attributes: Any):
    """
    Time a block of work.

    The block's own exception is recorded and re-raised: this
    observes, it never swallows.
    """

    handle = Span(operation, attributes)

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()

    error: BaseException | None = None

    try:
        yield handle

    except BaseException as raised:
        error = raised
        handle.status = "error"
        raise

    finally:

        duration = time.perf_counter() - started

        record_span(
            operation=operation,
            status=handle.status,
            duration=duration,
            started_at=started_at,
            attributes=handle.attributes,
            error=error,
        )


def record_span(
    operation: str,
    status: str,
    duration: float,
    started_at: datetime | None = None,
    attributes: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> SpanRecord:
    """
    Record a finished piece of work. Used by `span()` and by code
    that measures its own timing, such as the workflow observer.

    Best-effort: recording a measurement must never break the thing
    being measured.
    """

    context = current_context()

    record = SpanRecord(
        operation=operation,
        status=status,
        duration_seconds=duration,
        started_at=(started_at or datetime.now(timezone.utc)).isoformat(),
        attributes=attributes or {},
        request_id=context.get("request_id"),
        thread_id=context.get("thread_id"),
        error=f"{type(error).__name__}: {error}" if error else None,
    )

    try:

        metrics.operations.inc(operation=operation, status=status)
        metrics.operation_duration.observe(duration, operation=operation)

        recent_spans.add(record)

        if error is not None:
            recent_errors.add(record)

        log = logger.warning if duration >= SLOW_SECONDS else logger.debug

        log(
            "%s finished in %.3fs (%s)",
            operation,
            duration,
            status,
            extra={"operation": operation, "duration_seconds": round(duration, 6)},
        )

    except Exception:

        logger.exception("Could not record the span for %s", operation)

    return record


def record_error(kind: str, where: str) -> None:
    """Count an error that surfaced outside a span."""

    try:
        metrics.errors.inc(kind=kind, where=where)

    except Exception:
        logger.exception("Could not count an error for %s", where)


def reset() -> None:
    """Forget every recorded span. For tests."""

    recent_spans.clear()
    recent_errors.clear()
