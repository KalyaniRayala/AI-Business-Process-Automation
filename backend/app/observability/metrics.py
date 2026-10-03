"""
In-process metrics.

A small counter / gauge / histogram registry that renders the
Prometheus text format, so `GET /metrics` can be scraped by
Prometheus, the Docker step, or anything else that speaks it,
without adding a dependency.

What it is not: metrics live in the process that recorded them, so
with several workers a scrape returns one worker's view. That is
the usual reason to move to a client library with a shared store,
and the registry is deliberately small enough to swap out.

Label values are the cheapest way to blow up a metrics store, so
callers pass route templates and node names, never raw paths, ids
or questions.
"""

from __future__ import annotations

import math
import threading
import time


# Latencies in seconds. The buckets are wide: a Cohere call is
# hundreds of milliseconds, a whole workflow is seconds.
DEFAULT_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
)


def escape_label_value(value: str) -> str:

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def format_labels(names: tuple[str, ...], values: tuple[str, ...]) -> str:

    if not names:
        return ""

    pairs = ",".join(
        f'{name}="{escape_label_value(value)}"'
        for name, value in zip(names, values)
    )

    return "{" + pairs + "}"


def format_number(value: float) -> str:
    """Prometheus spells infinity `+Inf` and prints integers plainly."""

    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"

    if value == int(value) and abs(value) < 2**53:
        return str(int(value))

    return repr(value)


# ============================================================
# METRICS
# ============================================================

class Metric:

    type = "untyped"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] = (),
    ) -> None:

        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self.lock = threading.Lock()

    def __repr__(self) -> str:

        return f"{type(self).__name__}({self.name!r})"

    def key(self, labels: dict[str, str]) -> tuple[str, ...]:

        missing = set(self.labelnames) - set(labels)
        unexpected = set(labels) - set(self.labelnames)

        if missing or unexpected:
            raise ValueError(
                f"{self.name} takes labels {self.labelnames}, "
                f"got {tuple(sorted(labels))}."
            )

        return tuple(str(labels[name]) for name in self.labelnames)


class Counter(Metric):
    """A value that only goes up."""

    type = "counter"

    def __init__(self, name, documentation, labelnames=()) -> None:

        super().__init__(name, documentation, labelnames)

        self.values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:

        if amount < 0:
            raise ValueError("A counter cannot decrease.")

        key = self.key(labels)

        with self.lock:
            self.values[key] = self.values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:

        with self.lock:
            return self.values.get(self.key(labels), 0.0)

    def samples(self) -> list[tuple[str, tuple[str, ...], float]]:

        with self.lock:
            return [(self.name, key, value) for key, value in self.values.items()]


class Gauge(Metric):
    """A value that goes up and down."""

    type = "gauge"

    def __init__(self, name, documentation, labelnames=()) -> None:

        super().__init__(name, documentation, labelnames)

        self.values: dict[tuple[str, ...], float] = {}

    def set(self, amount: float, **labels: str) -> None:

        key = self.key(labels)

        with self.lock:
            self.values[key] = float(amount)

    def inc(self, amount: float = 1.0, **labels: str) -> None:

        key = self.key(labels)

        with self.lock:
            self.values[key] = self.values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:

        self.inc(-amount, **labels)

    def value(self, **labels: str) -> float:

        with self.lock:
            return self.values.get(self.key(labels), 0.0)

    def samples(self) -> list[tuple[str, tuple[str, ...], float]]:

        with self.lock:
            return [(self.name, key, value) for key, value in self.values.items()]


class Histogram(Metric):
    """Bucketed observations, for latencies."""

    type = "histogram"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:

        super().__init__(name, documentation, labelnames)

        self.buckets = tuple(sorted(buckets))
        self.counts: dict[tuple[str, ...], list[int]] = {}
        self.sums: dict[tuple[str, ...], float] = {}
        self.totals: dict[tuple[str, ...], int] = {}

    def observe(self, value: float, **labels: str) -> None:

        key = self.key(labels)

        with self.lock:

            counts = self.counts.setdefault(key, [0] * len(self.buckets))

            for position, bound in enumerate(self.buckets):

                if value <= bound:
                    counts[position] += 1

            self.sums[key] = self.sums.get(key, 0.0) + value
            self.totals[key] = self.totals.get(key, 0) + 1

    def count(self, **labels: str) -> int:

        with self.lock:
            return self.totals.get(self.key(labels), 0)

    def total_seconds(self, **labels: str) -> float:

        with self.lock:
            return self.sums.get(self.key(labels), 0.0)

    def average(self, **labels: str) -> float:
        """Mean observation, or 0 when there are none."""

        key = self.key(labels)

        with self.lock:
            total = self.totals.get(key, 0)

            return self.sums.get(key, 0.0) / total if total else 0.0

    def samples(self) -> list[tuple[str, tuple[str, ...], float]]:
        """Bucket, sum and count samples in Prometheus' layout."""

        with self.lock:
            snapshot = {
                key: (list(counts), self.sums[key], self.totals[key])
                for key, counts in self.counts.items()
            }

        samples = []

        for key, (counts, total_sum, total_count) in snapshot.items():

            for bound, count in zip(self.buckets, counts):
                samples.append(
                    (f"{self.name}_bucket", key + (format_number(bound),), count)
                )

            samples.append(
                (f"{self.name}_bucket", key + ("+Inf",), total_count)
            )
            samples.append((f"{self.name}_sum", key, total_sum))
            samples.append((f"{self.name}_count", key, total_count))

        return samples

    @property
    def bucket_labelnames(self) -> tuple[str, ...]:

        return self.labelnames + ("le",)


# ============================================================
# REGISTRY
# ============================================================

class Registry:
    """Holds the metrics and renders them."""

    def __init__(self) -> None:

        self.metrics: dict[str, Metric] = {}
        self.lock = threading.Lock()

    def register(self, metric: Metric) -> Metric:

        with self.lock:

            if metric.name in self.metrics:
                raise ValueError(f"Metric {metric.name} is already registered.")

            self.metrics[metric.name] = metric

        return metric

    def counter(self, name, documentation, labelnames=()) -> Counter:

        return self.register(Counter(name, documentation, tuple(labelnames)))

    def gauge(self, name, documentation, labelnames=()) -> Gauge:

        return self.register(Gauge(name, documentation, tuple(labelnames)))

    def histogram(
        self, name, documentation, labelnames=(), buckets=DEFAULT_BUCKETS
    ) -> Histogram:

        return self.register(
            Histogram(name, documentation, tuple(labelnames), tuple(buckets))
        )

    def clear(self) -> None:
        """Forget every recorded value, keeping the definitions."""

        with self.lock:
            metrics = list(self.metrics.values())

        for metric in metrics:

            with metric.lock:

                if isinstance(metric, Histogram):
                    metric.counts.clear()
                    metric.sums.clear()
                    metric.totals.clear()
                else:
                    metric.values.clear()

    # --------------------------------------------------------
    # Exposition
    # --------------------------------------------------------

    def render(self) -> str:
        """The Prometheus text exposition format."""

        with self.lock:
            metrics = list(self.metrics.values())

        lines: list[str] = []

        for metric in metrics:

            lines.append(f"# HELP {metric.name} {metric.documentation}")
            lines.append(f"# TYPE {metric.name} {metric.type}")

            labelnames = (
                metric.bucket_labelnames
                if isinstance(metric, Histogram)
                else metric.labelnames
            )

            for name, values, value in metric.samples():

                # _sum and _count carry the metric's own labels, not
                # the bucket boundary.
                names = labelnames if len(values) == len(labelnames) else metric.labelnames

                lines.append(
                    f"{name}{format_labels(names, values)} {format_number(value)}"
                )

        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict:
        """
        Plain data for the JSON summary endpoint, so the dashboard
        does not have to parse the Prometheus format.
        """

        with self.lock:
            metrics = list(self.metrics.values())

        payload: dict[str, dict] = {}

        for metric in metrics:

            if isinstance(metric, Histogram):

                with metric.lock:
                    series = [
                        {
                            "labels": dict(zip(metric.labelnames, key)),
                            "count": metric.totals[key],
                            "sum_seconds": round(metric.sums[key], 6),
                            "average_seconds": round(
                                metric.sums[key] / metric.totals[key], 6
                            )
                            if metric.totals[key]
                            else 0.0,
                        }
                        for key in metric.totals
                    ]

            else:

                with metric.lock:
                    series = [
                        {
                            "labels": dict(zip(metric.labelnames, key)),
                            "value": value,
                        }
                        for key, value in metric.values.items()
                    ]

            payload[metric.name] = {
                "type": metric.type,
                "help": metric.documentation,
                "series": series,
            }

        return payload


# ============================================================
# THE APPLICATION'S METRICS
# ============================================================

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

registry = Registry()

# HTTP
http_requests = registry.counter(
    "http_requests_total",
    "HTTP requests by route template, method and status code.",
    ("method", "route", "status"),
)

http_duration = registry.histogram(
    "http_request_duration_seconds",
    "Time to produce an HTTP response.",
    ("method", "route"),
)

# Unlabelled: the route is not known until after routing, and the
# raw path would put a workflow id in a metric label.
http_in_progress = registry.gauge(
    "http_requests_in_progress",
    "HTTP requests currently being handled.",
)

# Workflows
workflow_runs = registry.counter(
    "workflow_runs_total",
    "Workflow runs by outcome (success, awaiting_approval, rejected, failed).",
    ("outcome",),
)

workflow_duration = registry.histogram(
    "workflow_duration_seconds",
    "Time for one graph run or resume, from request to response.",
    ("operation",),
)

workflow_nodes = registry.counter(
    "workflow_node_total",
    "Workflow node executions by node and status.",
    ("node", "status"),
)

workflow_node_duration = registry.histogram(
    "workflow_node_duration_seconds",
    "Time spent in a workflow node.",
    ("node",),
)

approvals_pending = registry.gauge(
    "workflow_approvals_pending",
    "Workflows waiting for a human decision, as of the last measurement.",
)

# Model calls
model_calls = registry.counter(
    "model_calls_total",
    "Calls to the model provider by operation and outcome.",
    ("provider", "operation", "status"),
)

model_duration = registry.histogram(
    "model_call_duration_seconds",
    "Time spent waiting for the model provider.",
    ("provider", "operation"),
)

# Generic spans, for anything instrumented with span().
operations = registry.counter(
    "operations_total",
    "Instrumented operations by name and outcome.",
    ("operation", "status"),
)

operation_duration = registry.histogram(
    "operation_duration_seconds",
    "Duration of instrumented operations.",
    ("operation",),
)

errors = registry.counter(
    "errors_total",
    "Unhandled errors by the kind of exception and where they surfaced.",
    ("kind", "where"),
)

# Process
start_time = registry.gauge(
    "process_start_time_seconds",
    "When the process started, in seconds since the epoch.",
)

start_time.set(time.time())


def uptime_seconds() -> float:

    return max(0.0, time.time() - start_time.value())
