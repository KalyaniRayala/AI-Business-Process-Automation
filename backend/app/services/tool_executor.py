import logging
from typing import Callable, Any


logger = logging.getLogger(__name__)


def execute_with_retry(
    tool: Callable,
    retries: int = 2,
    **kwargs
) -> Any:

    last_error = None

    for attempt in range(retries + 1):

        try:
            result = tool(**kwargs)

            return result

        except Exception as error:

            last_error = error

            logger.warning(
                "Tool attempt %d failed: %s",
                attempt + 1,
                error
            )

    return {
        "success": False,
        "error": str(last_error),
        "message": "Tool execution failed after retries."
    }