"""Pure request-level retry decisions for one logical page."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import TypeVar

from job_harness.v2.ports import RetrySafety

_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_T = TypeVar("_T")


class RequestFailureKind(StrEnum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    HTTP_STATUS = "http_status"


class RequestRetryDisposition(StrEnum):
    SCHEDULE = "schedule"
    EXHAUSTED = "exhausted"
    TERMINAL = "terminal"


class RequestAttemptError(Exception):
    def __init__(
        self,
        *,
        failure_kind: RequestFailureKind,
        retry_safety: RetrySafety,
        message: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.failure_kind = failure_kind
        self.retry_safety = retry_safety
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


@dataclass(frozen=True)
class RequestRetryDecision:
    disposition: RequestRetryDisposition
    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds must be >= 0")
        if self.disposition != RequestRetryDisposition.SCHEDULE and self.delay_seconds != 0:
            raise ValueError("only scheduled retries may have a delay")


@dataclass(frozen=True)
class RequestRetryPolicy:
    max_attempts: int = 3
    attempt_timeout_seconds: float = 15.0
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    request_budget_seconds: float = 55.0
    random_fraction: Callable[[], float] = field(default=random.random, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be > 0")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be >= 0")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if self.request_budget_seconds < self.attempt_timeout_seconds:
            raise ValueError("request_budget_seconds must cover one attempt")

    def decide(
        self,
        *,
        retry_safety: RetrySafety,
        failure_kind: RequestFailureKind,
        attempt_number: int,
        elapsed_seconds: float,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> RequestRetryDecision:
        if attempt_number < 1:
            raise ValueError("attempt_number must be >= 1")
        if elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be >= 0")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be >= 0")
        if retry_safety != RetrySafety.SAFE or not _is_retryable(failure_kind, status_code):
            return RequestRetryDecision(RequestRetryDisposition.TERMINAL)
        if attempt_number >= self.max_attempts:
            return RequestRetryDecision(RequestRetryDisposition.EXHAUSTED)

        fraction = self.random_fraction()
        if not 0 <= fraction <= 1:
            raise ValueError("random_fraction must return a value in [0, 1]")
        ceiling = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** (attempt_number - 1)),
        )
        delay = ceiling * fraction
        if retry_after_seconds is not None:
            delay = max(delay, retry_after_seconds)
        if elapsed_seconds + delay + self.attempt_timeout_seconds > self.request_budget_seconds:
            return RequestRetryDecision(RequestRetryDisposition.EXHAUSTED)
        return RequestRetryDecision(RequestRetryDisposition.SCHEDULE, delay)


class InMemoryRequestRetrier:
    def __init__(
        self,
        *,
        policy: RequestRetryPolicy,
        sleep: Callable[[float], Awaitable[None]],
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._policy = policy
        self._sleep = sleep
        self._clock = clock

    async def run(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        started_at = self._clock()
        attempt_number = 0
        while True:
            attempt_number += 1
            try:
                return await operation()
            except RequestAttemptError as exc:
                decision = self._policy.decide(
                    retry_safety=exc.retry_safety,
                    failure_kind=exc.failure_kind,
                    attempt_number=attempt_number,
                    elapsed_seconds=max(0.0, self._clock() - started_at),
                    status_code=exc.status_code,
                    retry_after_seconds=exc.retry_after_seconds,
                )
                if decision.disposition != RequestRetryDisposition.SCHEDULE:
                    raise
                await self._sleep(max(decision.delay_seconds, 0.001))


def _is_retryable(failure_kind: RequestFailureKind, status_code: int | None) -> bool:
    if failure_kind in {RequestFailureKind.TIMEOUT, RequestFailureKind.NETWORK}:
        return True
    return failure_kind == RequestFailureKind.HTTP_STATUS and status_code in _RETRYABLE_STATUS_CODES


def is_retryable_http_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES
