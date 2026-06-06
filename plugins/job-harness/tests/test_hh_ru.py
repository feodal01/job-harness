"""Tests for the async hh.ru family scraper.

The scraper goes through BrowserPool in production. Here we exercise
`search_with_page` against a fake DOM, and the end-to-end engine path
against the same fake.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests._support.fake_browser import (
    FakeBrowser,
    FakeContext,
    FakePage,
    PageBehaviour,
    card_dom,
)

from job_harness.browser_pool import BrowserPool
from job_harness.models import SearchParams
from job_harness.registry import _SCRAPERS, register_scraper
from job_harness.run_journal import RunJournalWriter
from job_harness.scrapers.hh_ru import (
    _CARD_SELECTOR,
    HeadHunterKgScraper,
    HHKzScraper,
    HHRuScraper,
    HHUzScraper,
    RabotaByScraper,
)
from job_harness.search_engine import SearchEngine
from job_harness.types import (
    FailureMode,
    SearchRequest,
    SourceState,
    Transport,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _factory(browser: FakeBrowser):
    async def make():
        return browser

    return make


def _request(**overrides):
    overrides.setdefault("query", "QA")
    overrides.setdefault("sources", ("hh_ru",))
    overrides.setdefault("source_timeout_ms", 5000)
    overrides.setdefault("total_timeout_ms", 8000)
    return SearchRequest(**overrides)


class _FailingCountLocator:
    def __init__(self, inner: Any, page: _FailingCountPage) -> None:
        self._inner = inner
        self._page = page

    async def count(self) -> int:
        if self._page.remaining_count_failures > 0:
            self._page.remaining_count_failures -= 1
            raise RuntimeError(
                "Locator.count: Execution context was destroyed, "
                "most likely because of a navigation"
            )
        return await self._inner.count()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _FailingCountPage(FakePage):
    def __init__(self, *, behaviour: PageBehaviour, remaining_count_failures: int) -> None:
        super().__init__(behaviour=behaviour)
        self.remaining_count_failures = remaining_count_failures

    def locator(self, selector: str) -> Any:
        locator = super().locator(selector)
        if selector == _CARD_SELECTOR:
            return _FailingCountLocator(locator, self)
        return locator


class _RegistryContext:
    def __init__(self, classes: dict[str, type]):
        self._classes = classes
        self._saved: dict = {}

    def __enter__(self):
        self._saved = dict(_SCRAPERS)
        _SCRAPERS.clear()
        for name, cls in self._classes.items():
            register_scraper(name)(cls)
        return self

    def __exit__(self, *_e):
        _SCRAPERS.clear()
        _SCRAPERS.update(self._saved)


# ---------------------------------------------------------------------------
# Direct search_with_page tests
# ---------------------------------------------------------------------------


class HHRuParseTest(unittest.IsolatedAsyncioTestCase):
    async def test_parses_cards_from_fake_dom(self):
        dom = card_dom(
            {
                "title": "QA Engineer",
                "link_href": "https://hh.ru/vacancy/123",
                "company": "Acme",
                "salary": "от 200 000 ₽",
                "experience_raw": "От 1 года до 3 лет",
                "remote": True,
            },
            {
                "title": "Senior QA",
                "link_href": "https://hh.ru/vacancy/456",
                "company": "Beta",
                "remote": False,
            },
        )
        page = FakePage(behaviour=PageBehaviour(title="Job listings", dom=dom))
        scraper = HHRuScraper(max_results=10)
        listings = await scraper.search_with_page(page, SearchParams(query="QA"))
        self.assertEqual(len(listings), 2)
        self.assertEqual(listings[0].title, "QA Engineer")
        self.assertEqual(listings[0].url, "https://hh.ru/vacancy/123")
        self.assertEqual(listings[0].company, "Acme")
        self.assertEqual(listings[0].salary, "от 200 000 ₽")
        self.assertEqual(listings[0].experience, "middle")
        self.assertTrue(listings[0].remote)
        self.assertEqual(listings[0].country, "RU")
        self.assertFalse(listings[1].remote)

    async def test_country_set_per_subclass(self):
        dom = card_dom(
            {"title": "QA", "link_href": "https://hh.kz/vacancy/1", "company": "X"}
        )
        page = FakePage(behaviour=PageBehaviour(dom=dom))
        scraper = HHKzScraper(max_results=10)
        listings = await scraper.search_with_page(page, SearchParams(query="QA"))
        self.assertEqual(listings[0].country, "KZ")

    async def test_retries_when_card_count_races_with_navigation(self):
        dom = card_dom(
            {"title": "QA", "link_href": "https://hh.uz/vacancy/1", "company": "X"}
        )
        page = _FailingCountPage(
            behaviour=PageBehaviour(dom=dom),
            remaining_count_failures=1,
        )
        scraper = HHUzScraper(max_results=10)
        listings = await scraper.search_with_page(page, SearchParams(query="QA"))
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].url, "https://hh.uz/vacancy/1")
        self.assertEqual(page.remaining_count_failures, 0)

    async def test_search_url_includes_remote_when_requested(self):
        scraper = HHRuScraper()
        url = scraper._build_search_url(SearchParams(query="QA", remote_only=True))
        self.assertIn("schedule=remote", url)
        url2 = scraper._build_search_url(SearchParams(query="QA", remote_only=False))
        self.assertNotIn("schedule=remote", url2)

    async def test_max_results_truncates(self):
        dom = card_dom(*[
            {"title": f"QA {i}", "link_href": f"https://hh.ru/v/{i}", "company": "Acme"}
            for i in range(20)
        ])
        page = FakePage(behaviour=PageBehaviour(dom=dom))
        scraper = HHRuScraper(max_results=5)
        listings = await scraper.search_with_page(page, SearchParams(query="QA", max_results=5))
        self.assertEqual(len(listings), 5)

    async def test_legacy_sync_search_raises(self):
        """The new contract: browser scrapers are async-only. Any sync
        call surfaces a NotImplementedError instead of silently
        producing nothing."""
        scraper = HHRuScraper()
        with self.assertRaises(NotImplementedError):
            scraper.search(SearchParams(query="QA"))


class HHFamilySubclassesTest(unittest.TestCase):
    def test_subclasses_inherit_capabilities_and_set_country(self):
        for cls, country, host in (
            (HHRuScraper, "RU", "hh.ru"),
            (HHKzScraper, "KZ", "hh.kz"),
            (HHUzScraper, "UZ", "hh.uz"),
            (RabotaByScraper, "BY", "rabota.by"),
            (HeadHunterKgScraper, "KG", "headhunter.kg"),
        ):
            self.assertEqual(cls.countries, (country,))
            self.assertIn(host, cls.BASE_URL)
            self.assertTrue(cls.declares_full_capabilities())


# ---------------------------------------------------------------------------
# Engine + BrowserPool integration
# ---------------------------------------------------------------------------


class EngineBrowserDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_engine_dispatches_hh_ru_via_pool(self):
        dom = card_dom(
            {"title": "QA Engineer", "link_href": "https://hh.ru/vacancy/1", "company": "Acme"}
        )

        def page_factory():
            return FakePage(behaviour=PageBehaviour(dom=dom))

        def context_factory(**_kw):
            return FakeContext(page_factory=page_factory)

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))
        engine = SearchEngine(browser_pool=pool)

        with _RegistryContext({"hh_ru": HHRuScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await engine.execute(
                    _request(country="RU"), journal=journal, run_id="r-x"
                )
            self.assertEqual(len(result.listings), 1)
            self.assertEqual(result.listings[0].url, "https://hh.ru/vacancy/1")
            status = result.summary["source_statuses"][0]
            self.assertEqual(status["source"], "hh_ru")
            self.assertEqual(status["state"], SourceState.OK.value)
            self.assertEqual(status["transport"], Transport.BROWSER.value)
        engine.http_runner.shutdown()
        await pool.shutdown()

    async def test_anti_bot_title_marks_blocked(self):
        def page_factory():
            return FakePage(
                behaviour=PageBehaviour(title="Доступ ограничен")
            )

        def context_factory(**_kw):
            return FakeContext(page_factory=page_factory)

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))
        engine = SearchEngine(browser_pool=pool)
        with _RegistryContext({"hh_ru": HHRuScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await engine.execute(
                    _request(country="RU"), journal=journal, run_id="r-x"
                )
            status = result.summary["source_statuses"][0]
            self.assertEqual(status["state"], SourceState.BLOCKED.value)
            self.assertEqual(status["failure_mode"], FailureMode.ANTI_BOT_PAGE.value)
            self.assertEqual(status["anti_bot_signal"], "Доступ ограничен")
        engine.http_runner.shutdown()
        await pool.shutdown()

    async def test_hang_in_goto_marks_goto_timeout(self):
        def page_factory():
            return FakePage(
                behaviour=PageBehaviour(hang_seconds=10)
            )

        def context_factory(**_kw):
            return FakeContext(page_factory=page_factory)

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))
        engine = SearchEngine(browser_pool=pool)
        with _RegistryContext({"hh_ru": HHRuScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await engine.execute(
                    _request(country="RU", source_timeout_ms=200, total_timeout_ms=2000),
                    journal=journal, run_id="r-x",
                )
            status = result.summary["source_statuses"][0]
            self.assertEqual(status["state"], SourceState.TIMEOUT.value)
            self.assertEqual(status["failure_mode"], FailureMode.GOTO_TIMEOUT.value)
        engine.http_runner.shutdown()
        await pool.shutdown()

    async def test_no_pool_marks_browser_source_skipped(self):
        engine = SearchEngine(browser_pool=None)
        with _RegistryContext({"hh_ru": HHRuScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await engine.execute(
                    _request(country="RU"), journal=journal, run_id="r-x"
                )
            status = result.summary["source_statuses"][0]
            self.assertEqual(status["state"], SourceState.SKIPPED.value)
            self.assertEqual(status["failure_mode"], FailureMode.NOT_IN_PROFILE.value)
        engine.http_runner.shutdown()


if __name__ == "__main__":
    unittest.main()
