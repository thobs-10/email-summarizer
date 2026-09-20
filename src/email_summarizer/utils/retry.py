from __future__ import annotations

import time
from typing import Callable, TypeVar


T = TypeVar("T")


class RetryError(RuntimeError):
    """Raised when a retryable operation exhausts all attempts."""


def retry_with_backoff(
    operation: Callable[[], T],
    *,
    exceptions: tuple[type[BaseException], ...],
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    backoff_multiplier: float = 2.0,
    operation_name: str = "operation",
) -> T:
    """Run an operation with bounded exponential backoff.

    Args:
        operation: Callable to execute.
        exceptions: Exception types that should trigger a retry.
        max_attempts: Maximum number of attempts, including the first try.
        base_delay_seconds: Initial delay before the second attempt.
        backoff_multiplier: Delay growth factor between attempts.
        operation_name: Human-readable operation name for error context.

    Raises:
        RetryError: If all attempts fail with retryable exceptions.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    delay = base_delay_seconds
    last_error: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except exceptions as error:
            last_error = error
            if attempt == max_attempts:
                break
            time.sleep(delay)
            delay *= backoff_multiplier

    raise RetryError(
        f"{operation_name} failed after {max_attempts} attempts"
    ) from last_error
