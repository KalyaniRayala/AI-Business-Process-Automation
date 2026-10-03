"""
Connector configuration, read from environment variables (.env).

Secrets come only from the environment: they are never defaulted,
never part of a dataclass repr, and never included in error
messages (errors name the missing variables, not their values).
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

from dotenv import load_dotenv

from app.services.connectors.base import ConnectorConfigError


load_dotenv()


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,99}$")

LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def environment(env: Mapping | None) -> Mapping:

    return os.environ if env is None else env


def clean_url(name: str, value: str) -> str:
    """https only (plain http just for a local mock server)."""

    parsed = urlparse(value.strip())

    local_http = parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS

    if (parsed.scheme != "https" and not local_http) or not parsed.hostname:
        raise ConnectorConfigError(f"{name} must be an https URL.")

    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConnectorConfigError(
            f"{name} must not contain credentials, a query or a fragment."
        )

    return value.strip().rstrip("/")


def bounded_number(env: Mapping, name: str, default, kind, low, high):

    raw = (env.get(name) or "").strip()

    if not raw:
        return default

    try:
        value = kind(raw)
    except ValueError:
        raise ConnectorConfigError(f"{name} must be a number.") from None

    if not low <= value <= high:
        raise ConnectorConfigError(f"{name} must be between {low} and {high}.")

    return value


# ============================================================
# HTTP BEHAVIOUR (shared by every connector)
# ============================================================

@dataclass(frozen=True)
class HttpSettings:

    timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30.0
    max_pages: int = 100

    @classmethod
    def from_env(cls, env: Mapping | None = None) -> "HttpSettings":

        env = environment(env)

        return cls(
            timeout_seconds=bounded_number(
                env, "CONNECTOR_TIMEOUT_SECONDS", 15.0, float, 1, 120
            ),
            max_retries=bounded_number(
                env, "CONNECTOR_MAX_RETRIES", 3, int, 0, 10
            ),
            max_pages=bounded_number(
                env, "CONNECTOR_MAX_PAGES", 100, int, 1, 1000
            ),
        )


# ============================================================
# MICROSOFT ENTRA ID
# ============================================================

@dataclass(frozen=True)
class EntraSettings:

    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)

    authority_url: str = "https://login.microsoftonline.com"
    graph_url: str = "https://graph.microsoft.com/v1.0"

    REQUIRED = ("ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_CLIENT_SECRET")

    @classmethod
    def from_env(cls, env: Mapping | None = None) -> "EntraSettings":

        env = environment(env)

        missing = [
            name for name in cls.REQUIRED
            if not (env.get(name) or "").strip()
        ]

        if missing:
            raise ConnectorConfigError(
                "Entra connector is not configured; missing: "
                + ", ".join(missing)
            )

        tenant_id = env["ENTRA_TENANT_ID"].strip()
        client_id = env["ENTRA_CLIENT_ID"].strip()

        # Used in a URL path and a form field.
        for name, value in (
            ("ENTRA_TENANT_ID", tenant_id), ("ENTRA_CLIENT_ID", client_id)
        ):
            if not IDENTIFIER.match(value):
                raise ConnectorConfigError(f"{name} has an invalid format.")

        return cls(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=env["ENTRA_CLIENT_SECRET"].strip(),
            authority_url=clean_url(
                "ENTRA_AUTHORITY_URL",
                env.get("ENTRA_AUTHORITY_URL") or cls.authority_url,
            ),
            graph_url=clean_url(
                "ENTRA_GRAPH_URL",
                env.get("ENTRA_GRAPH_URL") or cls.graph_url,
            ),
        )
