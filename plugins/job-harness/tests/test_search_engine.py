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

from job_harness.base import BaseBrowserScraper, BaseScraper
from job_harness.models import JobListing, SearchParams
from job_harness.registry import _SCRAPERS, register_scraper
from job_harness.run_journal import RunJournalReader, RunJournalWriter
from job_harness.search_engine import SearchEngine
from job_harness.types import (
    FailureMode,
    FilterSupport,
    RunState,
    ScraperCapabilities,
    SearchRequest,
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
    capabilities: ClassVar[ScraperCapabilities] = _capabilities()

    @classmethod
    def supports_country(cls, country):
        return True

    def search(self, params: SearchParams) -> list[JobListing]:
        return [
            JobListing(title="QA", url=f"https://x/{self.name}/1", company="Acme", source=self.name),
            JobListing(title="QA Senior", url=f"https://x/{self.name}/2", company="Acme", source=self.name, remote=True),
        ]

    def fetch_detail(self, listing):
        return listing


class _SlowScraper(_OkScraper):
    SLEEP_S: ClassVar[float] = 1.0

    def search(self, params: SearchParams) -> list[JobListing]:
        time.sleep(self.SLEEP_S)
        # Vary title+company by source so dedupe does not collapse them.
        return [JobListing(
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

    async def search_with_page(self, page, params: SearchParams) -> list[JobListing]:
        self.mark_timed_out()
        return [
            JobListing(
                title="QA from partial browser",
                url="https://x/browser/1",
                company="Browser Co",
                source=self.name,
            )
        ]


class _UnsupportedRemoteScraper(_OkScraper):
    capabilities: ClassVar[ScraperCapabilities] = _capabilities(remote_only=FilterSupport.UNSUPPORTED)


class _UnsupportedExperienceScraper(_OkScraper):
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
    overrides.setdefault("source_timeout_ms", 5000)
    overrides.setdefault("total_timeout_ms", 10000)
    return SearchRequest(**overrides)


async def _run(engine: SearchEngine, request, journal, run_id="r-test-000000"):
    return await engine.execute(request, journal=journal, run_id=run_id)


class _InlineBrowserPool:
    async def run_with_page(self, func, *, timeout_ms=None):
        return await func(object())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ValidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_query_rejected(self):
        engine = SearchEngine()
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

            def search(self, params: SearchParams) -> list[JobListing]:
                calls = type(self)._calls.get(self.name, 0)
                type(self)._calls[self.name] = calls + 1
                if calls == 0:
                    raise RuntimeError("first attempt failed")
                return super().search(params)

        engine = SearchEngine()
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
        engine = SearchEngine()
        with _RegistryContext({"slow": Slow}), tempfile.TemporaryDirectory() as d:
            t0 = time.monotonic()
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("slow",), source_timeout_ms=200, total_timeout_ms=2000),
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
        engine = SearchEngine()
        with _RegistryContext({"a": Slow, "b": Slow}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("a", "b"), source_timeout_ms=10_000, total_timeout_ms=200),
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
            self.assertEqual(status["raw_count"], 1)
        engine.http_runner.shutdown()


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


class StrictFlagPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_strict_flags_drops_unsupported_scrapers(self):
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
            self.assertEqual(statuses["noremote"]["state"], SourceState.SKIPPED_UNSUPPORTED_FLAG.value)
            self.assertEqual(statuses["noremote"]["failure_mode"], FailureMode.UNSUPPORTED_FLAG.value)
        engine.http_runner.shutdown()

    async def test_lenient_flags_keeps_unsupported_scrapers(self):
        engine = SearchEngine()
        with _RegistryContext({"noremote": _UnsupportedRemoteScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("noremote",), remote_only=True, strict_flags=False),
                    journal,
                )
            statuses = {s["source"]: s for s in result.summary["source_statuses"]}
            self.assertEqual(statuses["noremote"]["state"], SourceState.OK.value)
        engine.http_runner.shutdown()

    async def test_flag_enforcement_summary_built(self):
        engine = SearchEngine()
        with _RegistryContext({"ok": _OkScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(engine, _request(sources=("ok",), remote_only=True), journal)
            block = result.summary["flag_enforcement"]
            self.assertEqual(block["remote_only"]["policy"], "strict")
            self.assertEqual(
                block["remote_only"]["by_source"]["ok"]["support"],
                FilterSupport.SERVER.value,
            )
            self.assertTrue(block["remote_only"]["by_source"]["ok"]["applied"])
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
            self.assertEqual(
                result.summary["flag_enforcement"]["experience"]["by_source"]["src"],
                {
                    "support": FilterSupport.UNSUPPORTED.value,
                    "applied": True,
                    "applied_by": "grade_engine",
                },
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
            self.assertEqual(len(result.listings), 1)
            self.assertEqual(result.summary["max_results"]["requested"], 1)
            self.assertEqual(result.summary["max_results"]["returned"], 1)
        engine.http_runner.shutdown()

    async def test_exact_experience_filter_keeps_middle_and_unknown_only(self):
        class GradeScraper(_OkScraper):
            capabilities: ClassVar[ScraperCapabilities] = _capabilities(
                experience=FilterSupport.UNSUPPORTED
            )

            def search(self, params: SearchParams) -> list[JobListing]:
                return [
                    JobListing(
                        title="Middle QA",
                        url="https://x/middle",
                        company="Acme",
                        source=self.name,
                    ),
                    JobListing(
                        title="Senior QA",
                        url="https://x/senior",
                        company="Acme",
                        source=self.name,
                    ),
                    JobListing(
                        title="QA Engineer",
                        url="https://x/unknown",
                        company="Acme",
                        source=self.name,
                    ),
                ]

        engine = SearchEngine()
        with _RegistryContext({"src": GradeScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await _run(
                    engine,
                    _request(sources=("src",), experience_levels=("middle",)),
                    journal,
                )
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
        engine = SearchEngine()
        with _RegistryContext({"slow": Slow}), tempfile.TemporaryDirectory() as d:
            journal = RunJournalWriter(Path(d))
            task = asyncio.create_task(_run(engine, _request(sources=("slow",), total_timeout_ms=10_000), journal))
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
