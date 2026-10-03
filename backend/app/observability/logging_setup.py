"""
Log configuration.

Two formats, chosen with `LOG_FORMAT`:

- `text` (default): readable in a terminal, with the correlation
  ids appended,
- `json`: one JSON object per line, for a log collector.

Both pass every record through the same redaction, because a log
line is the easiest way to leak a password or an API key: a
connection string with credentials in it is a single `%s` away.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from app.observability.context import CorrelationFilter


DEFAULT_LEVEL = "INFO"
DEFAULT_FORMAT = "text"

TEXT_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[req=%(request_id)s thread=%(thread_id)s]: %(message)s"
)

# Fields logging puts on every record; anything else came from the
# caller's `extra=` and belongs in the JSON output.
STANDARD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "message", "request_id", "thread_id", "taskName"}


# ============================================================
# REDACTION
# ============================================================

# postgresql+psycopg://user:secret@host/db -> ...://user:***@host/db
CREDENTIALS_IN_URL = re.compile(r"(?P<scheme>\w[\w+.-]*://[^\s:/@]+:)[^\s@]+@")

# key=value / "key": "value" for anything that sounds like a secret.
# The key may be prefixed, so COHERE_API_KEY matches as well as
# api_key; the keyword has to sit right before the separator, so
# `tokenizer = x` does not match.
SECRET_ASSIGNMENT = re.compile(
    r"(?i)([A-Za-z0-9_.-]*"
    r"(?:api[_-]?key|secret|password|passwd|token|authorization))"
    r"(\"?\s*[:=]\s*\"?)([^\s,;\"'}]+)"
)

REDACTED = "***"


def redact(text: str) -> str:
    """Mask credentials in a string that is about to be logged."""

    masked = CREDENTIALS_IN_URL.sub(rf"\g<scheme>{REDACTED}@", text)

    return SECRET_ASSIGNMENT.sub(rf"\1\2{REDACTED}", masked)


class RedactingFilter(logging.Filter):
    """
    Redacts the formatted message and any string `extra` values.

    It runs as a filter rather than inside a formatter so it applies
    to both formats and to handlers added later.
    """

    def filter(self, record: logging.LogRecord) -> bool:

        try:
            message = record.getMessage()

        except Exception:
            # A broken format string is the caller's problem; do not
            # lose the record over it.
            return True

        cleaned = redact(message)

        if cleaned != message:
            record.msg = cleaned
            record.args = ()

        for key, value in list(record.__dict__.items()):

            if key not in STANDARD_FIELDS and isinstance(value, str):
                record.__dict__[key] = redact(value)

        return True


# ============================================================
# FORMATTERS
# ============================================================

class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:

        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in ("request_id", "thread_id"):

            value = getattr(record, key, None)

            if value and value != "-":
                payload[key] = value

        # Anything the caller passed as extra=.
        for key, value in record.__dict__.items():

            if key not in STANDARD_FIELDS:
                payload[key] = coerce(value)

        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, default=str)


def coerce(value):
    """Keep JSON-friendly values as they are; stringify the rest."""

    if isinstance(value, (str, int, float, bool, type(None))):
        return value

    if isinstance(value, (list, tuple)):
        return [coerce(item) for item in value]

    if isinstance(value, dict):
        return {str(key): coerce(item) for key, item in value.items()}

    return str(value)


# ============================================================
# SETUP
# ============================================================

def configure_logging(
    level: str | None = None,
    log_format: str | None = None,
) -> None:
    """
    Install the handler, filters and formatter on the root logger.

    Safe to call more than once: it replaces the handler it owns
    instead of stacking another one, so repeated calls (tests, a
    reloading dev server) do not duplicate every line. Handlers
    installed by anything else, such as pytest's log capture, are
    left alone.
    """

    level = (level or os.getenv("LOG_LEVEL", DEFAULT_LEVEL)).upper()
    log_format = (log_format or os.getenv("LOG_FORMAT", DEFAULT_FORMAT)).lower()

    handler = logging.StreamHandler()
    handler.set_name("observability")

    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    handler.addFilter(CorrelationFilter())
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()

    for existing in list(root.handlers):

        if existing.get_name() == "observability":
            root.removeHandler(existing)

    root.addHandler(handler)
    root.setLevel(level)
