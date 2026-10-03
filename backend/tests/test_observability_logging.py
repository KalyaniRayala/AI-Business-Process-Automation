"""
Correlation ids and log formatting.

The redaction tests matter most: a log line is the easiest way to
leak a password, and these are the only thing stopping it.
"""

import json
import logging

import pytest

from app.observability.context import (
    CorrelationFilter,
    current_context,
    current_request_id,
    current_thread_id,
    new_request_id,
    request_context,
    workflow_context,
)
from app.observability.logging_setup import (
    JsonFormatter,
    RedactingFilter,
    configure_logging,
    redact,
)


def make_record(message="hello", args=(), **extra):

    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )

    for key, value in extra.items():
        setattr(record, key, value)

    return record


# ============================================================
# CORRELATION
# ============================================================

def test_no_ids_outside_a_request():

    assert current_request_id() is None
    assert current_thread_id() is None
    assert current_context() == {}


def test_a_request_id_is_bound_for_the_block():

    with request_context("abc123") as request_id:
        assert request_id == "abc123"
        assert current_request_id() == "abc123"

    assert current_request_id() is None


def test_an_id_is_generated_when_none_is_given():

    with request_context() as request_id:
        assert request_id
        assert current_request_id() == request_id


def test_request_ids_are_unique():

    assert new_request_id() != new_request_id()


def test_nested_contexts_restore_the_outer_id():

    with request_context("outer"):

        with request_context("inner"):
            assert current_request_id() == "inner"

        assert current_request_id() == "outer"


def test_a_workflow_id_is_carried_alongside_the_request_id():

    with request_context("req1"), workflow_context("thread1"):

        assert current_context() == {"request_id": "req1", "thread_id": "thread1"}


def test_the_filter_puts_the_ids_on_the_record():

    record = make_record()

    with request_context("req1"), workflow_context("thread1"):
        CorrelationFilter().filter(record)

    assert record.request_id == "req1"
    assert record.thread_id == "thread1"


def test_the_filter_uses_a_dash_when_there_is_no_id():

    record = make_record()

    CorrelationFilter().filter(record)

    assert record.request_id == "-"
    assert record.thread_id == "-"


# ============================================================
# REDACTION
# ============================================================

@pytest.mark.parametrize(
    "text",
    [
        "postgresql+psycopg://bpa_app:hunter2@localhost:5432/bpa",
        "connecting to postgresql://user:hunter2@host/db now",
    ],
)
def test_a_password_in_a_connection_string_is_masked(text):

    masked = redact(text)

    assert "hunter2" not in masked
    assert "***" in masked


def test_the_rest_of_the_connection_string_survives():

    masked = redact("postgresql+psycopg://bpa_app:hunter2@localhost:5432/bpa")

    assert masked == "postgresql+psycopg://bpa_app:***@localhost:5432/bpa"


@pytest.mark.parametrize(
    "text",
    [
        "COHERE_API_KEY=abcd1234secret",
        "api_key: abcd1234secret",
        'password="abcd1234secret"',
        "Authorization: abcd1234secret",
        '{"token": "abcd1234secret"}',
    ],
)
def test_secret_assignments_are_masked(text):

    assert "abcd1234secret" not in redact(text)


def test_ordinary_text_is_untouched():

    text = "Workflow 7 completed in 1.2s"

    assert redact(text) == text


def test_the_filter_redacts_the_formatted_message():

    record = make_record("connecting to %s", ("postgresql://u:pw@host/db",))

    RedactingFilter().filter(record)

    assert "pw" not in record.getMessage()
    assert "***" in record.getMessage()


def test_the_filter_redacts_extra_values():

    record = make_record("done", url="postgresql://u:pw@host/db")

    RedactingFilter().filter(record)

    assert "pw" not in record.url


def test_a_broken_format_string_does_not_lose_the_record():

    record = make_record("needs %s and %s", ("only one",))

    assert RedactingFilter().filter(record) is True


# ============================================================
# JSON FORMAT
# ============================================================

def test_json_lines_carry_the_standard_fields():

    record = make_record("workflow started")

    CorrelationFilter().filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "workflow started"
    assert payload["timestamp"].endswith("+00:00")


def test_json_includes_the_ids_when_they_are_set():

    record = make_record()

    with request_context("req1"), workflow_context("thread1"):
        CorrelationFilter().filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["request_id"] == "req1"
    assert payload["thread_id"] == "thread1"


def test_json_omits_the_ids_when_there_are_none():

    record = make_record()

    CorrelationFilter().filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert "request_id" not in payload
    assert "thread_id" not in payload


def test_json_keeps_extra_fields():

    record = make_record("served", route="/health", duration_seconds=0.01)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["route"] == "/health"
    assert payload["duration_seconds"] == 0.01


def test_json_stringifies_values_it_cannot_serialise():

    record = make_record("odd", thing=object())

    payload = json.loads(JsonFormatter().format(record))

    assert isinstance(payload["thing"], str)


def test_json_includes_a_redacted_traceback():

    try:
        raise ValueError("postgresql://u:pw@host/db is wrong")

    except ValueError:
        import sys

        record = make_record("failed")
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError" in payload["exception"]
    assert "pw" not in payload["exception"]


# ============================================================
# SETUP
# ============================================================

@pytest.fixture
def root_logger():
    """Restore the root logger however the test leaves it."""

    root = logging.getLogger()

    handlers = list(root.handlers)
    level = root.level

    yield root

    root.handlers = handlers
    root.setLevel(level)


def test_configure_logging_installs_one_named_handler(root_logger):

    configure_logging(level="DEBUG")

    ours = [
        handler
        for handler in root_logger.handlers
        if handler.get_name() == "observability"
    ]

    assert len(ours) == 1
    assert root_logger.level == logging.DEBUG


def test_calling_it_twice_does_not_duplicate_the_handler(root_logger):

    configure_logging()
    configure_logging()

    ours = [
        handler
        for handler in root_logger.handlers
        if handler.get_name() == "observability"
    ]

    assert len(ours) == 1


def test_it_leaves_other_handlers_alone(root_logger):
    """pytest's capture handler must survive."""

    other = logging.NullHandler()
    other.set_name("someone-else")

    root_logger.addHandler(other)

    configure_logging()

    assert other in root_logger.handlers


def test_the_format_is_chosen_by_the_environment(root_logger, monkeypatch):

    monkeypatch.setenv("LOG_FORMAT", "json")

    configure_logging()

    handler = next(
        handler
        for handler in root_logger.handlers
        if handler.get_name() == "observability"
    )

    assert isinstance(handler.formatter, JsonFormatter)


def test_text_is_the_default_format(root_logger, monkeypatch):

    monkeypatch.delenv("LOG_FORMAT", raising=False)

    configure_logging()

    handler = next(
        handler
        for handler in root_logger.handlers
        if handler.get_name() == "observability"
    )

    assert not isinstance(handler.formatter, JsonFormatter)


def test_a_configured_logger_redacts_what_it_writes(root_logger, capsys):

    configure_logging(level="INFO", log_format="json")

    with request_context("req1"):
        logging.getLogger("test.secrets").info(
            "connecting to postgresql://u:hunter2@host/db"
        )

    line = capsys.readouterr().err.strip().splitlines()[-1]

    payload = json.loads(line)

    assert "hunter2" not in payload["message"]
    assert payload["request_id"] == "req1"
