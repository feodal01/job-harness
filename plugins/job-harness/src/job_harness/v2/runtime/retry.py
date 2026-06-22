"""Source-attempt retry policy."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import RetryNextAction, SourceOutcome

DEFAULT_RETRYABLE_OUTCOMES: frozenset[SourceOutcome] = frozenset(
    {
        SourceOutcome.SOURCE_TIMEOUT,
        SourceOutcome.NETWORK_ERROR,
        SourceOutcome.RATE_LIMITED,
        SourceOutcome.HTTP_SERVER_ERROR,
        SourceOutcome.RESOURCE_FAILURE,
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    retryable_outcomes: frozenset[SourceOutcome] = DEFAULT_RETRYABLE_OUTCOMES
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")

    def next_action(
        self,
        *,
        outcome: SourceOutcome,
        attempt: int,
        raw_listings_written: int,
    ) -> RetryNextAction:
        if raw_listings_written > 0:
            return RetryNextAction.NONE
        if attempt >= self.max_attempts:
            return RetryNextAction.NONE
        if outcome not in self.retryable_outcomes:
            return RetryNextAction.NONE
        return RetryNextAction.RETRY
