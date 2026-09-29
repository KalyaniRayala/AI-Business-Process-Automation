from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.services.agent_graph import agent_graph
from app.services.database_service import initialize_database


# ============================================================
# STARTUP
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    i  nitialize_database()

    yield


app = FastAPI(
    title="AI Business Process Discovery & Automation Platform",
    description="AI-powered business process discovery and automation platform",
    version="0.1.0",
    lifespan=lifespan
)


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


class WorkflowResponse(BaseModel):

    thread_id: str

    # running | awaiting_approval | success | rejected | failed
    status: str

    approval_status: str | None = None

    approval_request: dict[str, Any] | None = None

    answer: str | None = None

    action_result: dict[str, Any] | None = None

    error_message: str | None = None


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
    return {
        "status": "ok"
    }


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

    agent_graph.invoke(
        {
            "question": request.question
        },
        config=get_config(thread_id)
    )

    return build_response(thread_id)


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

    agent_graph.invoke(
        Command(resume=request.decision),
        config=get_config(thread_id)
    )

    return build_response(thread_id)
