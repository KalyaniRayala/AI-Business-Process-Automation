import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.observability.context import workflow_context
from app.observability.health import readiness, summary
from app.observability.instrumentation import (
    WorkflowObserver,
    record_workflow_run,
    set_pending_approvals,
)
from app.observability.logging_setup import configure_logging
from app.observability.metrics import CONTENT_TYPE as METRICS_CONTENT_TYPE, registry
from app.observability.middleware import observability_middleware
from app.services.agent_graph import agent_graph, checkpointer
from app.services.audit_service import (
    MAX_PAGE_SIZE,
    AuditEventType,
    list_events,
    record_event,
)
from app.services.automation_recommendation_service import recommend_automation
from app.services.workflow_audit import NodeAuditor
from app.services.workflow_discovery_service import (
    DEFAULT_LIMIT as DEFAULT_DISCOVERY_LIMIT,
    MAX_LIMIT as MAX_DISCOVERY_LIMIT,
    discover_workflows,
)
from app.services.checkpointer import setup_checkpointer
from app.services.context_graph_seed import sync_context_graph
from app.services.context_graph_service import (
    MAX_DEPTH as MAX_CONTEXT_DEPTH,
    MAX_NEIGHBORS as MAX_CONTEXT_NEIGHBORS,
    EdgeType,
    NodeType,
    get_process_context,
    neighbors as context_neighbors,
)
from app.services.database_service import (
    MAX_WORKFLOW_PAGE_SIZE,
    WORKFLOW_STATUSES,
    create_workflow_run,
    get_workflow_run,
    initialize_database,
    list_workflow_runs,
    record_approval_decision,
    to_money,
    update_workflow_run,
)


# ============================================================
# LOGGING
# ============================================================

# Structured or human-readable, with correlation ids and secret
# redaction (LOG_LEVEL, LOG_FORMAT).
configure_logging()

logger = logging.getLogger(__name__)


# ============================================================
# STARTUP
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    initialize_database()

    # Curated process knowledge + employee projection (idempotent).
    sync_context_graph()

    setup_checkpointer(checkpointer)

    yield


app = FastAPI(
    title="AI Business Process Discovery & Automation Platform",
    description="AI-powered business process discovery and automation platform",
    version="0.1.0",
    lifespan=lifespan
)


# Correlation id, timing, request counters and error counting.
app.middleware("http")(observability_middleware)


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================

class WorkflowRequest(BaseModel):

    question: str = Field(
        min_length=1,
        examples=[
            "For employee E102, explain the expense reimbursement "
            "process for a $750 expense and state what approval "
            "is required."
        ]
    )


class DecisionRequest(BaseModel):

    decision: Literal["approve", "reject"]

    approver: str | None = Field(
        default=None,
        max_length=100,
        examples=["Priya"]
    )

    comment: str | None = Field(
        default=None,
        max_length=2000
    )


class WorkflowResponse(BaseModel):

    thread_id: str

    # running | awaiting_approval | success | rejected | failed
    status: str

    approval_status: str | None = None

    approval_request: dict[str, Any] | None = None

    answer: str | None = None

    action_result: dict[str, Any] | None = None

    error_message: str | None = None


class WorkflowSummaryResponse(BaseModel):

    thread_id: str

    question: str

    employee_id: str | None = None

    expense_amount: float | None = None

    # running | awaiting_approval | success | rejected | failed
    status: str

    approval_status: str | None = None

    failed_node: str | None = None

    error_message: str | None = None

    created_at: str

    updated_at: str


class WorkflowListResponse(BaseModel):

    # Most recently started first.
    workflows: list[WorkflowSummaryResponse]

    # All runs matching the filter, not just this page.
    total: int

    limit: int

    offset: int


class AuditEventResponse(BaseModel):

    id: int

    event_id: str

    occurred_at: datetime

    thread_id: str | None = None

    event_type: str

    # system | human | agent
    actor_type: str

    actor_id: str | None = None

    node: str | None = None

    entity_type: str | None = None

    entity_id: str | None = None

    # success | failure | pending | info
    status: str

    message: str

    details: dict[str, Any] | None = None

    error_type: str | None = None

    error_message: str | None = None


class AuditHistoryResponse(BaseModel):

    thread_id: str

    # Oldest first, in recording order.
    events: list[AuditEventResponse]

    count: int

    limit: int

    # Pass as after_id to get the next page; null when this page
    # was not full. A full last page can be followed by an empty one.
    next_after_id: int | None = None


class AuditEventPage(BaseModel):

    # Oldest first, in recording order.
    events: list[AuditEventResponse]

    count: int

    limit: int

    # Pass as after_id to get the next page; null when this page
    # was not full.
    next_after_id: int | None = None


class ContextNodeResponse(BaseModel):

    node_key: str

    node_type: str

    label: str

    properties: dict[str, Any] | None = None

    # system_of_record | document | curated | derived
    source_type: str

    source_ref: str | None = None

    is_active: bool


class ContextEdgeResponse(BaseModel):

    from_key: str

    edge_type: str

    to_key: str

    properties: dict[str, Any] | None = None

    source_type: str

    source_ref: str | None = None

    confidence: float | None = None


class ContextNeighborResponse(BaseModel):

    # Hops from the requested node (shortest path).
    depth: int

    node: ContextNodeResponse

    # The edge that first reached this node.
    via: ContextEdgeResponse


class NodeContextResponse(BaseModel):

    node: ContextNodeResponse

    depth: int

    # out | in | both
    direction: str

    count: int

    # True when the neighbor limit was reached.
    truncated: bool

    neighbors: list[ContextNeighborResponse]


class StepImplementationResponse(BaseModel):

    # agent_node or tool
    component: ContextNodeResponse

    properties: dict[str, Any] | None = None


class ProcessStepResponse(BaseModel):

    order: int | None = None

    step: ContextNodeResponse

    next_steps: list[str]

    performed_by: list[ContextNodeResponse]

    uses_systems: list[ContextNodeResponse]

    # policy_rule node keys
    governed_by: list[str]

    implemented_by: list[StepImplementationResponse]

    # Document locations, e.g. "document:data/expense_process.txt#L15"
    evidence: list[str]


class RuleApproverResponse(BaseModel):

    role: ContextNodeResponse

    threshold: float | None = None


class PolicyRuleResponse(BaseModel):

    rule: ContextNodeResponse

    approvers: list[RuleApproverResponse]

    # policy node keys
    policies: list[str]

    evidence: list[str]


class ProcessContextResponse(BaseModel):

    process: ContextNodeResponse

    # Ordered by step order.
    steps: list[ProcessStepResponse]

    policy_rules: list[PolicyRuleResponse]

    evidence: list[str]


class DurationSummaryResponse(BaseModel):

    count: int

    mean: float

    median: float

    p95: float

    max: float


class DiscoveryWindowResponse(BaseModel):

    workflows_analyzed: int

    limit: int

    since: str | None = None

    until: str | None = None

    first_started_at: str | None = None

    last_started_at: str | None = None


class WorkflowVariantResponse(BaseModel):

    variant_id: str

    # Stages in order; nodes in one stage ran in parallel.
    path: list[list[str]]

    path_text: str

    # True when the runs paused for a person to approve.
    human_approval: bool

    workflows: int

    share: float

    outcomes: dict[str, int]

    # Start to finish, including any wait for approval.
    cycle_time_seconds: DurationSummaryResponse | None = None

    sample_thread_ids: list[str]


class StepStatsResponse(BaseModel):

    node: str

    workflows: int

    executions: int

    failures: int

    failure_rate: float

    duration_seconds: DurationSummaryResponse | None = None

    # Orders of the modeled process steps this node implements.
    modeled_steps: list[int] = []


class ApprovalStatsResponse(BaseModel):

    workflows: int

    required_approval: int

    approval_rate_of_workflows: float

    approved: int

    rejected: int

    pending: int

    rejection_rate: float

    # Time from the approval request to the decision.
    wait_seconds: DurationSummaryResponse | None = None


class DepartmentVolumeResponse(BaseModel):

    department: str

    workflows: int

    total_amount: float

    required_approval: int

    rejected: int


class AmountBandResponse(BaseModel):

    band: str

    # Numeric bounds: above < amount <= up_to (null = unbounded).
    above: float | None = None

    up_to: float | None = None

    workflows: int

    required_approval: int

    rejected: int


class VolumeResponse(BaseModel):

    workflows_with_run_record: int

    workflows_without_run_record: int

    by_department: list[DepartmentVolumeResponse]

    by_amount_band: list[AmountBandResponse]


class ModelComponentResponse(BaseModel):

    component: str

    label: str

    coverage: str


class ModelStepResponse(BaseModel):

    order: int | None = None

    step: str

    label: str

    performed_by: list[str]

    uses_systems: list[str]

    automation: Literal["automated", "partial", "manual"]

    implemented_by: list[ModelComponentResponse]

    # Workflows that used a node implementing this step; null for
    # manual steps, which the system cannot observe.
    workflows_observed: int | None = None

    evidence: list[str]


class DiscoveredProcessModelResponse(BaseModel):

    process: str

    label: str

    steps: list[ModelStepResponse]

    # Manual and partially automated steps.
    automation_gaps: list[ModelStepResponse]

    # Nodes that ran but implement no modeled step.
    unmapped_nodes: list[str]

    # Approval thresholds from the policy rules.
    thresholds: list[float]


class WorkflowDiscoveryResponse(BaseModel):

    window: DiscoveryWindowResponse

    outcomes: dict[str, int]

    # Most common first.
    variants: list[WorkflowVariantResponse]

    steps: list[StepStatsResponse]

    approvals: ApprovalStatsResponse

    volume: VolumeResponse

    # Null if the process is not in the context graph.
    process_model: DiscoveredProcessModelResponse | None = None


class RecommendationTargetResponse(BaseModel):

    # process_step | node | approval
    kind: str

    key: str

    label: str

    order: int | None = None


class RecommendationScoresResponse(BaseModel):

    # Each 0..1; the score weights them 0.5 / 0.3 / 0.2.
    impact: float

    feasibility: float

    confidence: float


class AutomationRecommendationResponse(BaseModel):

    # "<type>:<target key>"
    recommendation_id: str

    type: Literal[
        "automate_manual_step",
        "complete_partial_automation",
        "reduce_approval_wait",
        "reduce_step_failures",
    ]

    title: str

    summary: str

    target: RecommendationTargetResponse

    priority: Literal["low", "medium", "high"]

    # 0..100
    score: float

    scores: RecommendationScoresResponse

    confidence: Literal["low", "medium", "high"]

    effort: Literal["low", "medium", "high"]

    # Observed facts behind the recommendation.
    rationale: list[str]

    metrics: dict[str, Any]

    suggested_actions: list[str]

    # Document references from the process model.
    evidence: list[str]

    # True when acting on it changes a business policy.
    requires_human_decision: bool


class RecommendationBasisResponse(BaseModel):

    process: str

    window: DiscoveryWindowResponse

    outcomes: dict[str, int]


class RecommendationSummaryResponse(BaseModel):

    total: int

    by_priority: dict[str, int]

    by_type: dict[str, int]


class RecommendationMethodResponse(BaseModel):

    formula: str

    weights: dict[str, float]

    priority_thresholds: dict[str, float]

    volume_full_scale: int

    notes: list[str]


class AutomationRecommendationsResponse(BaseModel):

    basis: RecommendationBasisResponse

    summary: RecommendationSummaryResponse

    # Highest score first.
    recommendations: list[AutomationRecommendationResponse]

    warnings: list[str]

    # The scoring rubric, so rankings can be explained.
    method: RecommendationMethodResponse


# ============================================================
# HELPERS
# ============================================================

def get_config(thread_id: str) -> dict:

    return {
        "configurable": {
            "thread_id": thread_id
        }
    }


def build_response(thread_id: str) -> WorkflowResponse:
    """
    Read the checkpointed graph state and convert it
    into an API response.
    """

    snapshot = agent_graph.get_state(
        get_config(thread_id)
    )

    if not snapshot.values:

        raise HTTPException(
            status_code=404,
            detail=f"Workflow {thread_id} was not found."
        )

    values = snapshot.values

    pending = [
        interrupt.value
        for task in snapshot.tasks
        for interrupt in task.interrupts
    ]

    if pending:

        status = "awaiting_approval"

    elif snapshot.next:

        status = "running"

    else:

        status = values.get("status", "unknown")

    return WorkflowResponse(

        thread_id=thread_id,

        status=status,

        approval_status=values.get("approval_status"),

        approval_request=pending[0] if pending else None,

        answer=values.get("answer"),

        action_result=values.get("action_result"),

        error_message=values.get("error_message") or None
    )


def save_workflow_run(response: WorkflowResponse) -> None:
    """
    Store the workflow's current summary in the database.
    """

    values = agent_graph.get_state(
        get_config(response.thread_id)
    ).values

    employee_data = values.get("employee_data") or {}
    policy_result = values.get("policy_result") or {}

    employee_id = None

    # Only link employees that exist (foreign key).
    if employee_data.get("success"):
        employee_id = employee_data["employee"]["employee_id"]

    expense_amount = None

    if policy_result.get("success"):
        expense_amount = to_money(policy_result["expense_amount"])

    update_workflow_run(
        response.thread_id,
        status=response.status,
        approval_status=response.approval_status,
        failed_node=values.get("failed_node") or None,
        error_message=response.error_message,
        employee_id=employee_id,
        expense_amount=expense_amount
    )


def run_graph(graph_input, thread_id: str) -> WorkflowResponse:
    """
    Run or resume the graph, then store the workflow summary.
    """

    resumed = isinstance(graph_input, Command)

    auditor = NodeAuditor(thread_id, resumed=resumed)

    operation = "resume" if resumed else "run"

    # Measures the same task stream the auditor reads.
    observer = WorkflowObserver(thread_id, operation=operation)

    started = time.perf_counter()

    # Puts the thread id on every log line the graph produces.
    with workflow_context(thread_id):

        try:

            # Same execution as invoke(); streaming task events only
            # lets the node-level audit observe each node.
            for task in agent_graph.stream(
                graph_input,
                config=get_config(thread_id),
                stream_mode="tasks"
            ):
                auditor.observe(task)
                observer.observe(task)

        except Exception as error:

            auditor.fail_pending(error)

            observer.fail_pending(error)

            record_workflow_run(
                "failed", time.perf_counter() - started, operation
            )

            update_workflow_run(
                thread_id,
                status="failed",
                error_message=str(error)
            )

            record_event(
                event_type=AuditEventType.WORKFLOW_ERROR,
                message="Workflow execution raised an unexpected error.",
                status="failure",
                thread_id=thread_id,
                error=error
            )

            raise

        response = build_response(thread_id)

        record_workflow_run(
            response.status, time.perf_counter() - started, operation
        )

        save_workflow_run(response)

        record_run_outcome(response)

        return response


# Nodes whose failures are handled by the graph's recovery node.
RECOVERABLE_NODES = {"employee", "policy", "rag"}


def record_run_outcome(response: WorkflowResponse) -> None:
    """
    Audit where this run or resume ended: paused for approval,
    completed, rejected or failed. Best-effort; never raises for
    database errors.
    """

    thread_id = response.thread_id

    if response.status == "awaiting_approval":

        request = response.approval_request or {}
        policy = request.get("policy") or {}
        employee = (request.get("employee") or {}).get("employee") or {}

        record_event(
            event_type=AuditEventType.APPROVAL_REQUESTED,
            message="Expense requires approval; workflow paused.",
            status="pending",
            thread_id=thread_id,
            node="approval",
            entity_type="employee" if employee else None,
            entity_id=employee.get("employee_id"),
            details={
                "expense_amount": policy.get("expense_amount"),
                "required_approval": policy.get("approval")
            }
        )

    elif response.status == "success":

        record_event(
            event_type=AuditEventType.WORKFLOW_COMPLETED,
            message="Workflow completed successfully.",
            status="success",
            thread_id=thread_id,
            node="final",
            details={
                "approval_status": response.approval_status
            }
        )

    elif response.status == "rejected":

        record_event(
            event_type=AuditEventType.WORKFLOW_REJECTED,
            message="Workflow ended: expense was rejected.",
            status="info",
            thread_id=thread_id,
            node="final",
            details={
                "approval_status": response.approval_status
            }
        )

    elif response.status == "failed":

        values = agent_graph.get_state(
            get_config(thread_id)
        ).values

        failed_node = values.get("failed_node") or None

        action_result = values.get("action_result") or {}

        if action_result.get("success") is False:
            failed_node = failed_node or "action"

        error_message = (
            response.error_message
            or action_result.get("error")
        )

        if failed_node in RECOVERABLE_NODES:

            record_event(
                event_type=AuditEventType.WORKFLOW_RECOVERY,
                message=(
                    "A workflow step failed; the recovery path "
                    "ended the workflow."
                ),
                status="failure",
                thread_id=thread_id,
                node="recovery",
                details={"failed_node": failed_node},
                error_type=values.get("error_type") or None,
                error_message=error_message
            )

        record_event(
            event_type=AuditEventType.WORKFLOW_FAILED,
            message="Workflow failed.",
            status="failure",
            thread_id=thread_id,
            node=failed_node,
            details={"failed_node": failed_node},
            error_message=error_message
        )


# ============================================================
# HEALTH
# ============================================================

@app.get("/")
def root():
    return {
        "message": "AI Business Process Automation Platform",
        "status": "running"
    }


@app.get("/health")
def health():
    """Liveness: the process is up and serving."""

    return {
        "status": "ok"
    }


@app.get("/health/ready")
def health_ready(response: Response):
    """
    Readiness: the database, the vector store and the model
    provider are usable. No check calls Cohere.

    Returns 503 when an essential check fails, so a load balancer
    can act on it. A degraded result (in-memory workflow state) is
    still a 200: it serves, with a caveat.
    """

    result = readiness()

    if result["status"] == "failed":
        response.status_code = 503

    return result


@app.get(
    "/metrics",
    response_class=PlainTextResponse,
    responses={
        200: {
            "content": {"text/plain": {}},
            "description": "Prometheus text exposition format.",
        }
    },
)
def metrics_endpoint():
    """
    Metrics for this process, in the Prometheus text format.

    Counters reset when the process restarts, and with several
    workers a scrape returns one worker's view.
    """

    return PlainTextResponse(
        registry.render(),
        media_type=METRICS_CONTENT_TYPE,
    )


@app.get("/observability/summary")
def observability_summary(
    limit: int = Query(
        default=10,
        ge=1,
        le=50,
        description="How many rows to include in each list."
    )
):
    """
    A compact live view for the dashboard: what this process has
    served, how the workflows went, and what failed recently.

    Per process and lost on restart. The audit trail is the
    durable record.
    """

    payload = summary(limit=limit)

    # The approval queue is durable, so read it from the database
    # rather than from this process's counters.
    try:
        pending = list_workflow_runs(status="awaiting_approval", limit=1)["total"]

        set_pending_approvals(pending)

        payload["workflows"]["awaiting_approval"] = pending

    except Exception:
        logger.exception("Could not count the workflows awaiting approval")

        payload["workflows"]["awaiting_approval"] = None

    return payload


# ============================================================
# WORKFLOWS
# ============================================================

@app.post(
    "/workflows",
    response_model=WorkflowResponse,
    status_code=201
)
def start_workflow(request: WorkflowRequest):
    """
    Start a new expense workflow. The response has status
    "awaiting_approval" when a human decision is needed.
    """

    thread_id = str(uuid4())

    create_workflow_run(
        thread_id,
        request.question
    )

    return run_graph(
        {
            "question": request.question
        },
        thread_id
    )


@app.get(
    "/workflows",
    response_model=WorkflowListResponse
)
def list_workflows(
    status: Literal[WORKFLOW_STATUSES] | None = Query(
        default=None,
        description="Only runs with this status."
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=MAX_WORKFLOW_PAGE_SIZE
    ),
    offset: int = Query(default=0, ge=0)
):
    """
    Stored summaries of workflow runs, most recently started first.

    Use status=awaiting_approval for the runs waiting for a person.
    """

    page = list_workflow_runs(status=status, limit=limit, offset=offset)

    return WorkflowListResponse(
        workflows=page["workflows"],
        total=page["total"],
        limit=limit,
        offset=offset
    )


@app.get(
    "/workflows/{thread_id}",
    response_model=WorkflowResponse
)
def get_workflow(thread_id: str):

    return build_response(thread_id)


@app.post(
    "/workflows/{thread_id}/decision",
    response_model=WorkflowResponse
)
def submit_decision(
    thread_id: str,
    request: DecisionRequest
):
    """
    Approve or reject a paused workflow and resume it.
    """

    current = build_response(thread_id)

    if current.status != "awaiting_approval":

        raise HTTPException(
            status_code=409,
            detail=(
                f"Workflow {thread_id} is not awaiting approval "
                f"(status: {current.status})."
            )
        )

    # Record the decision before resuming. The database allows
    # one decision per workflow, so concurrent requests cannot
    # both resume it.
    recorded = record_approval_decision(
        thread_id,
        decision=request.decision,
        approver=request.approver,
        comment=request.comment
    )

    if not recorded:

        raise HTTPException(
            status_code=409,
            detail=f"Workflow {thread_id} has already been decided."
        )

    record_event(
        event_type=AuditEventType.WORKFLOW_RESUMED,
        message="Workflow resumed after the approval decision.",
        status="info",
        actor_type="human",
        actor_id=request.approver,
        thread_id=thread_id,
        node="approval",
        details={"decision": request.decision}
    )

    return run_graph(
        Command(resume=request.decision),
        thread_id
    )


# ============================================================
# AUDIT
# ============================================================

@app.get(
    "/workflows/{thread_id}/audit",
    response_model=AuditHistoryResponse
)
def get_workflow_audit(
    thread_id: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=MAX_PAGE_SIZE
    ),
    after_id: int | None = Query(
        default=None,
        ge=0,
        description="Return events after this id (next_after_id "
                    "from the previous page)."
    )
):
    """
    Audit history of a workflow, oldest first.
    """

    events = list_events(
        thread_id=thread_id,
        after_id=after_id,
        limit=limit
    )

    # Unknown only if there is neither a run nor any audit event;
    # workflows created before auditing return an empty list.
    if (
        not events
        and get_workflow_run(thread_id) is None
        and (
            after_id is None
            or not list_events(thread_id=thread_id, limit=1)
        )
    ):

        raise HTTPException(
            status_code=404,
            detail=f"Workflow {thread_id} was not found."
        )

    return AuditHistoryResponse(
        thread_id=thread_id,
        events=events,
        count=len(events),
        limit=limit,
        next_after_id=next_page_cursor(events, limit)
    )


@app.get(
    "/audit-events",
    response_model=AuditEventPage
)
def search_audit_events(
    thread_id: str | None = Query(default=None, max_length=64),
    event_type: AuditEventType | None = Query(default=None),
    entity_type: str | None = Query(default=None, max_length=50),
    entity_id: str | None = Query(default=None, max_length=100),
    limit: int = Query(
        default=100,
        ge=1,
        le=MAX_PAGE_SIZE
    ),
    after_id: int | None = Query(
        default=None,
        ge=0,
        description="Return events after this id (next_after_id "
                    "from the previous page)."
    )
):
    """
    Search audit events across workflows, oldest first.

    Filters are combined with AND. No match returns an empty
    page (this is a search, not a lookup).
    """

    events = list_events(
        thread_id=thread_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        after_id=after_id,
        limit=limit
    )

    return AuditEventPage(
        events=events,
        count=len(events),
        limit=limit,
        next_after_id=next_page_cursor(events, limit)
    )


def next_page_cursor(events: list[dict], limit: int) -> int | None:
    """
    after_id for the next page, or None when this page was not full.
    """

    if len(events) == limit:
        return events[-1]["id"]

    return None


# ============================================================
# CONTEXT GRAPH
# ============================================================

@app.get(
    "/context/processes/{process_key}",
    response_model=ProcessContextResponse
)
def get_process(process_key: str):
    """
    A business process with its ordered steps, roles, systems,
    governing policy rules, implementing agent nodes/tools and
    document evidence. Accepts "expense_reimbursement" or
    "process:expense_reimbursement".
    """

    if not process_key.startswith(f"{NodeType.PROCESS.value}:"):
        process_key = f"{NodeType.PROCESS.value}:{process_key}"

    process = get_process_context(process_key)

    if process is None:

        raise HTTPException(
            status_code=404,
            detail=f"Process {process_key} was not found."
        )

    return process


# ":path" because node keys may contain "/", e.g.
# document:data/expense_process.txt
@app.get(
    "/context/nodes/{node_key:path}",
    response_model=NodeContextResponse
)
def get_context_node(
    node_key: str,
    depth: int = Query(
        default=1,
        ge=1,
        le=MAX_CONTEXT_DEPTH,
        description="Hops to traverse (cycles are visited once)."
    ),
    direction: Literal["out", "in", "both"] = Query(default="both"),
    edge_type: list[EdgeType] | None = Query(
        default=None,
        description="Only follow these edge types (repeatable)."
    ),
    include_inactive: bool = Query(default=False),
    limit: int = Query(
        default=200,
        ge=1,
        le=MAX_CONTEXT_NEIGHBORS
    )
):
    """
    A context node and the nodes related to it within `depth` hops.
    """

    try:

        result = context_neighbors(
            node_key,
            depth=depth,
            direction=direction,
            edge_types=edge_type,
            include_inactive=include_inactive,
            limit=limit
        )

    except LookupError:

        raise HTTPException(
            status_code=404,
            detail=f"Context node {node_key} was not found."
        )

    return NodeContextResponse(
        node=result["start"],
        depth=result["depth"],
        direction=result["direction"],
        count=len(result["neighbors"]),
        truncated=result["truncated"],
        neighbors=result["neighbors"]
    )


# ============================================================
# WORKFLOW DISCOVERY API
# ============================================================

@app.get(
    "/discovery/workflows",
    response_model=WorkflowDiscoveryResponse
)
def discover_workflow_patterns(
    limit: int = Query(
        default=DEFAULT_DISCOVERY_LIMIT,
        ge=1,
        le=MAX_DISCOVERY_LIMIT,
        description="Analyse the most recent N workflow runs."
    ),
    since: datetime | None = Query(
        default=None,
        description="Only runs started at or after this time (ISO 8601)."
    ),
    until: datetime | None = Query(
        default=None,
        description="Only runs started at or before this time (ISO 8601)."
    )
):
    """
    How the process actually runs, from the audit trail: paths taken
    (variants), step failures and durations, approvals and their
    wait times, volume, and the gaps between the modeled process in
    the context graph and the automation that exists.

    Read-only; computed on every request.
    """

    try:

        return discover_workflows(limit=limit, since=since, until=until)

    except ValueError as error:

        raise HTTPException(status_code=422, detail=str(error))


# ============================================================
# AUTOMATION RECOMMENDATION API
# ============================================================

@app.get(
    "/automation/recommendations",
    response_model=AutomationRecommendationsResponse
)
def get_automation_recommendations(
    limit: int = Query(
        default=DEFAULT_DISCOVERY_LIMIT,
        ge=1,
        le=MAX_DISCOVERY_LIMIT,
        description="Base the recommendations on the most recent N workflow runs."
    ),
    since: datetime | None = Query(
        default=None,
        description="Only runs started at or after this time (ISO 8601)."
    ),
    until: datetime | None = Query(
        default=None,
        description="Only runs started at or before this time (ISO 8601)."
    ),
    min_priority: Literal["low", "medium", "high"] = Query(
        default="low",
        description="Leave out recommendations below this priority."
    )
):
    """
    What to automate or improve next, ranked and explained.

    Built on workflow discovery (the same window parameters): manual
    and partly automated process steps, steps that fail often, and
    approval waits. Every recommendation lists the observed facts
    behind it, and the scoring rubric is returned with the response.

    Read-only advice computed on every request; nothing is executed.
    """

    try:

        return recommend_automation(
            limit=limit, since=since, until=until, min_priority=min_priority
        )

    except ValueError as error:

        raise HTTPException(status_code=422, detail=str(error))
