"""
Workflow discovery: how the process actually runs, learned from what
is already recorded, and where it differs from the process model.

Read-only. Nothing is stored; every call analyses PostgreSQL:

- audit_events      the ordered node events of each workflow run
                    (the "event log")
- workflow_runs /   volume by department and amount band
  employees
- context graph     the modeled process (steps, who performs them,
                    which components automate them, policy thresholds)

The result describes observed behaviour (paths taken, outcomes, step
durations, approval waits, volume) and the gaps between the model and
the automation (manual and partially automated steps). Deciding what
to automate next is left to Automation Recommendation.

Only workflows with a workflow_started event are analysed: those are
the runs made through the API.
"""

import hashlib
import json
import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app.services.audit_service import AuditEventType
from app.services.context_graph_seed import PROCESS
from app.services.context_graph_service import get_process_context
from app.services.db import session_scope
from app.services.db_models import AuditEvent, Employee, WorkflowRun


logger = logging.getLogger(__name__)


DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000

# Keeps IN (...) lists below SQLite's variable limit.
LOOKUP_CHUNK = 500

MAX_SAMPLE_THREADS = 3

OUTCOMES = (
    "completed", "rejected", "failed", "error", "awaiting_approval",
    "in_progress",
)

E = AuditEventType

TERMINAL_OUTCOMES = {
    E.WORKFLOW_COMPLETED.value: "completed",
    E.WORKFLOW_REJECTED.value: "rejected",
    E.WORKFLOW_FAILED.value: "failed",
    E.WORKFLOW_RECOVERY.value: "failed",
    E.WORKFLOW_ERROR.value: "error",
}

NODE_EVENTS = {
    E.NODE_STARTED.value, E.NODE_COMPLETED.value, E.NODE_FAILED.value,
}


# ============================================================
# TRACES (one per workflow run, rebuilt from its audit events)
# ============================================================

@dataclass
class Trace:

    thread_id: str
    started_at: datetime
    ended_at: datetime | None = None
    outcome: str = "in_progress"

    # Stages of the path; nodes that ran in parallel share a stage.
    stages: list[tuple[str, ...]] = field(default_factory=list)

    # (node, seconds, failed) for every started/finished pair.
    node_runs: list[tuple[str, float, bool]] = field(default_factory=list)

    approval_requested: bool = False
    approval_decision: str | None = None       # approved | rejected
    approval_wait_seconds: float | None = None

    @property
    def duration_seconds(self) -> float | None:

        if self.ended_at is None:
            return None

        return seconds_between(self.started_at, self.ended_at)


def seconds_between(start: datetime, end: datetime) -> float:

    return max(0.0, (end - start).total_seconds())


def build_trace(thread_id: str, events: list) -> Trace:
    """
    Rebuild one run from its events (in recording order).

    A new stage starts when a node starts after some node has
    finished since the previous start, so nodes that started together
    (parallel branches) share a stage whatever order they finish in.
    Resuming after approval starts the approval node again; repeats
    of the previous stage are merged.
    """

    started = next(
        (e for e in events if e.event_type == E.WORKFLOW_STARTED.value),
        events[0]
    )

    trace = Trace(thread_id=thread_id, started_at=started.occurred_at)

    stage: set[str] = set()
    finished_since_start = False

    open_starts: dict[str, datetime] = {}
    requested_at: datetime | None = None

    def close_stage():

        if not stage:
            return

        names = tuple(sorted(stage))

        # Pausing and resuming for approval repeats the stage.
        if not trace.stages or trace.stages[-1] != names:
            trace.stages.append(names)

    for event in events:

        kind = event.event_type

        if kind == E.NODE_STARTED.value:

            if finished_since_start:
                close_stage()
                stage = set()
                finished_since_start = False

            stage.add(event.node)

            # A restart replaces an unfinished start (paused node).
            open_starts[event.node] = event.occurred_at

        elif kind in (E.NODE_COMPLETED.value, E.NODE_FAILED.value):

            finished_since_start = True

            begun = open_starts.pop(event.node, None)

            if begun is not None:

                trace.node_runs.append((
                    event.node,
                    seconds_between(begun, event.occurred_at),
                    kind == E.NODE_FAILED.value,
                ))

        elif kind == E.APPROVAL_REQUESTED.value:

            trace.approval_requested = True
            requested_at = event.occurred_at

        elif kind in (E.APPROVAL_APPROVED.value, E.APPROVAL_REJECTED.value):

            if trace.approval_decision is None:

                trace.approval_decision = (
                    "approved" if kind == E.APPROVAL_APPROVED.value
                    else "rejected"
                )

                if requested_at is not None:
                    trace.approval_wait_seconds = seconds_between(
                        requested_at, event.occurred_at
                    )

        elif kind in TERMINAL_OUTCOMES:

            trace.outcome = TERMINAL_OUTCOMES[kind]
            trace.ended_at = event.occurred_at

    close_stage()

    if trace.outcome == "in_progress" and (
        trace.approval_requested and trace.approval_decision is None
    ):
        trace.outcome = "awaiting_approval"

    return trace


# ============================================================
# LOADING
# ============================================================

def normalise_time(value: datetime | None) -> datetime | None:
    """UTC, as the audit trail stores it."""

    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def chunks(items: list, size: int = LOOKUP_CHUNK):

    for start in range(0, len(items), size):
        yield items[start:start + size]


def load_thread_ids(
    session, limit: int, since, until, thread_ids
) -> list[str]:
    """The most recent runs (newest first) with a workflow_started event."""

    query = select(AuditEvent.thread_id).where(
        AuditEvent.event_type == E.WORKFLOW_STARTED.value,
        AuditEvent.thread_id.is_not(None),
    )

    if since is not None:
        query = query.where(AuditEvent.occurred_at >= since)

    if until is not None:
        query = query.where(AuditEvent.occurred_at <= until)

    if thread_ids is not None:
        query = query.where(AuditEvent.thread_id.in_(list(thread_ids)))

    query = query.order_by(AuditEvent.id.desc()).limit(limit)

    ordered = []
    seen = set()

    for thread_id in session.scalars(query):

        if thread_id not in seen:
            seen.add(thread_id)
            ordered.append(thread_id)

    return ordered


def load_traces(session, thread_ids: list[str]) -> list[Trace]:

    events_by_thread = defaultdict(list)

    for chunk in chunks(thread_ids):

        rows = session.execute(
            select(
                AuditEvent.id, AuditEvent.thread_id, AuditEvent.event_type,
                AuditEvent.node, AuditEvent.occurred_at,
            )
            .where(AuditEvent.thread_id.in_(chunk))
            .order_by(AuditEvent.id)
        )

        for row in rows:
            events_by_thread[row.thread_id].append(row)

    return [
        build_trace(thread_id, events_by_thread[thread_id])
        for thread_id in thread_ids
        if events_by_thread[thread_id]
    ]


# ============================================================
# STATISTICS
# ============================================================

def summarize(values: list[float]) -> dict | None:
    """count / mean / median / p95 / max, or None when empty."""

    if not values:
        return None

    ordered = sorted(values)

    # Linear interpolation between the closest ranks.
    position = 0.95 * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    p95 = ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return {
        "count": len(ordered),
        "mean": round(statistics.fmean(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p95": round(p95, 3),
        "max": round(ordered[-1], 3),
    }


def share(count: int, total: int) -> float:

    return round(count / total, 4) if total else 0.0


def path_text(stages, human_approval: bool = False) -> str:

    text = (
        " -> ".join("+".join(stage) for stage in stages)
        if stages else "(no nodes executed)"
    )

    return text + (" [human approval]" if human_approval else "")


def variant_id(stages, human_approval: bool = False) -> str:

    return hashlib.sha1(
        json.dumps([[list(s) for s in stages], human_approval]).encode()
    ).hexdigest()[:10]


def discover_variants(traces: list[Trace]) -> list[dict]:
    """
    Distinct paths taken, most common first. Whether a human had to
    approve is part of the variant: the approval node runs on every
    path, but only some of them pause for a person.
    """

    groups = defaultdict(list)

    for trace in traces:
        groups[(tuple(trace.stages), trace.approval_requested)].append(trace)

    variants = []

    for (stages, human_approval), members in groups.items():

        outcomes = Counter(t.outcome for t in members)

        variants.append({
            "variant_id": variant_id(stages, human_approval),
            "path": [list(stage) for stage in stages],
            "path_text": path_text(stages, human_approval),
            "human_approval": human_approval,
            "workflows": len(members),
            "share": share(len(members), len(traces)),
            "outcomes": dict(sorted(outcomes.items())),
            "cycle_time_seconds": summarize([
                t.duration_seconds for t in members
                if t.duration_seconds is not None
            ]),
            "sample_thread_ids": [t.thread_id for t in members[:MAX_SAMPLE_THREADS]],
        })

    variants.sort(key=lambda v: (-v["workflows"], v["path_text"]))

    return variants


def discover_steps(traces: list[Trace]) -> list[dict]:
    """Per node: executions, failures and duration."""

    durations = defaultdict(list)
    failures = Counter()
    executions = Counter()
    workflows = Counter()

    for trace in traces:

        for node in {name for stage in trace.stages for name in stage}:
            workflows[node] += 1

        for node, seconds, failed in trace.node_runs:

            executions[node] += 1
            durations[node].append(seconds)

            if failed:
                failures[node] += 1

    steps = []

    for node in sorted(set(workflows) | set(executions)):

        steps.append({
            "node": node,
            "workflows": workflows[node],
            "executions": executions[node],
            "failures": failures[node],
            "failure_rate": share(failures[node], executions[node]),
            "duration_seconds": summarize(durations[node]),
        })

    steps.sort(key=lambda s: (-s["workflows"], s["node"]))

    return steps


def discover_approvals(traces: list[Trace]) -> dict:

    requested = [t for t in traces if t.approval_requested]

    approved = sum(1 for t in requested if t.approval_decision == "approved")
    rejected = sum(1 for t in requested if t.approval_decision == "rejected")

    return {
        "workflows": len(traces),
        "required_approval": len(requested),
        "approval_rate_of_workflows": share(len(requested), len(traces)),
        "approved": approved,
        "rejected": rejected,
        "pending": len(requested) - approved - rejected,
        "rejection_rate": share(rejected, approved + rejected),
        "wait_seconds": summarize([
            t.approval_wait_seconds for t in requested
            if t.approval_wait_seconds is not None
        ]),
    }


# ============================================================
# BUSINESS DATA (volume)
# ============================================================

def amount_bands(thresholds: list[float]) -> list[tuple[str, float | None, float | None]]:
    """
    (label, above, up_to) bands from the policy thresholds: an
    amount is in a band when above < amount <= up_to, matching the
    rules' "above $X" wording.
    """

    if not thresholds:
        return [("all amounts", None, None)]

    ordered = sorted(thresholds)

    bands = [(f"up to {ordered[0]:g}", None, ordered[0])]

    for low, high in zip(ordered, ordered[1:]):
        bands.append((f"above {low:g} up to {high:g}", low, high))

    bands.append((f"above {ordered[-1]:g}", ordered[-1], None))

    return bands


def band_for(amount: float, bands):

    for label, above, up_to in bands:

        if (above is None or amount > above) and (up_to is None or amount <= up_to):
            return label

    return bands[-1][0]


def discover_volume(session, traces: list[Trace], thresholds: list[float]) -> dict:

    thread_ids = [t.thread_id for t in traces]
    bands = amount_bands(thresholds)

    departments = defaultdict(lambda: {
        "workflows": 0, "total_amount": 0.0, "required_approval": 0, "rejected": 0
    })
    # above / up_to are the numeric bounds (above < amount <= up_to).
    by_band = {label: {
        "band": label, "above": above, "up_to": up_to,
        "workflows": 0, "required_approval": 0, "rejected": 0
    } for label, above, up_to in bands}
    by_band["unknown amount"] = {
        "band": "unknown amount", "above": None, "up_to": None,
        "workflows": 0, "required_approval": 0, "rejected": 0,
    }

    matched = 0

    for chunk in chunks(thread_ids):

        rows = session.execute(
            select(
                WorkflowRun.expense_amount, WorkflowRun.approval_status,
                Employee.department,
            )
            .outerjoin(Employee, Employee.employee_id == WorkflowRun.employee_id)
            .where(WorkflowRun.thread_id.in_(chunk))
        )

        for amount, approval_status, department in rows:

            matched += 1

            needed = approval_status not in (None, "not_required")
            rejected = approval_status == "rejected"

            entry = departments[department or "unknown"]
            entry["workflows"] += 1
            entry["required_approval"] += int(needed)
            entry["rejected"] += int(rejected)

            if amount is None:
                label = "unknown amount"
            else:
                entry["total_amount"] += float(amount)
                label = band_for(float(amount), bands)

            band = by_band[label]
            band["workflows"] += 1
            band["required_approval"] += int(needed)
            band["rejected"] += int(rejected)

    return {
        "workflows_with_run_record": matched,
        "workflows_without_run_record": len(traces) - matched,
        "by_department": [
            {"department": name, **{
                key: (round(value, 2) if key == "total_amount" else value)
                for key, value in values.items()
            }}
            for name, values in sorted(
                departments.items(), key=lambda item: (-item[1]["workflows"], item[0])
            )
        ],
        "by_amount_band": [
            band for band in by_band.values()
            if band["workflows"] or band["band"] != "unknown amount"
        ],
    }


# ============================================================
# PROCESS MODEL COMPARISON
# ============================================================

def describe_process_model(steps: list[dict], traces: list[Trace]) -> dict | None:
    """
    The Context Graph's process model set against what ran: each
    modeled step's automation level and how many runs used its
    implementing nodes, the gaps, and observed nodes the model does
    not know.
    """

    process = get_process_context(PROCESS)

    if process is None:
        return None

    ran = Counter()

    for trace in traces:

        for node in {name for stage in trace.stages for name in stage}:
            ran[node] += 1

    modeled_nodes: dict[str, list[int]] = defaultdict(list)
    model_steps = []

    for step in process["steps"]:

        components = [
            {
                "component": item["component"]["node_key"],
                "label": item["component"]["label"],
                "coverage": (item["properties"] or {}).get("coverage", "full"),
            }
            for item in step["implemented_by"]
        ]

        if not components:
            automation = "manual"
        elif all(c["coverage"] == "partial" for c in components):
            automation = "partial"
        else:
            automation = "automated"

        nodes = [
            c["component"].split(":", 1)[1] for c in components
            if c["component"].startswith("agent_node:")
        ]

        for node in nodes:
            modeled_nodes[node].append(step["order"])

        model_steps.append({
            "order": step["order"],
            "step": step["step"]["node_key"],
            "label": step["step"]["label"],
            "performed_by": [r["label"] for r in step["performed_by"]],
            "uses_systems": [s["label"] for s in step["uses_systems"]],
            "automation": automation,
            "implemented_by": components,
            # Workflows whose run used a node implementing this step.
            "workflows_observed": (
                max((ran[node] for node in nodes), default=0) if nodes else None
            ),
            "evidence": step["evidence"],
        })

    for entry in steps:
        entry["modeled_steps"] = sorted(modeled_nodes.get(entry["node"], []))

    return {
        "process": process["process"]["node_key"],
        "label": process["process"]["label"],
        "steps": model_steps,
        "automation_gaps": [s for s in model_steps if s["automation"] != "automated"],
        "unmapped_nodes": sorted(
            entry["node"] for entry in steps
            if entry["node"] not in modeled_nodes
        ),
        "thresholds": policy_thresholds(process),
    }


def policy_thresholds(process: dict) -> list[float]:

    return sorted({
        float(approver["threshold"])
        for rule in process["policy_rules"]
        for approver in rule["approvers"]
        if approver["threshold"] is not None
    })


# ============================================================
# ENTRY POINT
# ============================================================

def discover_workflows(
    *,
    limit: int = DEFAULT_LIMIT,
    since: datetime | None = None,
    until: datetime | None = None,
    thread_ids: list[str] | None = None,
) -> dict:
    """
    Analyse the most recent `limit` workflow runs (optionally only
    those started within [since, until], or only `thread_ids`).
    """

    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}.")

    since, until = normalise_time(since), normalise_time(until)

    if since is not None and until is not None and until < since:
        raise ValueError("until must not be before since.")

    with session_scope() as session:

        ids = load_thread_ids(session, limit, since, until, thread_ids)
        traces = load_traces(session, ids)

        steps = discover_steps(traces)
        model = describe_process_model(steps, traces)

        volume = discover_volume(
            session, traces, model["thresholds"] if model else []
        )

    outcomes = Counter(t.outcome for t in traces)

    starts = [t.started_at for t in traces]

    return {
        "window": {
            "workflows_analyzed": len(traces),
            "limit": limit,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "first_started_at": min(starts).isoformat() if starts else None,
            "last_started_at": max(starts).isoformat() if starts else None,
        },
        "outcomes": {name: outcomes.get(name, 0) for name in OUTCOMES},
        "variants": discover_variants(traces),
        "steps": steps,
        "approvals": discover_approvals(traces),
        "volume": volume,
        "process_model": model,
    }
