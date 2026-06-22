"""Engine-level runtime policy for source attempts."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v1.types import FailureMode


@dataclass(frozen=True)
class SourceRuntimeConfig:
    total_run_timeout_ms: int = 90_000
    source_attempt_timeout_ms: int = 30_000
    company_probe_timeout_ms: int = 8_000
    source_max_attempts: int = 2
    source_retry_initial_backoff_ms: int = 500
    source_retry_backoff_multiplier: float = 2.0
    source_retry_max_backoff_ms: int = 2_000

    def __post_init__(self) -> None:
        for field_name in (
            "total_run_timeout_ms",
            "source_attempt_timeout_ms",
            "company_probe_timeout_ms",
            "source_max_attempts",
            "source_retry_initial_backoff_ms",
            "source_retry_max_backoff_ms",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be >= 1")
        if self.source_retry_backoff_multiplier < 1.0:
            raise ValueError("source_retry_backoff_multiplier must be >= 1.0")

    def retry_backoff_ms(self, retry_number: int, remaining_total_ms: int) -> int:
        if retry_number < 1:
            raise ValueError("retry_number must be >= 1")
        if remaining_total_ms <= 100:
            return 0
        raw = int(
            self.source_retry_initial_backoff_ms
            * (self.source_retry_backoff_multiplier ** (retry_number - 1))
        )
        return min(raw, self.source_retry_max_backoff_ms, remaining_total_ms - 100)


SOURCE_LEVEL_RETRYABLE_FAILURES: frozenset[FailureMode] = frozenset(
    {
        FailureMode.NETWORK_ERROR,
        FailureMode.HTTP_TIMEOUT,
        FailureMode.GOTO_TIMEOUT,
        FailureMode.POOL_ACQUIRE_TIMEOUT,
        FailureMode.HTTP_429,
        FailureMode.HTTP_503_RETRY_AFTER,
    }
)
