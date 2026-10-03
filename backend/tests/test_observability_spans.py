"""
Spans, the recent-activity buffers, and the instrumentation
wrappers. The rule under test throughout: measuring must never
change or break the thing being measured.
"""

import pytest

from app.observability import metrics, spans
from app.observability.context import request_context, workflow_context
from app.observability.instrumentation import (
    WorkflowObserver,
    measure_model_call,
    record_workflow_run,
    set_pending_approvals,
)
from app.observability.spans import (
    RecentActivity,
    SpanRecord,
    record_error,
    record_span,
    span,
)


@pytest.fixture(autouse=True)
def clean_metrics():
    """These tests read global counters, so start from zero."""

    metrics.registry.clear()
    spans.reset()

    yield

    metrics.registry.clear()
    spans.reset()


def make_record(operation="op", duration=1.0, status="success"):

    return SpanRecord(
        operation=operation,
        status=status,
        duration_seconds=duration,
        started_at="2026-10-02T00:00:00+00:00",
    )


# ============================================================
# SPAN
# ============================================================

def test_a_span_records_a_success():

    with span("demo", kind="test") as handle:
        handle.set(rows=3)

    record = spans.recent_spans.latest()[0]

    assert record.operation == "demo"
    assert record.status == "success"
    assert record.duration_seconds >= 0
    assert record.attributes == {"kind": "test", "rows": 3}


def test_a_span_counts_the_operation():

    with span("demo"):
        pass

    assert metrics.operations.value(operation="demo", status="success") == 1
    assert metrics.operation_duration.count(operation="demo") == 1


def test_a_failing_span_re_raises_and_records_the_error():

    with pytest.raises(ValueError, match="boom"):
        with span("demo"):
            raise ValueError("boom")

    record = spans.recent_spans.latest()[0]

    assert record.status == "error"
    assert record.error == "ValueError: boom"

    assert metrics.operations.value(operation="demo", status="error") == 1

    # Errors are also kept in their own buffer.
    assert spans.recent_errors.latest()[0].operation == "demo"


def test_a_successful_span_is_not_in_the_error_buffer():

    with span("demo"):
        pass

    assert spans.recent_errors.latest() == []


def test_a_span_carries_the_correlation_ids():

    with request_context("req1"), workflow_context("thread1"):
        with span("demo"):
            pass

    record = spans.recent_spans.latest()[0]

    assert record.request_id == "req1"
    assert record.thread_id == "thread1"


def test_a_span_result_is_json_friendly():

    import json

    with span("demo", note="hello"):
        pass

    json.dumps(spans.recent_spans.latest()[0].to_dict())


def test_the_record_omits_empty_fields():

    payload = make_record().to_dict()

    assert "error" not in payload
    assert "request_id" not in payload


def test_recording_a_span_survives_a_broken_metric(monkeypatch):
    """A failure to measure must not propagate to the caller."""

    def explode(*args, **kwargs):
        raise RuntimeError("metrics are down")

    monkeypatch.setattr(metrics.operations, "inc", explode)

    record = record_span("demo", "success", 0.1)

    assert record.operation == "demo"


def test_record_error_counts_by_kind_and_place():

    record_error("ValueError", "http")
    record_error("ValueError", "http")

    assert metrics.errors.value(kind="ValueError", where="http") == 2


# ============================================================
# RECENT ACTIVITY
# ============================================================

def test_the_buffer_keeps_only_the_most_recent():

    buffer = RecentActivity(capacity=3)

    for index in range(5):
        buffer.add(make_record(operation=f"op{index}"))

    assert [record.operation for record in buffer.latest()] == [
        "op4",
        "op3",
        "op2",
    ]


def test_latest_is_newest_first_and_limited():

    buffer = RecentActivity(capacity=10)

    for index in range(5):
        buffer.add(make_record(operation=f"op{index}"))

    assert [record.operation for record in buffer.latest(2)] == ["op4", "op3"]


def test_slowest_is_sorted_by_duration():

    buffer = RecentActivity(capacity=10)

    buffer.add(make_record("quick", duration=0.1))
    buffer.add(make_record("slow", duration=9.0))
    buffer.add(make_record("medium", duration=1.0))

    assert [record.operation for record in buffer.slowest(2)] == ["slow", "medium"]


def test_an_empty_buffer_returns_nothing():

    buffer = RecentActivity(capacity=5)

    assert buffer.latest() == []
    assert buffer.slowest() == []


# ============================================================
# MODEL CALLS
# ============================================================

def test_a_model_call_is_counted_and_timed():

    @measure_model_call("chat")
    def generate(question):
        return f"answer to {question}"

    assert generate("why?") == "answer to why?"

    assert (
        metrics.model_calls.value(
            provider="cohere", operation="chat", status="success"
        )
        == 1
    )
    assert metrics.model_duration.count(provider="cohere", operation="chat") == 1


def test_a_failing_model_call_is_counted_as_an_error():

    @measure_model_call("chat")
    def generate():
        raise RuntimeError("provider is down")

    with pytest.raises(RuntimeError, match="provider is down"):
        generate()

    assert (
        metrics.model_calls.value(
            provider="cohere", operation="chat", status="error"
        )
        == 1
    )

    assert spans.recent_errors.latest()[0].operation == "cohere.chat"


def test_the_wrapper_keeps_the_function_identity():

    @measure_model_call("embed")
    def create_embeddings(texts, input_type="search_document"):
        """Doc string."""
        return [[0.1]]

    assert create_embeddings.__name__ == "create_embeddings"
    assert create_embeddings.__doc__ == "Doc string."
    assert create_embeddings(["a"], input_type="search_query") == [[0.1]]


def test_the_real_services_are_instrumented():
    """The decorator has to be on the functions that cost money."""

    from app.services.embedding_services import create_embeddings
    from app.services.llm_service import generate_answer
    from app.services.structured_output_service import extract_business_rule

    for function in (generate_answer, create_embeddings, extract_business_rule):
        assert hasattr(function, "__wrapped__"), function.__name__


# ============================================================
# WORKFLOW OBSERVER
# ============================================================

def started(task_id, name):

    return {"id": task_id, "name": name, "input": {"question": "q"}}


def finished(task_id, name, result=None, interrupts=()):

    task = {"id": task_id, "name": name, "result": result or {}}

    if interrupts:
        task["interrupts"] = interrupts

    return task


def test_a_completed_node_is_counted_and_timed():

    observer = WorkflowObserver("thread1")

    observer.observe(started("1", "policy"))
    observer.observe(
        finished("1", "policy", {"policy_result": {"success": True}})
    )

    assert metrics.workflow_nodes.value(node="policy", status="success") == 1
    assert metrics.workflow_node_duration.count(node="policy") == 1

    record = spans.recent_spans.latest()[0]

    assert record.operation == "workflow.node.policy"
    assert record.attributes["thread_id"] == "thread1"


def test_a_node_that_reports_a_failure_is_counted_as_one():

    observer = WorkflowObserver("thread1")

    observer.observe(started("1", "employee"))
    observer.observe(
        finished(
            "1",
            "employee",
            {"employee_data": {"success": False, "error": "not found"}},
        )
    )

    assert metrics.workflow_nodes.value(node="employee", status="failure") == 1


def test_a_paused_node_is_counted_but_not_timed():
    """Waiting for a human is not time the system spent working."""

    observer = WorkflowObserver("thread1")

    observer.observe(started("1", "approval"))
    observer.observe(finished("1", "approval", interrupts=[{"value": "x"}]))

    assert metrics.workflow_nodes.value(node="approval", status="paused") == 1
    assert metrics.workflow_node_duration.count(node="approval") == 0


def test_plumbing_nodes_are_ignored():

    observer = WorkflowObserver("thread1")

    observer.observe(started("1", "analyze"))
    observer.observe(finished("1", "analyze"))

    assert spans.recent_spans.latest() == []


def test_a_node_that_never_finished_is_the_one_that_failed():

    observer = WorkflowObserver("thread1")

    observer.observe(started("1", "rag"))

    observer.fail_pending(RuntimeError("graph exploded"))

    assert metrics.workflow_nodes.value(node="rag", status="error") == 1

    record = spans.recent_errors.latest()[0]

    assert record.operation == "workflow.node.rag"
    assert "graph exploded" in record.error


def test_failing_pending_twice_counts_once():

    observer = WorkflowObserver("thread1")

    observer.observe(started("1", "rag"))

    observer.fail_pending(RuntimeError("boom"))
    observer.fail_pending(RuntimeError("boom"))

    assert metrics.workflow_nodes.value(node="rag", status="error") == 1


def test_a_finish_without_a_start_is_ignored():

    observer = WorkflowObserver("thread1")

    observer.observe(finished("1", "policy", {"policy_result": {"success": True}}))

    assert spans.recent_spans.latest() == []


def test_a_malformed_task_does_not_raise():
    """The observer sits in the workflow's path; it must be inert."""

    observer = WorkflowObserver("thread1")

    observer.observe({})
    observer.observe({"name": "policy"})

    assert spans.recent_spans.latest() == []


# ============================================================
# RUN OUTCOMES
# ============================================================

def test_a_run_outcome_is_counted_by_status():

    record_workflow_run("success", 1.5)
    record_workflow_run("failed", 0.5)

    assert metrics.workflow_runs.value(outcome="success") == 1
    assert metrics.workflow_runs.value(outcome="failed") == 1
    assert metrics.workflow_duration.count(operation="run") == 2


def test_a_resume_is_recorded_separately():

    record_workflow_run("success", 1.0, operation="resume")

    assert metrics.workflow_duration.count(operation="resume") == 1
    assert metrics.workflow_duration.count(operation="run") == 0


def test_the_pending_gauge_can_be_published():

    set_pending_approvals(4)

    assert metrics.approvals_pending.value() == 4
