"""
Correlation ids.

One request, one id. The id is set by the HTTP middleware, carried
in a context variable, attached to every log record written while
that request is handled, and returned in the `X-Request-ID` header
so a user can quote it in a bug report.

Workflow runs carry their `thread_id` the same way, so the logs of
a background node and the request that started it can be lined up.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4


REQUEST_ID_HEADER = "X-Request-ID"

# Context variables survive across awaits and are per-task, so two
# concurrent requests never see each other's ids.
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_thread_id: ContextVar[str | None] = ContextVar("thread_id", default=None)


def new_request_id() -> str:

    return uuid4().hex


def current_request_id() -> str | None:

    return _request_id.get()


def current_thread_id() -> str | None:

    return _thread_id.get()


def current_context() -> dict[str, str]:
    """The correlation fields that are set, for logs and responses."""

    context = {}

    request_id = _request_id.get()
    thread_id = _thread_id.get()

    if request_id:
        context["request_id"] = request_id

    if thread_id:
        context["thread_id"] = thread_id

    return context


@contextmanager
def request_context(request_id: str | None = None):
    """Bind a request id for the duration of the block."""

    token = _request_id.set(request_id or new_request_id())

    try:
        yield _request_id.get()

    finally:
        _request_id.reset(token)


@contextmanager
def workflow_context(thread_id: str | None):
    """Bind a workflow thread id for the duration of the block."""

    token = _thread_id.set(thread_id)

    try:
        yield thread_id

    finally:
        _thread_id.reset(token)


class CorrelationFilter(logging.Filter):
    """
    Puts the current ids on every log record, so a formatter can
    print them without each call site passing them.
    """

    def filter(self, record: logging.LogRecord) -> bool:

        context = current_context()

        record.request_id = context.get("request_id", "-")
        record.thread_id = context.get("thread_id", "-")

        return True
