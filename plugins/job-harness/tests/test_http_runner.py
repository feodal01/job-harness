"""Tests for HttpRunner — async dispatch + cooldown + classification."""

from __future__ import annotations

import asyncio
import time
import unittest

from job_harness.http_runner import HttpRunner, SourceOutcome
from job_harness.models import JobListing, SearchParams
from job_harness.scrapers.http_common import (
    AntiBotBlocked,
    HttpClientError,
    HttpServerError,
    LoginRequired,
    NetworkError,
    ParseError,
    RateLimited,
)
from job_harness.types import (
    FailureMode,
    FilterSupport,
    SourceState,
    Transport,
)


class _FakeScraper:
    """Mimics the BaseScraper surface that HttpRunner needs."""

    def __init__(
        self,
        name: str,
        *,
        return_value: list | None = None,
        raises: Exception | None = None,
        sleep_s: float = 0.0,
        timed_out: bool = False,
        capabilities: dict | None = None,
    ):
        self.name = name
        self.display_name = name
        self._ret = return_value or []
        self._raises = raises
        self._sleep_s = sleep_s
        self.timed_out = timed_out
        self.capabilities = capabilities or {}

    def search(self, params: SearchParams) -> list[JobListing]:
        if self._sleep_s:
            time.sleep(self._sleep_s)
        if self._raises is not None:
            raise self._raises
        return self._ret


class HappyPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_ok_outcome_reflects_listings_and_capabilities(self):
        runner = HttpRunner()
        s = _FakeScraper(
            "hh_ru",
            return_value=[JobListing(title="QA", url="https://x", company="Acme")],
            capabilities={"remote_only": FilterSupport.SERVER},
        )
        outcome = await runner.run_source(s, SearchParams(query="QA"), deadline_ms=1000)
        self.assertIsInstance(outcome, SourceOutcome)
        self.assertEqual(outcome.status.state, SourceState.OK)
        self.assertIsNone(outcome.status.failure_mode)
        self.assertEqual(outcome.status.raw_count, 1)
        self.assertEqual(outcome.status.transport, Transport.HTTP)
        self.assertEqual(outcome.status.flag_enforcement["remote_only"], FilterSupport.SERVER)


class ClassificationTest(unittest.IsolatedAsyncioTestCase):
    async def _run_with(self, exc: Exception, **scraper_kwargs) -> SourceOutcome:
        runner = HttpRunner()
        s = _FakeScraper("src", raises=exc, **scraper_kwargs)
        return await runner.run_source(s, SearchParams(query="x"), deadline_ms=500)

    async def test_anti_bot_blocked(self):
        o = await self._run_with(AntiBotBlocked("blocked", marker="cf-chl"))
        self.assertEqual(o.status.state, SourceState.BLOCKED)
        self.assertEqual(o.status.failure_mode, FailureMode.ANTI_BOT_PAGE)
        self.assertEqual(o.status.anti_bot_signal, "cf-chl")

    async def test_login_required(self):
        o = await self._run_with(LoginRequired("login", final_url="https://x/login"))
        self.assertEqual(o.status.state, SourceState.BLOCKED)
        self.assertEqual(o.status.failure_mode, FailureMode.LOGIN_REDIRECT)
        self.assertEqual(o.status.anti_bot_signal, "https://x/login")

    async def test_rate_limited_429(self):
        o = await self._run_with(RateLimited("limited", retry_after_s=60.0, status_code=429))
        self.assertEqual(o.status.state, SourceState.RATE_LIMITED)
        self.assertEqual(o.status.failure_mode, FailureMode.HTTP_429)

    async def test_rate_limited_503(self):
        o = await self._run_with(RateLimited("limited", retry_after_s=60.0, status_code=503))
        self.assertEqual(o.status.state, SourceState.RATE_LIMITED)
        self.assertEqual(o.status.failure_mode, FailureMode.HTTP_503_RETRY_AFTER)

    async def test_http_5xx(self):
        o = await self._run_with(HttpServerError("500"))
        self.assertEqual(o.status.state, SourceState.ERROR)
        self.assertEqual(o.status.failure_mode, FailureMode.HTTP_5XX)

    async def test_http_4xx(self):
        o = await self._run_with(HttpClientError("404"))
        self.assertEqual(o.status.state, SourceState.ERROR)
        self.assertEqual(o.status.failure_mode, FailureMode.HTTP_4XX)

    async def test_parse_error(self):
        o = await self._run_with(ParseError("bad json"))
        self.assertEqual(o.status.state, SourceState.ERROR)
        self.assertEqual(o.status.failure_mode, FailureMode.PARSE_ERROR)

    async def test_network_error_records_failure(self):
        runner = HttpRunner(cooldown_threshold=2, cooldown_window_s=5)
        s = _FakeScraper("hirehi", raises=NetworkError("dns"))
        o = await runner.run_source(s, SearchParams(query="x"), deadline_ms=500)
        self.assertEqual(o.status.state, SourceState.ERROR)
        self.assertEqual(o.status.failure_mode, FailureMode.NETWORK_ERROR)
        self.assertFalse(runner.global_outage)

    async def test_generic_exception_classified_as_parse_error(self):
        o = await self._run_with(RuntimeError("unexpected"))
        self.assertEqual(o.status.state, SourceState.ERROR)
        self.assertEqual(o.status.failure_mode, FailureMode.PARSE_ERROR)


class TimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_exceeded_marks_http_timeout(self):
        runner = HttpRunner()
        s = _FakeScraper("slow", sleep_s=2.0)
        t0 = time.monotonic()
        outcome = await runner.run_source(s, SearchParams(query="x"), deadline_ms=200)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0, f"runner waited too long: {elapsed:.2f}s")
        self.assertEqual(outcome.status.state, SourceState.TIMEOUT)
        self.assertEqual(outcome.status.failure_mode, FailureMode.HTTP_TIMEOUT)

    async def test_legacy_timed_out_flag_becomes_partial_with_data(self):
        runner = HttpRunner()
        s = _FakeScraper(
            "legacy",
            return_value=[JobListing(title="x", url="https://x", company="Acme")],
            timed_out=True,
        )
        outcome = await runner.run_source(s, SearchParams(query="x"), deadline_ms=2000)
        self.assertEqual(outcome.status.state, SourceState.PARTIAL)
        self.assertEqual(outcome.status.failure_mode, FailureMode.SLOW_PAGINATION)

    async def test_legacy_timed_out_flag_with_no_data_becomes_timeout(self):
        runner = HttpRunner()
        s = _FakeScraper("legacy", return_value=[], timed_out=True)
        outcome = await runner.run_source(s, SearchParams(query="x"), deadline_ms=2000)
        self.assertEqual(outcome.status.state, SourceState.TIMEOUT)
        self.assertEqual(outcome.status.failure_mode, FailureMode.HTTP_TIMEOUT)


class CooldownTest(unittest.IsolatedAsyncioTestCase):
    async def test_threshold_trip_short_circuits_subsequent_sources(self):
        runner = HttpRunner(cooldown_threshold=3, cooldown_window_s=5)

        # Three distinct sources fail with NetworkError.
        for name in ("a", "b", "c"):
            s = _FakeScraper(name, raises=NetworkError("dns"))
            await runner.run_source(s, SearchParams(query="x"), deadline_ms=500)
        self.assertTrue(runner.global_outage)

        # Next source short-circuits without invoking the scraper.
        called = {"v": False}

        class _Sentry(_FakeScraper):
            def search(self, params):
                called["v"] = True
                return []

        outcome = await runner.run_source(
            _Sentry("d"), SearchParams(query="x"), deadline_ms=500
        )
        self.assertFalse(called["v"], "outage trip must skip scraper invocation")
        self.assertEqual(outcome.status.state, SourceState.ERROR)
        self.assertEqual(outcome.status.failure_mode, FailureMode.GLOBAL_NETWORK_OUTAGE)

    async def test_threshold_counts_distinct_sources_not_events(self):
        runner = HttpRunner(cooldown_threshold=3, cooldown_window_s=5)
        # Same source 5 times — only counts once.
        for _ in range(5):
            s = _FakeScraper("same", raises=NetworkError("dns"))
            await runner.run_source(s, SearchParams(query="x"), deadline_ms=500)
        self.assertFalse(runner.global_outage)


class CancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_propagates(self):
        """Verifies that `task.cancel()` on a runner coroutine raises
        CancelledError. The thread runs to completion (verified
        empirically in plan §4): we release it explicitly so the test's
        executor shutdown doesn't block."""
        import threading

        runner = HttpRunner()
        started_evt = threading.Event()
        release_evt = threading.Event()

        class _SlowScraper:
            name = "slow"
            display_name = "slow"
            timed_out = False
            capabilities: dict = {}

            def search(self, params):
                started_evt.set()
                # Release fast so the abandoned thread doesn't block
                # the test's executor on teardown.
                release_evt.wait(timeout=5.0)
                return []

        task = asyncio.create_task(
            runner.run_source(_SlowScraper(), SearchParams(query="x"), deadline_ms=10_000)
        )
        await asyncio.to_thread(started_evt.wait, 2.0)
        self.assertTrue(started_evt.is_set())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # Now let the abandoned thread terminate so teardown is fast.
        release_evt.set()


if __name__ == "__main__":
    unittest.main()
