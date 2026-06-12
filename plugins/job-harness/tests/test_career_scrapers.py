"""Tests for the async per-company career scrapers (career:vk, career:ibs).

Both now live in the main registry and are dispatched through the
SearchEngine + BrowserPool, same as hh.ru. No separate BaseCareerScraper
inheritance — everything is BaseBrowserScraper.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests._support.fake_browser import (
    FakeBrowser,
    FakeContext,
    FakeElement,
    FakePage,
    PageBehaviour,
)

from job_harness import registry
from job_harness.browser_pool import BrowserPool
from job_harness.models import SearchParams
from job_harness.registry import _SCRAPERS, register_scraper
from job_harness.run_journal import RunJournalWriter
from job_harness.scrapers.career.ibs import IBSCareerScraper
from job_harness.scrapers.career.vk import VKCareerScraper
from job_harness.search_engine import SearchEngine
from job_harness.types import (
    SearchRequest,
    SourceState,
    Transport,
)


def _factory(browser: FakeBrowser):
    async def make():
        return browser

    return make


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
# VK
# ---------------------------------------------------------------------------


class VKScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_url_includes_specialty_for_qa_query(self):
        scraper = VKCareerScraper()
        url = scraper._build_url(SearchParams(query="qa engineer"))
        self.assertIn("specialty=284", url)
        self.assertNotIn("search=", url)

    async def test_url_includes_search_when_no_specialty_match(self):
        scraper = VKCareerScraper()
        url = scraper._build_url(SearchParams(query="exotic role"))
        self.assertIn("search=exotic", url)

    async def test_url_includes_remote_when_requested(self):
        scraper = VKCareerScraper()
        url = scraper._build_url(SearchParams(query="qa", remote_only=True))
        self.assertIn("remote=true", url)

    async def test_parses_next_data(self):
        # The fake page exposes __NEXT_DATA__ via page.evaluate.
        payload = {
            "props": {"pageProps": {"initialVacancies": [
                {"id": 100, "title": "QA Engineer", "group": {"name": "QA Team"},
                 "town": {"name": "Moscow"}, "remote": True,
                 "tags": [{"name": "python"}], "specialty": {"name": "qa"}},
            ]}}
        }

        class _Page(FakePage):
            async def evaluate(self, _script):
                import json
                return json.dumps(payload)

        page = _Page(behaviour=PageBehaviour())
        scraper = VKCareerScraper(max_results=10)
        listings = await scraper.search_with_page(page, SearchParams(query="qa"))
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].title, "QA Engineer")
        self.assertTrue(listings[0].remote)
        self.assertEqual(listings[0].skills, ("python",))

    async def test_falls_back_to_dom_when_next_data_missing(self):
        # No __NEXT_DATA__ → evaluate returns None.
        class _Page(FakePage):
            async def evaluate(self, _script):
                return None

        items_selector = "a.vacancy_vacancyItem__jrNqL"
        page = _Page(
            behaviour=PageBehaviour(
                dom={
                    items_selector: [
                        FakeElement(text="QA Lead", attrs={"href": "/vacancy/42/"}),
                    ]
                }
            )
        )
        scraper = VKCareerScraper(max_results=10)
        listings = await scraper.search_with_page(page, SearchParams(query="qa"))
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].url, "https://team.vk.company/vacancy/42/")
        self.assertIsNone(listings[0].experience)


# ---------------------------------------------------------------------------
# IBS
# ---------------------------------------------------------------------------


class IBSScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_qa_query_maps_to_testirovanie_segment(self):
        scraper = IBSCareerScraper()
        url = scraper._build_filter_url(SearchParams(query="QA"))
        self.assertIn("napravlenie-is-testirovanie", url)

    async def test_remote_only_adds_format_segment(self):
        scraper = IBSCareerScraper()
        url = scraper._build_filter_url(SearchParams(query="QA", remote_only=True))
        self.assertIn("format-is-online", url)

    async def test_location_maps_to_city_segment(self):
        scraper = IBSCareerScraper()
        url = scraper._build_filter_url(SearchParams(query="QA", location="Москва"))
        self.assertIn("gorod-is-moscow", url)

    async def test_no_match_falls_back_to_base_url(self):
        scraper = IBSCareerScraper()
        url = scraper._build_filter_url(SearchParams(query="completely unknown thing"))
        self.assertEqual(url, "https://ibs.ru/career/vacancies/")

    async def test_parses_jobs_item_cards(self):
        page = FakePage(
            behaviour=PageBehaviour(
                dom={
                    "a.jobs-item": [
                        FakeElement(
                            text="",
                            attrs={"href": "/career/vacancies/qa-engineer/"},
                            children={
                                ".jobs-item-title": [FakeElement(text="QA Engineer")],
                                ".jobs-item-tags": [FakeElement(text="python\nудаленно")],
                                ".jobs-item-desc": [FakeElement(text="Test our APIs")],
                            },
                        ),
                    ]
                }
            )
        )
        scraper = IBSCareerScraper(max_results=10)
        listings = await scraper.search_with_page(page, SearchParams(query="QA"))
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].title, "QA Engineer")
        self.assertEqual(listings[0].url, "https://ibs.ru/career/vacancies/qa-engineer/")
        self.assertEqual(listings[0].company, "IBS")
        self.assertTrue(listings[0].remote)
        self.assertIn("python", listings[0].skills)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


class RegistrySanityTest(unittest.TestCase):
    def test_career_scrapers_registered_in_main_registry(self):
        # Importing the package should have side-effect registered them.
        import job_harness.scrapers  # noqa: F401
        import job_harness.scrapers.career  # noqa: F401
        names = {n for n, _ in registry.iter_registered()}
        self.assertIn("career:vk", names)
        self.assertIn("career:ibs", names)

    def test_career_scrapers_have_browser_transport(self):
        import job_harness.scrapers.career  # noqa: F401
        for name in ("career:vk", "career:ibs"):
            cls = registry.get_scraper_class(name)
            self.assertEqual(cls.transport(), Transport.BROWSER)
            self.assertTrue(cls.declares_full_capabilities())


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


class EngineIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_engine_dispatches_career_ibs_via_pool(self):
        def page_factory():
            return FakePage(
                behaviour=PageBehaviour(
                    dom={
                        "a.jobs-item": [
                            FakeElement(
                                text="",
                                attrs={"href": "/career/vacancies/qa-engineer/"},
                                children={
                                    ".jobs-item-title": [FakeElement(text="QA Engineer")],
                                    ".jobs-item-tags": [FakeElement(text="")],
                                    ".jobs-item-desc": [FakeElement(text="")],
                                },
                            ),
                        ]
                    }
                )
            )

        def context_factory(**_kw):
            return FakeContext(page_factory=page_factory)

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))
        engine = SearchEngine(browser_pool=pool)
        with _RegistryContext({"career:ibs": IBSCareerScraper}), tempfile.TemporaryDirectory() as d:
            with RunJournalWriter(Path(d)) as journal:
                result = await engine.execute(
                    SearchRequest(query="QA", sources=("career:ibs",), country="RU"),
                    journal=journal, run_id="r-x",
                )
            self.assertEqual(len(result.listings), 1)
            self.assertEqual(result.listings[0].title, "QA Engineer")
            status = result.summary["source_statuses"][0]
            self.assertEqual(status["source"], "career:ibs")
            self.assertEqual(status["state"], SourceState.OK.value)
        engine.http_runner.shutdown()
        await pool.shutdown()


if __name__ == "__main__":
    unittest.main()
