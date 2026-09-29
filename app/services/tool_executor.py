from typing import Callable, Any


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

            print(
                f"Tool attempt {attempt + 1} failed: {error}"
            )

    return {
        "success": False,
        "error": str(last_error),
        "message": "Tool execution failed after retries."
    }