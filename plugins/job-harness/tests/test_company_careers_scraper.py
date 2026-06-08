from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests._support.fake_browser import FakeBrowser, FakeContext, FakePage

from job_harness.browser_pool import BrowserPool
from job_harness.company_directory import CompanyProfile
from job_harness.models import SearchParams
from job_harness.registry import _SCRAPERS, register_scraper
from job_harness.run_journal import RunJournalWriter
from job_harness.scrapers.company_careers import CompanyCareersScraper
from job_harness.search_engine import SearchEngine
from job_harness.types import SearchRequest, SourceState


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


def _record(company: str = "Alpha", title: str = "Middle QA Engineer") -> dict:
    slug = company.casefold()
    return {
        "company": company,
        "status": "ok",
        "method": "ats_api",
        "careers_url": f"https://{slug}.test/careers",
        "hit_count": 1,
        "hits": [
            {
                "company": company,
                "title": title,
                "vacancy_url": f"https://{slug}.test/jobs/middle-qa",
                "careers_url": f"https://{slug}.test/careers",
                "matched_text": title,
                "score": 4,
                "countries": ["RU"],
                "stack": ["QA", "Python"],
                "job_types": ["QA"],
                "remote_match": None,
            }
        ],
    }


class CompanyCareersScraperTest(unittest.IsolatedAsyncioTestCase):
    async def test_converts_company_hits_to_listings_without_native_experience(self) -> None:
        company = CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers")
        with (
            patch("job_harness.scrapers.company_careers._load_company_targets", return_value=[company]),
            patch("job_harness.scrapers.company_careers._check_company", new_callable=AsyncMock) as check,
        ):
            check.return_value = _record()
            scraper = CompanyCareersScraper(max_results=5, timeout_ms=5_000)

            listings = await scraper.search_with_page(FakePage(), SearchParams(query="QA", country="RU"))

        self.assertEqual(1, len(listings))
        listing = listings[0]
        self.assertEqual("company_careers", listing.source)
        self.assertEqual("Middle QA Engineer", listing.title)
        self.assertEqual("https://alpha.test/jobs/middle-qa", listing.url)
        self.assertIsNone(listing.experience)
        self.assertEqual("Middle QA Engineer", listing.description)
        self.assertEqual(["QA", "Python"], listing.skills)
        self.assertEqual("ats_api", listing.raw["method"])

    async def test_scraping_scope_is_not_capped_by_requested_result_limit(self) -> None:
        companies = [
            CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers"),
            CompanyProfile(name="Beta", careers_url="https://beta.test/careers"),
        ]
        with (
            patch("job_harness.scrapers.company_careers._load_company_targets", return_value=companies),
            patch("job_harness.scrapers.company_careers._check_company", new_callable=AsyncMock) as check,
        ):
            check.side_effect = [_record("Alpha"), _record("Beta", "Senior QA Engineer")]
            scraper = CompanyCareersScraper(max_results=1, timeout_ms=5_000)

            listings = await scraper.search_with_page(FakePage(), SearchParams(query="QA", country="RU"))

        self.assertEqual(2, check.await_count)
        self.assertEqual(["Alpha", "Beta"], [listing.company for listing in listings])

    async def test_engine_runs_company_careers_and_applies_grade_engine(self) -> None:
        company = CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers")
        browser = FakeBrowser(context_factory=lambda **_kw: FakeContext(page_factory=FakePage))
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))
        engine = SearchEngine(browser_pool=pool)

        with (
            _RegistryContext({"company_careers": CompanyCareersScraper}),
            tempfile.TemporaryDirectory() as d,
            patch("job_harness.scrapers.company_careers._load_company_targets", return_value=[company]),
            patch("job_harness.scrapers.company_careers._check_company", new_callable=AsyncMock) as check,
        ):
            check.return_value = _record()
            with RunJournalWriter(Path(d)) as journal:
                result = await engine.execute(
                    SearchRequest(
                        query="QA",
                        sources=("company_careers",),
                        country="RU",
                        experience_levels=("middle",),
                    ),
                    journal=journal,
                    run_id="r-company-careers",
                )

        self.assertEqual(1, len(result.listings))
        listing = result.listings[0]
        self.assertEqual(["middle"], listing.experience_levels)
        self.assertEqual("estimated", listing.experience_origin)
        self.assertIsNone(listing.experience)
        status = result.summary["source_statuses"][0]
        self.assertEqual("company_careers", status["source"])
        self.assertEqual(SourceState.OK.value, status["state"])
        self.assertEqual(
            "grade_engine",
            result.summary["flag_enforcement"]["experience"]["by_source"]["company_careers"]["applied_by"],
        )
        engine.http_runner.shutdown()
        await pool.shutdown()


if __name__ == "__main__":
    unittest.main()
