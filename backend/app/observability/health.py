"""
Readiness checks and the summary the dashboard reads.

`/health` stays a liveness check: the process is up. Readiness is
the stricter question — can this process actually serve a request?
That means the database answers, the vector store is reachable and
the model provider is configured.

No check calls Cohere. A readiness probe runs every few seconds,
and paying for an embedding each time to prove a key exists would
be a bill, not a signal: the key is checked for presence only.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from app.observability import metrics
from app.observability.spans import recent_errors, recent_spans


logger = logging.getLogger(__name__)


OK = "ok"
DEGRADED = "degraded"
FAILED = "failed"


def check(name: str, essential: bool = True):
    """
    Run one check and turn any failure into a result, so a probe
    always answers instead of returning a 500.
    """

    def run(probe):

        started = time.perf_counter()

        try:
            detail = probe() or ""
            status = OK

        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            status = FAILED if essential else DEGRADED

            logger.warning("Readiness check %s failed: %s", name, detail)

        return {
            "name": name,
            "status": status,
            "essential": essential,
            "detail": str(detail),
            "duration_seconds": round(time.perf_counter() - started, 6),
        }

    return run


def check_database() -> str:

    from sqlalchemy import text

    from app.services.db import engine

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return f"{engine.url.get_backend_name()} reachable"


def check_vector_store() -> str:

    from app.services.vector_store import get_collection_count

    return f"{get_collection_count()} chunks"


def check_model_provider() -> str:
    """Configuration only: this must not spend quota."""

    if not os.getenv("COHERE_API_KEY"):
        raise RuntimeError("COHERE_API_KEY is not set.")

    return "api key configured"


def check_checkpointer() -> str:

    from app.services.agent_graph import checkpointer
    from app.services.checkpointer import is_postgres_url
    from app.services.db import DATABASE_URL

    kind = type(checkpointer).__name__

    # The same decision create_checkpointer makes, rather than the
    # class name, which the library is free to rename.
    if not is_postgres_url(DATABASE_URL):
        raise RuntimeError(
            f"{kind}: workflow state is in memory, so paused approvals "
            "are lost on restart; use PostgreSQL for durable state"
        )

    return f"{kind} (durable)"


CHECKS = (
    ("database", check_database, True),
    ("vector_store", check_vector_store, True),
    ("model_provider", check_model_provider, True),
    ("workflow_state", check_checkpointer, False),
)


def readiness() -> dict:
    """
    Every check, plus the overall verdict. A non-essential check
    that fails makes the service degraded, not unready.
    """

    results = [
        check(name, essential)(probe) for name, probe, essential in CHECKS
    ]

    if any(result["status"] == FAILED for result in results):
        status = FAILED

    elif any(result["status"] == DEGRADED for result in results):
        status = DEGRADED

    else:
        status = OK

    return {
        "status": status,
        "checks": results,
        "uptime_seconds": round(metrics.uptime_seconds(), 3),
    }


# ============================================================
# SUMMARY
# ============================================================

def series(metric_name: str) -> list[dict]:

    snapshot = metrics.registry.snapshot()

    return snapshot.get(metric_name, {}).get("series", [])


def total_of(metric_name: str) -> float:

    return sum(item.get("value", 0) for item in series(metric_name))


def summary(limit: int = 10) -> dict:
    """
    A compact view of this process for the dashboard: what it has
    served, how the workflows went, and what went wrong lately.

    These numbers are per process and reset when it restarts. The
    audit trail is the durable record; this is the live view.
    """

    http = series("http_requests_total")

    http_failed = sum(
        item["value"]
        for item in http
        if item["labels"].get("status", "").startswith(("4", "5"))
    )

    durations = series("http_request_duration_seconds")

    served = sum(item["count"] for item in durations)
    total_seconds = sum(item["sum_seconds"] for item in durations)

    return {
        "status": OK,
        "started_at": datetime.fromtimestamp(
            metrics.start_time.value(), tz=timezone.utc
        ).isoformat(),
        "uptime_seconds": round(metrics.uptime_seconds(), 3),
        "requests": {
            "total": sum(item["value"] for item in http),
            "failed": http_failed,
            "in_progress": metrics.http_in_progress.value(),
            "average_seconds": round(total_seconds / served, 6) if served else 0.0,
            "by_route": sorted(
                (
                    {
                        "route": item["labels"].get("route", "unmatched"),
                        "method": item["labels"].get("method", ""),
                        "status": item["labels"].get("status", ""),
                        "count": item["value"],
                    }
                    for item in http
                ),
                key=lambda item: item["count"],
                reverse=True,
            )[:limit],
        },
        "workflows": {
            "by_outcome": {
                item["labels"].get("outcome", "unknown"): item["value"]
                for item in series("workflow_runs_total")
            },
            "average_seconds": {
                item["labels"].get("operation", "unknown"): item["average_seconds"]
                for item in series("workflow_duration_seconds")
            },
            "nodes": node_summary(),
        },
        "model_calls": {
            "total": total_of("model_calls_total"),
            "by_operation": [
                {
                    "provider": item["labels"].get("provider", ""),
                    "operation": item["labels"].get("operation", ""),
                    "status": item["labels"].get("status", ""),
                    "count": item["value"],
                }
                for item in series("model_calls_total")
            ],
            "average_seconds": {
                item["labels"].get("operation", "unknown"): item["average_seconds"]
                for item in series("model_call_duration_seconds")
            },
        },
        "errors": {
            "total": total_of("errors_total"),
            "by_kind": [
                {
                    "kind": item["labels"].get("kind", ""),
                    "where": item["labels"].get("where", ""),
                    "count": item["value"],
                }
                for item in series("errors_total")
            ],
            "recent": [record.to_dict() for record in recent_errors.latest(limit)],
        },
        "slowest_operations": [
            record.to_dict() for record in recent_spans.slowest(limit)
        ],
        "recent_operations": [
            record.to_dict() for record in recent_spans.latest(limit)
        ],
    }


def node_summary() -> list[dict]:
    """Per-node counts and average duration, busiest first."""

    durations = {
        item["labels"].get("node", ""): item
        for item in series("workflow_node_duration_seconds")
    }

    rows: dict[str, dict] = {}

    for item in series("workflow_node_total"):

        node = item["labels"].get("node", "")
        status = item["labels"].get("status", "")

        row = rows.setdefault(
            node,
            {
                "node": node,
                "total": 0.0,
                "failed": 0.0,
                "average_seconds": durations.get(node, {}).get(
                    "average_seconds", 0.0
                ),
            },
        )

        row["total"] += item["value"]

        if status != "success":
            row["failed"] += item["value"]

    return sorted(rows.values(), key=lambda row: row["total"], reverse=True)
