"""
HTTP instrumentation.

One middleware gives every request a correlation id, times it,
counts it, logs the result and returns the id in a header. It
observes only: a request that would have succeeded still succeeds,
and a request that fails fails the same way, with the error counted
on its way out.
"""

from __future__ import annotations

import logging
import time

from starlette.requests import Request

from app.observability import metrics
from app.observability.context import (
    REQUEST_ID_HEADER,
    current_request_id,
    new_request_id,
    request_context,
)
from app.observability.spans import record_error


logger = logging.getLogger(__name__)


# A client-supplied id is echoed back so a caller can correlate
# across services, but only if it is short and plain: it ends up in
# logs and in a response header.
MAX_CLIENT_REQUEST_ID = 64


def clean_request_id(value: str | None) -> str:

    if not value:
        return new_request_id()

    candidate = value.strip()

    if not candidate or len(candidate) > MAX_CLIENT_REQUEST_ID:
        return new_request_id()

    if not all(character.isalnum() or character in "-_." for character in candidate):
        return new_request_id()

    return candidate


def route_template(request: Request) -> str:
    """
    The route pattern, such as `/workflows/{thread_id}`.

    Never the raw path: ids in a metric label would create a new
    time series per workflow.
    """

    route = request.scope.get("route")

    path = getattr(route, "path", None)

    return path or "unmatched"


async def observability_middleware(request: Request, call_next):

    request_id = clean_request_id(request.headers.get(REQUEST_ID_HEADER))

    started = time.perf_counter()

    with request_context(request_id):

        method = request.method

        metrics.http_in_progress.inc()

        try:

            response = await call_next(request)

        except Exception as error:

            duration = time.perf_counter() - started

            record_error(kind=type(error).__name__, where="http")

            metrics.http_requests.inc(
                method=method, route=route_template(request), status="500"
            )
            metrics.http_duration.observe(
                duration, method=method, route=route_template(request)
            )

            logger.exception(
                "%s %s failed after %.3fs",
                method,
                request.url.path,
                duration,
                extra={"method": method, "path": request.url.path},
            )

            raise

        finally:
            metrics.http_in_progress.dec()

        duration = time.perf_counter() - started
        route = route_template(request)
        status = str(response.status_code)

        metrics.http_requests.inc(method=method, route=route, status=status)
        metrics.http_duration.observe(duration, method=method, route=route)

        if response.status_code >= 500:
            record_error(kind=f"http_{status}", where=route)

        response.headers[REQUEST_ID_HEADER] = current_request_id() or request_id

        logger.info(
            "%s %s -> %s in %.3fs",
            method,
            request.url.path,
            status,
            duration,
            extra={
                "method": method,
                "route": route,
                "status": response.status_code,
                "duration_seconds": round(duration, 6),
            },
        )

        return response
