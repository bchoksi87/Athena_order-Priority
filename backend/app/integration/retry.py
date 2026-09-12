"""Retry with exponential backoff for connector calls (spec Phase 2, *Retry mechanisms*).

The policy is plain data so it can be configured per deployment; ``sleep`` is
injectable so tests never wait. Every failure after the last attempt is
reported as :class:`~app.core.errors.IntegrationError` carrying the attempt
count and the last underlying error.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import structlog

from app.core.errors import IntegrationError, ValidationError

log = structlog.get_logger(__name__)

T = TypeVar("T")

SleepFn = Callable[[float], None]


@dataclass(slots=True, frozen=True)
class RetryPolicy:
    """How often and how long to wait when a connector call fails."""

    attempts: int = 3
    base_delay_seconds: float = 0.5
    multiplier: float = 2.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValidationError("retry attempts must be >= 1", details={"attempts": self.attempts})
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValidationError("retry delays must be >= 0")
        if self.multiplier < 1.0:
            raise ValidationError("retry multiplier must be >= 1", details={"multiplier": self.multiplier})

    def delay_before(self, attempt: int) -> float:
        """Seconds to wait before retry number ``attempt`` (1 = first retry)."""
        raw = self.base_delay_seconds * (self.multiplier ** max(0, attempt - 1))
        return min(self.max_delay_seconds, raw)


def call_with_retry(
    operation: str,
    func: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: SleepFn = time.sleep,
    details: dict[str, Any] | None = None,
) -> T:
    """Run ``func`` up to ``policy.attempts`` times; raise ``IntegrationError`` when all fail.

    ``operation`` names the call in logs and error messages (e.g. ``"fetch_orders"``).
    Any exception counts as a failure; the last one is chained as ``__cause__``.
    """
    last_error: Exception | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt >= policy.attempts:
                break
            delay = policy.delay_before(attempt)
            log.warning(
                "retry.attempt_failed",
                operation=operation,
                attempt=attempt,
                max_attempts=policy.attempts,
                retry_in_seconds=delay,
                error=str(exc),
            )
            sleep(delay)
    assert last_error is not None  # loop always runs at least once
    log.error("retry.exhausted", operation=operation, attempts=policy.attempts, error=str(last_error))
    raise IntegrationError(
        f"{operation} failed after {policy.attempts} attempt(s): {last_error}",
        details={
            "operation": operation,
            "attempts": policy.attempts,
            "last_error": str(last_error),
            **(details or {}),
        },
    ) from last_error


__all__ = ["RetryPolicy", "SleepFn", "call_with_retry"]
