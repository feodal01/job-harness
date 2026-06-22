"""Tests for SearchEngine — orchestration, flag policy, and journal writes.

Uses a fake scraper factory so no real network call is made and tests
run in milliseconds. Each test sets up exactly the scrapers it needs.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar

from tests._support.fake_browser import FakeBrowser

import job_harness.v1.browser_pool as browser_pool_module
from job_harness.v1.base import BaseBrowserScraper, BaseScraper
from job_harness.v1.browser_pool import BlockSignal, BrowserPool
from job_harness.v1.models import RawListing, SearchParams
from job_harness.v1.registry import _SCRAPERS, register_scraper
from job_harness.v1.run_journal import RunJournalReader, RunJournalWriter
from job_harness.v1.search_engine import SearchEngine
from job_harness.v1.source_runtime import SourceRuntimeConfig
from job_harness.v1.types import (
    BlockReason,
    FailureMode,
    FilterSupport,
    RunState,
    ScraperCapabilities,
    SearchCriterion,
    SearchRequest,
    SourceGroup,
    SourceState,
)

# ---------------------------------------------------------------------------
# Test scrapers — declared at module scope so registry registration is
# stable across tests. We swap _SCRAPERS in/out per test for isolation.
# ---------------------------------------------------------------------------


def _capabilities(**kw: FilterSupport) -> ScraperCapabilities:
    base: ScraperCapabilities = {
        "remote_only": FilterSupport.SERVER,
        "country": FilterSupport.SERVER,
        "experience": FilterSupport.SERVER,
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.CLIENT,
        "query_match": FilterSupport.SERVER,
    }
    for k, v in kw.items():
        if v is not None:
            base[k] = v  # type: ignore[literal-required]
    return base


class _OkScraper(BaseScraper):
    """Returns a fixed list of listings."""

    display_name = "OK"
    requires_browser = False
    detail_requires_browser = False
    countries: tuple[str, ...] = ()
    source_group = SourceGroup.AGGREGATOR
    source_limit = 3
    server_criteria = frozenset(
        {
            SearchCriterion.QUERY,
            SearchCriterion.COUNTRY,
            SearchCriterion.REMOTE_ONLY,
            SearchCriterion.EXPERIENCE_LEVELS,
        }
    )
    capabilities: ClassVar[ScraperCapabilities] = _capabilities()

    @classmethod
    def supports_country(cls, country):
        return True

    def search(self, params: SearchParams) -> list[RawListing]:
        return [
            RawListing(title="QA", url=f"https://x/{self.name}/1", company="Acme", source=self.name),
            RawListing(title="QA Senior", url=f"https://x/{self.name}/2", company="Acme", source=self.name, remote=True),
        ]

    def fetch_detail(self, listing):
        return listing


class _SlowScraper(_OkScraper):
    SLEEP_S: ClassVar[float] = 1.0

    def search(self, params: SearchParams) -> list[RawListing]:
        time.sleep(self.SLEEP_S)
        # Vary title+company by source so dedupe does not collapse them.
        return [RawListing(
            title=f"QA-{self.name}",
            url=f"https://x/{self.name}/1",
            company=f"Acme-{self.name}",
            source=self.name,
        )]


class _RaisesScraper(_OkScraper):
    EXC: ClassVar[Exception] = RuntimeError("parser broke")

    def search(self, params: SearchParams):
        raise type(self).EXC


class _BrowserScraper(_OkScraper):
    requires_browser = True
    detail_requires_browser = True


class _PartialBrowserScraper(BaseBrowserScraper):
    display_name = "Partial Browser"
    capabilities: ClassVar[ScraperCapabilities] = _capabilities()

    @classmethod
    def supports_country(cls, country):
        return True

    async def search_with_page(self, page, params: SearchParams) -> list[RawListing]:
        self.mark_timed_out()
        return [
            RawListing(
                title="QA from partial browser",
                url="https://x/browser/1",
                company="Browser Co",
                source=self.name,
            )
        ]


class _UnsupportedRemoteScraper(_OkScraper):
    server_criteria = frozenset(
        {
            SearchCriterion.QUERY,
            SearchCriterion.COUNTRY,
            SearchCriterion.EXPERIENCE_LEVELS,
        }
    )
    capabilities: ClassVar[ScraperCapabilities] = _capabilities(remote_only=FilterSupport.UNSUPPORTED)


class _UnsupportedExperienceScraper(_OkScraper):
    server_criteria = frozenset(
        {
            SearchCriterion.QUERY,
            SearchCriterion.COUNTRY,
            SearchCriterion.REMOTE_ONLY,
        }
    )
    capabilities: ClassVar[ScraperCapabilities] = _capabilities(
        experience=FilterSupport.UNSUPPORTED
    )


class _RUOnlyScraper(_OkScraper):
    countries: tuple[str, ...] = ("RU",)

    @classmethod
    def supports_country(cls, country):
        return country is None or country in cls.countries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RegistryContext:
    """Drop the existing registry, install only the named scrapers,
    restore afterwards. The Engine's `_resolve_sources` walks the registry
    directly via `iter_registered`, so this gives full control."""

    def __init__(self, scraper_classes: dict[str, type[BaseScraper]]):
        self._classes = scraper_classes
        self._saved: dict = {}

    def __enter__(self):
        self._saved = dict(_SCRAPERS)
        _SCRAPERS.clear()
        for name, cls in self._classes.items():
            register_scraper(name)(cls)
        return self

    def __exit__(self, *_exc):
        _SCRAPERS.clear()
        _SCRAPERS.update(self._saved)


def _request(**overrides) -> SearchRequest:
    overrides.setdefault("query", "QA")
    return SearchRequest(**overrides)


async def _run(engine: SearchEngine, request, journal, run_id="r-test-000000"):
    return await engine.execute(request, journal=journal, run_id=run_id)


class _InlineBrowserPool:
    async def run_with_page(self, func, *, timeout_ms=None):
        return await func(object())


def _browser_factory(browser: FakeBrowser):
    async def factory():
        return browser

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ValidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_query_rejected(self):
        engine = SearchEngine(
            runtime_config=SourceRuntimeConfig(
                source_attempt_timeout_ms=200,
                source_max_attempts=1,
            )
        )
        with tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal, self.assertRaises(ValueError):
                await engine.execute(_request(query="   "), journal=journal, run_id="r-x")
        engine.http_runner.shutdown()

    async def test_zero_max_results_rejected(self):
        engine = SearchEngine()
        with tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal, self.assertRaises(ValueError):
                await engine.execute(_request(max_results=0), journal=journal, run_id="r-x")
        engine.http_runner.shutdown()

    async def test_unknown_profile_rejected(self):
        engine = SearchEngine()
        with tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal, self.assertRaises(ValueError):
                await engine.execute(_request(profile="weird"), journal=journal, run_id="r-x")
        engine.http_runner.shutdown()


class HappyPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_single_http_source(self):
        engine = SearchEngine()
        with _RegistryContext({"src": _OkScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("src",)), journal)
            self.assertEqual(len(result.listings), 2)
            self.assertEqual(result.errors, [])
            # Journal contains everything.
            snap = RunJournalReader(Path(d)).snapshot()
            self.assertEqual(snap.state, RunState.COMPLETED)
            self.assertEqual(snap.listings_count, 2)
            self.assertIn("src", snap.sources)
            self.assertEqual(snap.sources["src"].state, SourceState.OK)
        engine.http_runner.shutdown()


class ConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_sources_dispatched_in_parallel(self):
        class A(_SlowScraper):
            SLEEP_S = 0.2
        class B(_SlowScraper):
            SLEEP_S = 0.2
        class C(_SlowScraper):
            SLEEP_S = 0.2
        engine = SearchEngine()
        with _RegistryContext({"a": A, "b": B, "c": C}), tempfile.TemporaryDirectory() as d:
            t0 = time.monotonic()
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("a", "b", "c")), journal)
            elapsed = time.monotonic() - t0
            # Three 0.2s scrapers, parallel ≈ 0.2s; serial would be ≈ 0.6s.
            self.assertLess(elapsed, 0.4, f"expected parallel, got {elapsed:.2f}s")
            self.assertEqual(len(result.listings), 3)
        engine.http_runner.shutdown()


class ExecuteRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_execute_retry_only_dispatches_named_sources(self):
        class _FailOnceScraper(_OkScraper):
            _calls: ClassVar[dict[str, int]] = {}

            def search(self, params: SearchParams) -> list[RawListing]:
                calls = type(self)._calls.get(self.name, 0)
                type(self)._calls[self.name] = calls + 1
                if calls == 0:
                    raise RuntimeError("first attempt failed")
                return super().search(params)

        engine = SearchEngine(
            runtime_config=SourceRuntimeConfig(
                source_attempt_timeout_ms=200,
                source_max_attempts=1,
            )
        )
        with _RegistryContext({"good": _OkScraper, "flaky": _FailOnceScraper}), tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            with RunJournalWriter(run_dir) as journal:
                await _run(
                    engine,
                    _request(sources=("good", "flaky")),
                    journal,
                    run_id="r-retry-test",
                )
                retry_request = _request(sources=("flaky",))
                journal.write_listings_purged(sources=["flaky"])
                journal.write_run_retry_started(sources=["flaky"])
                await engine.execute_retry(
                    retry_request,
                    journal=journal,
                    run_id="r-retry-test",
                    sources=("flaky",),
                )
            snap = RunJournalReader(run_dir).snapshot()
            self.assertEqual(snap.sources["good"].state, SourceState.OK)
            self.assertEqual(snap.sources["flaky"].state, SourceState.OK)
            self.assertEqual(snap.listings_count, 4)
            events = list(RunJournalReader(run_dir).iter_events())
            self.assertTrue(any(e.get("type") == "listings_purged" for e in events))
        engine.http_runner.shutdown()


class FailureModeTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_source_raising_does_not_block_others(self):
        engine = SearchEngine()
        with _RegistryContext({"good": _OkScraper, "bad": _RaisesScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("good", "bad")), journal)
            self.assertEqual(len(result.listings), 2)
            sources = {s["source"]: s for s in result.summary["source_statuses"]}
            self.assertEqual(sources["good"]["state"], SourceState.OK.value)
            self.assertEqual(sources["bad"]["state"], SourceState.ERROR.value)
            self.assertEqual(sources["bad"]["failure_mode"], FailureMode.PARSE_ERROR.value)
        engine.http_runner.shutdown()

    async def test_source_timeout_marks_http_timeout(self):
        class Slow(_SlowScraper):
            SLEEP_S = 2.0
        engine = SearchEngine(
            runtime_config=SourceRuntimeConfig(
                source_attempt_timeout_ms=200,
                source_max_attempts=1,
            )
        )
        with _RegistryContext({"slow": Slow}), tempfile.TemporaryDirectory() as d:
            t0 = time.monotonic()
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("slow",)),
                    journal,
                )
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 1.0, f"engine waited too long: {elapsed:.2f}s")
            statuses = {s["source"]: s for s in result.summary["source_statuses"]}
            self.assertEqual(statuses["slow"]["state"], SourceState.TIMEOUT.value)
            self.assertEqual(statuses["slow"]["failure_mode"], FailureMode.HTTP_TIMEOUT.value)
        engine.http_runner.shutdown()

    async def test_total_timeout_cancels_in_flight_sources(self):
        class Slow(_SlowScraper):
            SLEEP_S = 5.0
        engine = SearchEngine(
            runtime_config=SourceRuntimeConfig(
                source_attempt_timeout_ms=10_000,
                total_run_timeout_ms=200,
                source_max_attempts=1,
            )
        )
        with _RegistryContext({"a": Slow, "b": Slow}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("a", "b")),
                    journal,
                )
            statuses = {s["source"]: s for s in result.summary["source_statuses"]}
            # Both either timed out at source level (if HttpRunner won) or
            # got cancelled at total level (if wait_for won). Either way
            # they must NOT be OK.
            for s in ("a", "b"):
                self.assertNotEqual(statuses[s]["state"], SourceState.OK.value)
        engine.http_runner.shutdown()

    async def test_browser_source_can_return_partial_listings(self):
        engine = SearchEngine(browser_pool=_InlineBrowserPool())
        with _RegistryContext({"browser": _PartialBrowserScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("browser",)), journal)
            self.assertEqual(len(result.listings), 1)
            status = result.summary["source_statuses"][0]
            self.assertEqual(status["state"], SourceState.PARTIAL.value)
            self.assertEqual(status["failure_mode"], FailureMode.SLOW_PAGINATION.value)
            self.assertEqual(status["listings_written"], 1)
        engine.http_runner.shutdown()


class BrowserPoolIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_queued_browser_source_completes_without_pool_acquire_timeout(self):
        entered: list[str] = []
        first_two_started = asyncio.Event()
        release_first_two = asyncio.Event()

        class QueuedBrowserScraper(BaseBrowserScraper):
            display_name = "Queued Browser"
            source_group = SourceGroup.AGGREGATOR
            source_limit = 1
            server_criteria = frozenset({SearchCriterion.QUERY})
            capabilities: ClassVar[ScraperCapabilities] = _capabilities()

            @classmethod
            def supports_country(cls, country):
                return True

            async def search_with_page(self, page, params: SearchParams) -> list[RawListing]:
                entered.append(self.name)
                if len(entered) == 2:
                    first_two_started.set()
                if len(entered) <= 2:
                    await release_first_two.wait()
                return [
                    RawListing(
                        title=f"QA {self.name}",
                        url=f"https://x/{self.name}",
                        company=f"Company {self.name}",
                        source=self.name,
                    )
                ]

        class A(QueuedBrowserScraper):
            pass

        class B(QueuedBrowserScraper):
            pass

        class C(QueuedBrowserScraper):
            pass

        pool = BrowserPool(
            max_contexts=2,
            acquire_timeout_ms=None,
            browser_factory=_browser_factory(FakeBrowser()),
        )
        engine = SearchEngine(
            browser_pool=pool,
            runtime_config=SourceRuntimeConfig(
                source_attempt_timeout_ms=1000,
                source_max_attempts=1,
            ),
        )

        with _RegistryContext({"a": A, "b": B, "c": C}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                task = asyncio.create_task(
                    _run(engine, _request(sources=("a", "b", "c")), journal)
                )
                await asyncio.wait_for(first_two_started.wait(), timeout=1.0)
                await asyncio.sleep(0.05)
                self.assertEqual(len(entered), 2)
                release_first_two.set()
                result = await task

        statuses = {s["source"]: s for s in result.summary["source_statuses"]}
        self.assertEqual(set(statuses), {"a", "b", "c"})
        self.assertEqual(len(entered), 3)
        for status in statuses.values():
            self.assertEqual(status["state"], SourceState.OK.value)
            self.assertNotEqual(
                status.get("failure_mode"),
                FailureMode.POOL_ACQUIRE_TIMEOUT.value,
            )
        await pool.shutdown()
        engine.http_runner.shutdown()

    async def test_blocked_browser_partial_listing_is_written_to_raw_artifacts(self):
        class BlockedPartialScraper(BaseBrowserScraper):
            display_name = "Blocked Partial"
            source_group = SourceGroup.AGGREGATOR
            source_limit = 1
            server_criteria = frozenset({SearchCriterion.QUERY})
            capabilities: ClassVar[ScraperCapabilities] = _capabilities()

            @classmethod
            def supports_country(cls, country):
                return True

            async def search_with_page(self, page, params: SearchParams) -> list[RawListing]:
                return [
                    RawListing(
                        title="QA behind block",
                        url="https://x/blocked",
                        company="Blocked Co",
                        source=self.name,
                    )
                ]

        original_probe = browser_pool_module.is_blocked

        async def blocked_probe(page):
            return BlockSignal(reason=BlockReason.ANTI_BOT_PAGE, signal="test block")

        browser_pool_module.is_blocked = blocked_probe
        pool = BrowserPool(max_contexts=1, browser_factory=_browser_factory(FakeBrowser()))
        engine = SearchEngine(
            browser_pool=pool,
            runtime_config=SourceRuntimeConfig(
                source_attempt_timeout_ms=1000,
                source_max_attempts=1,
            ),
        )

        try:
            with _RegistryContext({"blocked": BlockedPartialScraper}), tempfile.TemporaryDirectory() as d:
                run_dir = Path(d)
                with RunJournalWriter(run_dir) as journal:
                    result = await _run(
                        engine,
                        _request(sources=("blocked",)),
                        journal,
                    )
                status = result.summary["source_statuses"][0]
                raw_lines = (run_dir / "raw_search.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
        finally:
            browser_pool_module.is_blocked = original_probe
            await pool.shutdown()
            engine.http_runner.shutdown()

        self.assertEqual(status["state"], SourceState.BLOCKED.value)
        self.assertEqual(status["failure_mode"], FailureMode.ANTI_BOT_PAGE.value)
        self.assertEqual(status["listings_written"], 1)
        self.assertEqual(result.summary["raw_search"]["listings_written"], 1)
        self.assertEqual(len(raw_lines), 1)
        raw_record = json.loads(raw_lines[0])
        self.assertEqual(raw_record["listing"]["title"], "QA behind block")


class CountryRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_country_mismatch_skips_with_not_in_country(self):
        engine = SearchEngine()
        with _RegistryContext({"ru": _RUOnlyScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("ru",), country="AM"), journal)
            statuses = {s["source"]: s for s in result.summary["source_statuses"]}
            self.assertEqual(statuses["ru"]["state"], SourceState.SKIPPED.value)
            self.assertEqual(statuses["ru"]["failure_mode"], FailureMode.NOT_IN_COUNTRY.value)
        engine.http_runner.shutdown()


class CriteriaSummaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_remote_is_collected_and_reported(self):
        engine = SearchEngine()
        with _RegistryContext({"ok": _OkScraper, "noremote": _UnsupportedRemoteScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("ok", "noremote"), remote_only=True),
                    journal,
                )
            statuses = {s["source"]: s for s in result.summary["source_statuses"]}
            self.assertEqual(statuses["ok"]["state"], SourceState.OK.value)
            self.assertEqual(statuses["noremote"]["state"], SourceState.OK.value)
            self.assertIn(
                SearchCriterion.REMOTE_ONLY.value,
                statuses["noremote"]["unsupported_requested_criteria"],
            )
        engine.http_runner.shutdown()

    async def test_server_criteria_used_summary_built(self):
        engine = SearchEngine()
        with _RegistryContext({"ok": _OkScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("ok",), remote_only=True), journal)
            status = result.summary["source_statuses"][0]
            self.assertIn(SearchCriterion.REMOTE_ONLY.value, status["server_criteria_used"])
            self.assertEqual([], status["unsupported_requested_criteria"])
        engine.http_runner.shutdown()

    async def test_experience_filter_does_not_skip_unsupported_sources(self):
        engine = SearchEngine()
        with _RegistryContext({"src": _UnsupportedExperienceScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("src",), experience_levels=("middle",)),
                    journal,
                )
            statuses = {s["source"]: s for s in result.summary["source_statuses"]}
            self.assertEqual(statuses["src"]["state"], SourceState.OK.value)
            self.assertIn(
                SearchCriterion.EXPERIENCE_LEVELS.value,
                statuses["src"]["unsupported_requested_criteria"],
            )
        engine.http_runner.shutdown()


class ProfileTest(unittest.IsolatedAsyncioTestCase):
    async def test_fast_profile_skips_browser_sources(self):
        engine = SearchEngine()
        with _RegistryContext({"http": _OkScraper, "browser": _BrowserScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("http", "browser"), profile="fast"),
                    journal,
                )
            statuses = {s["source"]: s for s in result.summary["source_statuses"]}
            self.assertEqual(statuses["http"]["state"], SourceState.OK.value)
            self.assertEqual(statuses["browser"]["state"], SourceState.SKIPPED.value)
            self.assertEqual(
                statuses["browser"]["failure_mode"],
                FailureMode.NOT_IN_PROFILE.value,
            )
        engine.http_runner.shutdown()


class DedupeAndFilterTest(unittest.IsolatedAsyncioTestCase):
    async def test_filter_plan_applied_after_aggregation(self):
        # remote_only=True must drop listings where listing.remote=False.
        engine = SearchEngine()
        with _RegistryContext({"src": _OkScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("src",), remote_only=True), journal)
            # Only the remote=True listing survives.
            self.assertEqual(len(result.listings), 1)
            self.assertTrue(result.listings[0].remote)
            self.assertEqual(result.summary["filters"]["before"], 2)
            self.assertEqual(result.summary["filters"]["after"], 1)
        engine.http_runner.shutdown()

    async def test_max_results_truncates(self):
        engine = SearchEngine()
        with _RegistryContext({"a": _OkScraper, "b": _OkScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("a", "b"), max_results=1),
                    journal,
                )
            snap = RunJournalReader(Path(d)).snapshot()
            self.assertEqual(len(result.listings), 1)
            self.assertEqual(snap.listings_count, 4)
            self.assertEqual(result.summary["max_results"]["requested"], 1)
            self.assertEqual(result.summary["max_results"]["returned"], 1)
            self.assertFalse(result.summary["raw_search"]["global_truncation"])
        engine.http_runner.shutdown()

    async def test_exact_experience_filter_keeps_middle_and_unknown_only(self):
        class GradeScraper(_OkScraper):
            capabilities: ClassVar[ScraperCapabilities] = _capabilities(
                experience=FilterSupport.UNSUPPORTED
            )

            def search(self, params: SearchParams) -> list[RawListing]:
                return [
                    RawListing(
                        title="Middle QA",
                        url="https://x/middle",
                        company="Acme",
                        source=self.name,
                    ),
                    RawListing(
                        title="Senior QA",
                        url="https://x/senior",
                        company="Acme",
                        source=self.name,
                    ),
                    RawListing(
                        title="QA Engineer",
                        url="https://x/unknown",
                        company="Acme",
                        source=self.name,
                    ),
                ]

        engine = SearchEngine()
        with _RegistryContext({"src": GradeScraper}), tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("src",), experience_levels=("middle",)),
                    journal,
                )
            raw_lines = (run_dir / "raw_search.jsonl").read_text(encoding="utf-8").splitlines()
            raw_records = [json.loads(line) for line in raw_lines]
            self.assertNotIn("experience_levels", raw_records[1]["listing"])
            self.assertEqual(
                ["Middle QA", "QA Engineer"],
                [listing.title for listing in result.listings],
            )
            self.assertEqual(["middle"], result.listings[0].experience_levels)
            self.assertEqual("unknown", result.listings[1].experience_origin)
            self.assertEqual(
                {
                    "requested_levels": ["middle"],
                    "native_matched": 0,
                    "estimated_matched": 1,
                    "unknown_kept": 1,
                    "removed": 1,
                },
                result.summary["filters"]["experience"],
            )
        engine.http_runner.shutdown()


class CancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_writes_final_journal_state(self):
        class Slow(_SlowScraper):
            SLEEP_S = 5.0
        engine = SearchEngine(
            runtime_config=SourceRuntimeConfig(total_run_timeout_ms=10_000)
        )
        with _RegistryContext({"slow": Slow}), tempfile.TemporaryDirectory() as d:
            journal = RunJournalWriter(Path(d))
            task = asyncio.create_task(_run(engine, _request(sources=("slow",)), journal))
            # Let it start.
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            journal.close()
            snap = RunJournalReader(Path(d)).snapshot()
            self.assertEqual(snap.state, RunState.CANCELLED)
        engine.http_runner.shutdown()


class SanityBaselineTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_result_against_baseline_flagged_suspicious(self):
        class ZeroScraper(_OkScraper):
            def search(self, params):
                return []

        with tempfile.TemporaryDirectory() as d:
            baseline_path = Path(d) / "baselines.json"
            baseline_path.write_text(
                json.dumps({"hh_ru": {"*": {"python": 10}}}),
                encoding="utf-8",
            )
            engine = SearchEngine(sanity_baselines_path=baseline_path)
            with _RegistryContext({"hh_ru": ZeroScraper}):
                with RunJournalWriter(Path(d)) as journal:
                    result = await _run(engine, _request(query="python", sources=("hh_ru",)), journal)
                sanity = result.summary["result_sanity"]
                self.assertIn("hh_ru", sanity)
                self.assertEqual(sanity["hh_ru"]["verdict"], "suspicious")
                self.assertEqual(sanity["hh_ru"]["baseline_min"], 10)
            engine.http_runner.shutdown()


if __name__ == "__main__":
    unittest.main()
