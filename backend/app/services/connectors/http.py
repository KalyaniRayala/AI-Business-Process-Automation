"""
HTTP access for connectors: retries, back-off and safe errors.

- Transport errors, timeouts and HTTP 408/429/5xx are retried with
  exponential back-off and jitter (Retry-After is honoured, capped).
  Other 4xx responses are returned to the caller, never retried.
- Redirects are not followed, so credentials cannot be forwarded to
  another host.
- Error messages carry the status code or exception class only: no
  URLs with query strings, headers, bodies or credentials.
"""

import logging
import random
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.services.connectors.base import (
    ConnectorAuthError,
    ConnectorRequestError,
)
from app.services.connectors.settings import HttpSettings


logger = logging.getLogger(__name__)


RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Retry-After as seconds (numeric or HTTP-date), if present."""

    value = response.headers.get("Retry-After")

    if not value:
        return None

    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class ConnectorHttpClient:

    def __init__(
        self,
        settings: HttpSettings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ):

        self.settings = settings or HttpSettings()
        self._sleep = sleep
        self._jitter = jitter

        self._client = httpx.Client(
            transport=transport,
            timeout=self.settings.timeout_seconds,
            follow_redirects=False,
        )

    def close(self) -> None:

        self._client.close()

    def __enter__(self):

        return self

    def __exit__(self, *exc_info):

        self.close()

    # --------------------------------------------------------
    # Requests
    # --------------------------------------------------------

    def send(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Send a request, retrying transient failures. Returns the
        response for any status that should not be retried (the
        caller decides what a 401, 403 or 404 means).
        """

        settings = self.settings
        attempt = 0

        while True:

            retry_after = None

            try:

                response = self._client.request(method, url, **kwargs)

            except httpx.HTTPError as error:

                # Class name only: exception text can include the URL.
                failure = type(error).__name__

            else:

                if response.status_code not in RETRYABLE_STATUS:
                    return response

                failure = f"HTTP {response.status_code}"
                retry_after = retry_after_seconds(response)

            if attempt >= settings.max_retries:

                raise ConnectorRequestError(
                    f"Request failed after {attempt + 1} attempt(s): {failure}."
                )

            if retry_after is not None:
                delay = retry_after
            else:
                delay = settings.backoff_seconds * (2 ** attempt)
                delay *= 0.5 + 0.5 * self._jitter()

            delay = min(delay, settings.max_backoff_seconds)

            logger.warning(
                "Connector request failed (%s); retrying in %.1fs "
                "(attempt %d of %d)",
                failure, delay, attempt + 1, settings.max_retries
            )

            self._sleep(delay)

            attempt += 1


# ============================================================
# OAUTH 2.0 CLIENT CREDENTIALS
# ============================================================

class ClientCredentialsAuth:
    """
    Access tokens for an application identity. The token is cached
    in memory until shortly before it expires and is never logged
    or written anywhere.
    """

    EXPIRY_MARGIN_SECONDS = 60

    def __init__(
        self,
        http: ConnectorHttpClient,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str,
        clock: Callable[[], float] = time.monotonic,
    ):

        self._http = http
        self._token_url = token_url
        self._form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }
        self._clock = clock

        self._token: str | None = None
        self._expires_at = 0.0

    def invalidate(self) -> None:

        self._token = None

    def token(self) -> str:

        if self._token and self._clock() < self._expires_at:
            return self._token

        response = self._http.send(
            "POST", self._token_url, data=self._form
        )

        if response.status_code != 200:

            raise ConnectorAuthError(
                "Token request was rejected "
                f"(HTTP {response.status_code}"
                f"{self._error_code(response)}). "
                "Check the client id, client secret and tenant."
            )

        try:

            body = response.json()
            token = body["access_token"]
            lifetime = float(body.get("expires_in", 3600))

        except (ValueError, KeyError, TypeError, AttributeError):

            raise ConnectorAuthError(
                "Token response did not contain an access token."
            ) from None

        if not isinstance(token, str) or not token:

            raise ConnectorAuthError(
                "Token response did not contain an access token."
            )

        self._token = token
        self._expires_at = (
            self._clock() + max(0.0, lifetime - self.EXPIRY_MARGIN_SECONDS)
        )

        return token

    @staticmethod
    def _error_code(response: httpx.Response) -> str:
        """The OAuth 'error' code (e.g. invalid_client), if simple."""

        try:
            code = response.json().get("error")
        except (ValueError, AttributeError):
            return ""

        if isinstance(code, str) and code.replace("_", "").isalnum():
            return f", {code[:50]}"

        return ""
