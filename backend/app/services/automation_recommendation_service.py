"""
Automation recommendation: what to automate or improve next, ranked
and explained, from what workflow discovery observed.

Deterministic and read-only (no LLM, nothing stored): the input is
the workflow_discovery_service report plus the context graph's
policy rules, so every recommendation points at observed facts.

Four kinds of recommendation:

  automate_manual_step          a modeled step no node implements
  complete_partial_automation   a step only partly implemented
  reduce_approval_wait          people take longer than the workflow
  reduce_step_failures          a node fails often enough to matter

Each gets a score from 0 to 100:

    score = 100 * (0.5 * impact + 0.3 * feasibility + 0.2 * confidence)

- impact       how much of the process the change touches, from the
               observed volume (and, per kind, the share of runs
               affected, the failure rate or the approval wait)
- feasibility  how easy it looks: what the step does and whether a
               system to integrate with is known (a fixed catalogue
               below, explicitly a heuristic)
- confidence   how much data supports it (number of runs analysed)

The weights and thresholds are constants in this module and are
returned with every response, so the ranking is never a black box.
Recommendations are advice for people: nothing is changed or
executed here, and several need a human or policy decision.
"""

import math
import re
from collections import Counter

from app.services.context_graph_seed import PROCESS
from app.services.context_graph_service import get_process_context
from app.services.workflow_discovery_service import discover_workflows


# ============================================================
# RUBRIC (heuristics: tune per organisation)
# ============================================================

WEIGHTS = {"impact": 0.5, "feasibility": 0.3, "confidence": 0.2}

PRIORITY_THRESHOLDS = {"high": 70.0, "medium": 45.0}

PRIORITIES = ("low", "medium", "high")

# Runs at which the volume part of impact reaches 1.0 (log scale).
VOLUME_FULL_SCALE = 1000

# (minimum sample, confidence score, level)
CONFIDENCE_LEVELS = ((50, 0.9, "high"), (10, 0.6, "medium"), (0, 0.3, "low"))

MIN_RUNS_FOR_RELIABLE_RESULTS = 10

# Step failures: reported from this failure rate over this many
# executions; impact reaches 1.0 at FAILURE_RATE_FULL_SCALE.
FAILURE_RATE_THRESHOLD = 0.05
MIN_EXECUTIONS_FOR_FAILURES = 5
FAILURE_RATE_FULL_SCALE = 0.25

# Approval wait: reported when the average wait is at least this
# share of the cycle time of runs that needed approval.
WAIT_SHARE_THRESHOLD = 0.5

# Delegated approval is only floated with this much evidence.
MIN_DECISIONS_FOR_PREAPPROVAL = 20
LOW_REJECTION_RATE = 0.10


# ============================================================
# CATALOGUE: what a manual step looks like it needs
# ============================================================

# First match on the step label wins; "words" are word stems matched
# at the start of a word ("notif" matches "notify" and
# "notification", not "denotify"). feasibility is (with, without) a
# system named in the process model for the step (something to
# integrate with).
PATTERNS = [
    {
        "name": "notification",
        "words": r"notif|send|inform|email|alert",
        "feasibility": (0.9, 0.7),
        "effort": "low",
        "actions": [
            "Send the message automatically when the workflow reaches its "
            "final state (approved, rejected or failed).",
        ],
    },
    {
        "name": "system integration",
        "words": r"record|post|enter|update|book|register",
        "feasibility": (0.8, 0.5),
        "effort": "medium",
        "actions": [
            "Write the result to the target system from the workflow's "
            "business action instead of by hand.",
        ],
    },
    {
        "name": "rule-based validation",
        "words": r"validat|verif|check|review|confirm|reconcil",
        "feasibility": (0.8, 0.7),
        "effort": "medium",
        "actions": [
            "Express the check as rules or a lookup against data the "
            "platform already holds, and run it as a workflow node.",
            "Route only exceptions to a person.",
        ],
    },
    {
        "name": "automated intake",
        "words": r"submit|receive|collect|intake|captur",
        "feasibility": (0.75, 0.75),
        "effort": "medium",
        "actions": [
            "Connect the intake channel to the workflow API "
            "(POST /workflows) so claims enter the process directly.",
            "Validate the claim's required fields on entry.",
        ],
    },
]

DEFAULT_PATTERN = {
    "name": "task automation",
    "words": "",
    "feasibility": (0.5, 0.5),
    "effort": "high",
    "actions": [
        "Analyse the step with the team that performs it before "
        "choosing how to automate it.",
    ],
}

# Suggested fixes for nodes that fail often.
NODE_FIXES = {
    "employee": (0.8, "low", [
        "Validate the employee ID when the claim is submitted, before the "
        "workflow starts.",
        "Ask the submitter to correct a missing or unknown employee ID "
        "instead of ending the run in recovery.",
    ]),
    "policy": (0.8, "low", [
        "Validate that the claim contains a positive expense amount "
        "before the workflow starts.",
    ]),
    "rag": (0.6, "medium", [
        "Check that the business documents are indexed and the document "
        "search is available.",
    ]),
    "action": (0.5, "medium", [
        "Inspect the failed reimbursement actions and add a retry for "
        "transient database errors.",
    ]),
}

DEFAULT_FIX = (0.5, "medium", [
    "Review the failures in the audit trail "
    "(GET /audit-events?event_type=node_failed) and add input "
    "validation or a retry.",
])


# ============================================================
# SCORING
# ============================================================

def volume_scale(workflows: int) -> float:
    """0 for no runs, rising on a log scale to 1.0 at VOLUME_FULL_SCALE."""

    if workflows <= 0:
        return 0.0

    return min(1.0, math.log10(1 + workflows) / math.log10(1 + VOLUME_FULL_SCALE))


def confidence_for(sample: int) -> tuple[float, str]:

    for minimum, score, level in CONFIDENCE_LEVELS:

        if sample >= minimum:
            return score, level

    return CONFIDENCE_LEVELS[-1][1:]


def priority_for(score: float) -> str:

    if score >= PRIORITY_THRESHOLDS["high"]:
        return "high"

    if score >= PRIORITY_THRESHOLDS["medium"]:
        return "medium"

    return "low"


def make_recommendation(
    *, kind, target, title, summary, impact, feasibility, sample, effort,
    rationale, metrics, actions, evidence=None, human_decision=False,
) -> dict:

    impact = round(max(0.0, min(1.0, impact)), 4)
    confidence, level = confidence_for(sample)

    score = round(100 * (
        WEIGHTS["impact"] * impact
        + WEIGHTS["feasibility"] * feasibility
        + WEIGHTS["confidence"] * confidence
    ), 1)

    return {
        "recommendation_id": f"{kind}:{target['key']}",
        "type": kind,
        "title": title,
        "summary": summary,
        "target": target,
        "priority": priority_for(score),
        "score": score,
        "scores": {
            "impact": impact,
            "feasibility": feasibility,
            "confidence": confidence,
        },
        "confidence": level,
        "effort": effort,
        "rationale": rationale,
        "metrics": metrics,
        "suggested_actions": actions,
        "evidence": evidence or [],
        "requires_human_decision": human_decision,
    }


# ============================================================
# MANUAL AND PARTIAL STEPS
# ============================================================

# Not preceded by a letter, so stems never match in the middle of a word.
WORD_START = "(?<![a-z])"


def match_pattern(label: str) -> dict:

    for pattern in PATTERNS:

        if re.search(WORD_START + "(?:" + pattern["words"] + ")", label.lower()):
            return pattern

    return DEFAULT_PATTERN


def step_target(step: dict) -> dict:

    return {
        "kind": "process_step",
        "key": step["step"],
        "label": step["label"],
        "order": step["order"],
    }


def manual_step_recommendation(step: dict, analyzed: int) -> dict:

    pattern = match_pattern(step["label"])
    systems = step["uses_systems"]

    with_system, without_system = pattern["feasibility"]

    performed_by = ", ".join(step["performed_by"]) or "an unspecified role"

    rationale = [
        f"'{step['label']}' is a manual step: no workflow node implements it.",
        f"It is performed by {performed_by}.",
        f"{analyzed} claim(s) were processed in the analysed window and each "
        "one passes through this step.",
    ]

    if systems:
        rationale.append(
            f"The process model says it uses {', '.join(systems)}, "
            "which can be integrated."
        )
    else:
        rationale.append(
            "The process model names no system for this step, so there is "
            "nothing yet to integrate with."
        )

    return make_recommendation(
        kind="automate_manual_step",
        target=step_target(step),
        title=f"Automate: {step['label']}",
        summary=(
            f"Automate the manual step '{step['label']}' "
            f"({pattern['name']}); it touches all {analyzed} observed claim(s)."
        ),
        impact=volume_scale(analyzed),
        feasibility=with_system if systems else without_system,
        sample=analyzed,
        effort=pattern["effort"],
        rationale=rationale,
        metrics={
            "workflows_in_window": analyzed,
            "performed_by": step["performed_by"],
            "uses_systems": systems,
            "pattern": pattern["name"],
        },
        actions=list(pattern["actions"]) + (
            [f"Integrate with {', '.join(systems)}."] if systems else []
        ),
        evidence=step["evidence"],
    )


def step_thresholds(process: dict | None) -> dict[str, list[float]]:
    """Approval thresholds of the rules governing each step."""

    if not process:
        return {}

    by_rule = {
        rule["rule"]["node_key"]: [
            float(a["threshold"]) for a in rule["approvers"]
            if a["threshold"] is not None
        ]
        for rule in process["policy_rules"]
    }

    return {
        step["step"]["node_key"]: sorted(
            t for rule in step["governed_by"] for t in by_rule.get(rule, [])
        )
        for step in process["steps"]
    }


def workflows_above(volume: dict, threshold: float) -> int:
    """Runs with an amount above the threshold, from the amount bands."""

    return sum(
        band["workflows"] for band in volume["by_amount_band"]
        if band.get("above") is not None and band["above"] >= threshold
    )


def partial_step_recommendation(
    step: dict, discovery: dict, thresholds: list[float]
) -> dict:

    analyzed = discovery["window"]["workflows_analyzed"]
    volume = discovery["volume"]
    recorded = volume["workflows_with_run_record"]

    components = [c["label"] for c in step["implemented_by"]]

    rationale = [
        f"'{step['label']}' is only partly automated: "
        f"{', '.join(components)} cover it partially.",
    ]

    metrics = {"components": components, "workflows_in_window": analyzed}

    if thresholds and recorded:

        threshold = thresholds[0]
        affected = workflows_above(volume, threshold)
        share = affected / recorded

        impact = share * volume_scale(analyzed)

        rationale.append(
            f"The rule governing it applies to amounts above {threshold:g}: "
            f"{affected} of {recorded} recorded claim(s) ({share:.0%}) are above it."
        )

        metrics.update({
            "threshold": threshold,
            "workflows_affected": affected,
            "affected_share": round(share, 4),
        })

        summary = (
            f"Complete the automation of '{step['label']}': it applies to "
            f"{affected} of {recorded} recorded claim(s)."
        )

    else:

        impact = 0.5 * volume_scale(analyzed)

        rationale.append(
            "No policy threshold or run records were available to measure "
            "how many claims it affects, so impact is estimated at half the "
            "volume score."
        )

        summary = f"Complete the automation of '{step['label']}'."

    return make_recommendation(
        kind="complete_partial_automation",
        target=step_target(step),
        title=f"Complete automation: {step['label']}",
        summary=summary,
        impact=impact,
        feasibility=0.85,
        sample=analyzed,
        effort="low",
        rationale=rationale,
        metrics=metrics,
        actions=[
            f"Extend {', '.join(components)} to fully implement "
            f"'{step['label']}' (for example, collect a separate decision "
            "from each required approver role).",
        ],
        evidence=step["evidence"],
    )


# ============================================================
# FAILURES AND APPROVAL WAIT (from observed behaviour)
# ============================================================

def failure_recommendations(discovery: dict) -> list[dict]:

    analyzed = discovery["window"]["workflows_analyzed"]

    recovery_runs = sum(
        v["outcomes"].get("failed", 0) for v in discovery["variants"]
    )

    recommendations = []

    for step in discovery["steps"]:

        if (
            step["executions"] < MIN_EXECUTIONS_FOR_FAILURES
            or step["failure_rate"] < FAILURE_RATE_THRESHOLD
        ):
            continue

        node = step["node"]
        feasibility, effort, actions = NODE_FIXES.get(node, DEFAULT_FIX)

        impact = (
            min(1.0, step["failure_rate"] / FAILURE_RATE_FULL_SCALE)
            * volume_scale(step["executions"])
        )

        rationale = [
            f"'{node}' failed in {step['failures']} of {step['executions']} "
            f"execution(s) ({step['failure_rate']:.0%}).",
        ]

        if recovery_runs:
            rationale.append(
                f"{recovery_runs} of {analyzed} run(s) ended as failed, "
                "so the person who submitted the claim got no result."
            )

        recommendations.append(make_recommendation(
            kind="reduce_step_failures",
            target={"kind": "node", "key": node, "label": node, "order": None},
            title=f"Reduce failures in the {node} step",
            summary=(
                f"The {node} step fails {step['failure_rate']:.0%} of the time "
                f"({step['failures']}/{step['executions']})."
            ),
            impact=impact,
            feasibility=feasibility,
            sample=step["executions"],
            effort=effort,
            rationale=rationale,
            metrics={
                "executions": step["executions"],
                "failures": step["failures"],
                "failure_rate": step["failure_rate"],
                "runs_ending_failed": recovery_runs,
            },
            actions=list(actions),
        ))

    return recommendations


def approval_wait_recommendation(discovery: dict) -> dict | None:

    approvals = discovery["approvals"]
    wait = approvals["wait_seconds"]

    if not wait:
        return None

    # Average cycle time of the runs that needed a person.
    cycles = [
        (v["cycle_time_seconds"]["mean"], v["cycle_time_seconds"]["count"])
        for v in discovery["variants"]
        if v["human_approval"] and v["cycle_time_seconds"]
    ]

    count = sum(n for _, n in cycles)

    if not count:
        return None

    cycle_mean = sum(mean * n for mean, n in cycles) / count

    if cycle_mean <= 0:
        return None

    wait_share = min(1.0, wait["mean"] / cycle_mean)

    if wait_share < WAIT_SHARE_THRESHOLD:
        return None

    decided = approvals["approved"] + approvals["rejected"]

    rationale = [
        f"Approvers take {wait['mean']:.0f}s on average (median "
        f"{wait['median']:.0f}s, p95 {wait['p95']:.0f}s), "
        f"{wait_share:.0%} of the {cycle_mean:.0f}s average cycle time of "
        "claims that needed approval.",
        f"{approvals['required_approval']} of {approvals['workflows']} "
        f"run(s) needed approval; {approvals['pending']} are still waiting.",
    ]

    actions = [
        "Remind approvers of pending requests and escalate unanswered ones "
        "to the approver's manager (reporting lines are in the context "
        "graph, including those synced by the directory connector).",
    ]

    human_decision = False

    if decided >= MIN_DECISIONS_FOR_PREAPPROVAL and (
        approvals["rejection_rate"] <= LOW_REJECTION_RATE
    ):

        rationale.append(
            f"Only {approvals['rejected']} of {decided} decision(s) "
            f"({approvals['rejection_rate']:.0%}) were rejections."
        )

        actions.append(
            "Consider delegated or rule-based approval for low-risk "
            "amounts. This changes the approval policy, so it needs a "
            "business decision."
        )

        human_decision = True

    return make_recommendation(
        kind="reduce_approval_wait",
        target={"kind": "approval", "key": "approval", "label": "Human approval", "order": None},
        title="Reduce time waiting for approval",
        summary=(
            f"Waiting for approvers is {wait_share:.0%} of the cycle time "
            f"of claims that need approval."
        ),
        impact=(
            wait_share
            * approvals["approval_rate_of_workflows"]
            * volume_scale(discovery["window"]["workflows_analyzed"])
        ),
        feasibility=0.6,
        sample=wait["count"],
        effort="medium",
        rationale=rationale,
        metrics={
            "wait_seconds": wait,
            "average_cycle_seconds": round(cycle_mean, 3),
            "wait_share_of_cycle": round(wait_share, 4),
            "required_approval": approvals["required_approval"],
            "pending": approvals["pending"],
            "rejection_rate": approvals["rejection_rate"],
        },
        actions=actions,
        human_decision=human_decision,
    )


# ============================================================
# ASSEMBLY
# ============================================================

def build_recommendations(discovery: dict, process: dict | None) -> dict:
    """Recommendations from a discovery report (pure function)."""

    analyzed = discovery["window"]["workflows_analyzed"]
    model = discovery["process_model"]

    recommendations = []
    warnings = []

    if model:

        thresholds = step_thresholds(process)

        for step in model["steps"]:

            if step["automation"] == "manual":

                recommendations.append(manual_step_recommendation(step, analyzed))

            elif step["automation"] == "partial":

                recommendations.append(partial_step_recommendation(
                    step, discovery, thresholds.get(step["step"], [])
                ))

    else:

        warnings.append(
            "The process is not in the context graph, so step-level "
            "recommendations are unavailable."
        )

    recommendations += failure_recommendations(discovery)

    wait = approval_wait_recommendation(discovery)

    if wait:
        recommendations.append(wait)

    if analyzed == 0:

        warnings.append(
            "No workflow runs were found in the window; recommendations "
            "rest on the process model only."
        )

    elif analyzed < MIN_RUNS_FOR_RELIABLE_RESULTS:

        warnings.append(
            f"Only {analyzed} run(s) were analysed; confidence is low."
        )

    recommendations.sort(key=lambda r: (-r["score"], r["recommendation_id"]))

    return {"recommendations": recommendations, "warnings": warnings}


def method() -> dict:
    """The rubric, returned with every response."""

    return {
        "formula": "score = 100 * (0.5 * impact + 0.3 * feasibility + 0.2 * confidence)",
        "weights": dict(WEIGHTS),
        "priority_thresholds": dict(PRIORITY_THRESHOLDS),
        "volume_full_scale": VOLUME_FULL_SCALE,
        "notes": [
            "Impact comes from observed volume, and per kind from the share "
            "of runs affected, the failure rate or the approval wait.",
            "Feasibility is a fixed heuristic from the kind of step and "
            "whether a system to integrate with is known.",
            "Confidence reflects how many runs were analysed.",
            "Manual steps happen outside the system, so their impact uses "
            "the claim volume, not measured effort.",
            "Recommendations are advice; nothing is changed or executed.",
        ],
    }


def recommend_automation(
    *,
    limit: int | None = None,
    since=None,
    until=None,
    min_priority: str = "low",
) -> dict:
    """
    Discover how the process runs, then recommend what to automate or
    improve. Raises ValueError for invalid arguments.
    """

    if min_priority not in PRIORITIES:
        raise ValueError(f"min_priority must be one of {', '.join(PRIORITIES)}.")

    options = {"since": since, "until": until}

    if limit is not None:
        options["limit"] = limit

    discovery = discover_workflows(**options)

    result = build_recommendations(discovery, get_process_context(PROCESS))

    minimum = PRIORITIES.index(min_priority)

    recommendations = [
        r for r in result["recommendations"]
        if PRIORITIES.index(r["priority"]) >= minimum
    ]

    return {
        "basis": {
            "process": PROCESS,
            "window": discovery["window"],
            "outcomes": discovery["outcomes"],
        },
        "summary": {
            "total": len(recommendations),
            "by_priority": {
                p: sum(1 for r in recommendations if r["priority"] == p)
                for p in reversed(PRIORITIES)
            },
            "by_type": dict(sorted(Counter(r["type"] for r in recommendations).items())),
        },
        "recommendations": recommendations,
        "warnings": result["warnings"],
        "method": method(),
    }
