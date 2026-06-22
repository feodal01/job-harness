"""Async dispatcher for HTTP scrapers.

Wraps the blocking `scraper.search(params)` call in `asyncio.to_thread`
so the engine awaits it from the asyncio loop. Maintains a shared
cool-down counter: after `cooldown_threshold` distinct sources hit
`NetworkError` inside `cooldown_window_s`, remaining HTTP sources are
short-circuited to `GLOBAL_NETWORK_OUTAGE` for the rest of the run.

Cancellation semantics (verified empirically):

* `asyncio.CancelledError` raised in the awaiting coroutine cancels
  the await, but the underlying blocking thread keeps running until
  `urlopen(timeout=...)` returns. The runner does not wait on the
  abandoned thread; the engine treats the source as `cancelled`.

* The engine's per-source budget must be set so a thread cannot leak
  for longer than the budget plus one HTTP attempt timeout
  (currently ≤ 10 s — see http_common.MAX_ATTEMPT_TIMEOUT_S).
"""

from __future__ import annotations

import asyncio
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from job_harness.v1.models import RawListing, SearchParams
from job_harness.v1.scrapers.http_common import (
    AntiBotBlocked,
    FetchError,
    HttpClientError,
    HttpServerError,
    LoginRequired,
    NetworkError,
    ParseError,
    RateLimited,
)
from job_harness.v1.types import FailureMode, SourceGroup, SourceState, SourceStatus

# ---------------------------------------------------------------------------
# Source outcome — what the runner returns to the engine for one source
# ---------------------------------------------------------------------------


@dataclass
class SourceOutcome:
    """Final, journaled state of one source after the runner is done."""

    status: SourceStatus
    listings: list[RawListing] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class HttpRunner:
    """Owns the cool-down counter and dispatches blocking scrapers.

    Constructed once per run by the engine. Not safe for concurrent
    reuse across runs; each run gets its own instance.
    """

    def __init__(
        self,
        *,
        max_workers: int = 8,
        cooldown_threshold: int = 4,
        cooldown_window_s: float = 10.0,
    ) -> None:
        self._cooldown_threshold = cooldown_threshold
        self._cooldown_window_s = cooldown_window_s
        self._lock = asyncio.Lock()
        self._recent_network_failures: list[tuple[float, str]] = []
        self._global_outage = False
        # Dedicated executor so we can shutdown(wait=False) and leave any
        # abandoned blocking threads to terminate on their own without
        # blocking the event loop's default executor at process exit.
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="http-runner"
        )

    @property
    def global_outage(self) -> bool:
        return self._global_outage

    def shutdown(self) -> None:
        """Stop accepting new work. Abandoned threads finish in the
        background and the process can still exit cleanly."""
        self._executor.shutdown(wait=False)

    async def run_source(
        self,
        scraper: Any,
        params: SearchParams,
        *,
        deadline_ms: int,
    ) -> SourceOutcome:
        """Dispatch one scraper call.

        The scraper is expected to follow the current `BaseScraper`
        contract: `scraper.search(params)` returns `list[RawListing]`.
        The scraper's per-call timeout has already been wired through
        the legacy `timeout_ms` constructor arg; the runner only needs
        to enforce its own wall-clock guard via the executor.
        """
        if self._global_outage:
            return self._short_circuit_outage(scraper)

        started = time.monotonic()
        loop = asyncio.get_running_loop()

        def _call() -> list[RawListing]:
            return scraper.search(params)

        # The to_thread-equivalent awaitable is cancellable; the
        # underlying thread keeps running until urlopen returns, but we
        # don't wait on it. We use our own executor (not the loop
        # default) so a hung thread does not block process teardown.
        listings: list[RawListing] = []
        exc: BaseException | None = None
        try:
            fut = loop.run_in_executor(self._executor, _call)
            done, _pending = await asyncio.wait(
                {fut},
                timeout=max(0.001, deadline_ms / 1000.0),
            )
            if fut in done:
                listings = fut.result()
            else:
                fut.cancel()
                exc = TimeoutError()
        except TimeoutError as e:
            exc = e
        except asyncio.CancelledError:
            raise
        except Exception as e:
            exc = e

        duration_ms = int((time.monotonic() - started) * 1000)
        state, failure_mode, error_class, error_message, anti_bot_signal = self._classify(exc)

        # Feed the cooldown counter when the failure was network-ish.
        if failure_mode == FailureMode.NETWORK_ERROR:
            await self._record_network_failure(scraper.name)

        # If the scraper itself reported partial/timeout state (legacy
        # base class), reflect it instead of OK.
        if state == SourceState.OK and getattr(scraper, "timed_out", False):
            if listings:
                state, failure_mode = SourceState.PARTIAL, FailureMode.SLOW_PAGINATION
            else:
                state, failure_mode = SourceState.TIMEOUT, FailureMode.HTTP_TIMEOUT

        status = SourceStatus(
            source=scraper.name,
            group=getattr(scraper, "source_group", SourceGroup.OTHER),
            state=state,
            failure_mode=failure_mode,
            source_limit=getattr(scraper, "source_limit", max(len(listings), 1)),
            deadline_ms=deadline_ms,
            elapsed_ms=duration_ms,
            supported_server_criteria=tuple(getattr(scraper, "server_criteria", ())),
            listings_written=len(listings),
            error=anti_bot_signal or error_message or error_class,
        )
        return SourceOutcome(status=status, listings=listings)

    # --- internal -------------------------------------------------------

    def _classify(
        self, exc: BaseException | None
    ) -> tuple[SourceState, FailureMode | None, str | None, str | None, str | None]:
        if exc is None:
            return SourceState.OK, None, None, None, None
        cls_name = type(exc).__name__
        msg = str(exc)

        if isinstance(exc, asyncio.TimeoutError):
            return SourceState.TIMEOUT, FailureMode.HTTP_TIMEOUT, cls_name, msg, None
        if isinstance(exc, AntiBotBlocked):
            marker = exc.marker or "anti-bot marker matched"
            return SourceState.BLOCKED, FailureMode.ANTI_BOT_PAGE, cls_name, msg, marker
        if isinstance(exc, LoginRequired):
            return (
                SourceState.BLOCKED,
                FailureMode.LOGIN_REDIRECT,
                cls_name,
                msg,
                exc.final_url,
            )
        if isinstance(exc, RateLimited):
            mode = (
                FailureMode.HTTP_429
                if exc.status_code == 429
                else FailureMode.HTTP_503_RETRY_AFTER
            )
            return SourceState.RATE_LIMITED, mode, cls_name, msg, None
        if isinstance(exc, HttpServerError):
            return SourceState.ERROR, FailureMode.HTTP_5XX, cls_name, msg, None
        if isinstance(exc, HttpClientError):
            return SourceState.ERROR, FailureMode.HTTP_4XX, cls_name, msg, None
        if isinstance(exc, ParseError):
            return SourceState.ERROR, FailureMode.PARSE_ERROR, cls_name, msg, None
        if isinstance(exc, NetworkError):
            return SourceState.ERROR, FailureMode.NETWORK_ERROR, cls_name, msg, None
        if isinstance(exc, FetchError):
            return SourceState.ERROR, FailureMode.NETWORK_ERROR, cls_name, msg, None
        # Generic exception from the scraper — most likely a parse bug.
        traceback_msg = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        return SourceState.ERROR, FailureMode.PARSE_ERROR, cls_name, traceback_msg, None

    async def _record_network_failure(self, source: str) -> None:
        now = time.monotonic()
        async with self._lock:
            self._recent_network_failures = [
                (t, s)
                for (t, s) in self._recent_network_failures
                if now - t <= self._cooldown_window_s
            ]
            # Count distinct sources, not raw events.
            sources = {s for _, s in self._recent_network_failures}
            sources.add(source)
            self._recent_network_failures.append((now, source))
            if len(sources) >= self._cooldown_threshold:
                self._global_outage = True

    async def note_success(self) -> None:
        """The engine calls this after any successful source to reset cooldown.

        Optional — keeping it lazy is fine because the window is short.
        """
        async with self._lock:
            self._global_outage = False
            self._recent_network_failures = []

    def _short_circuit_outage(self, scraper: Any) -> SourceOutcome:
        status = SourceStatus(
            source=scraper.name,
            group=getattr(scraper, "source_group", SourceGroup.OTHER),
            state=SourceState.ERROR,
            failure_mode=FailureMode.GLOBAL_NETWORK_OUTAGE,
            source_limit=getattr(scraper, "source_limit", 1),
            deadline_ms=1,
            elapsed_ms=0,
            supported_server_criteria=tuple(getattr(scraper, "server_criteria", ())),
            error="HTTP runner short-circuited after repeated network failures",
        )
        return SourceOutcome(status=status, listings=[])
