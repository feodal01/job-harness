from __future__ import annotations

import unittest
from unittest.mock import patch

from job_harness.models import RawListing, SearchParams
from job_harness.scrapers.habr_career import HabrCareerScraper

LIST_PAGE_1 = """
<html><body>
<div class="vacancy-card">
  <div class="vacancy-card__company"><a href="/companies/ninsar">Ninsar</a></div>
  <a class="vacancy-card__title-link" href="/vacancies/1000166719">Lead QA Engineer</a>
  <div class="basic-salary">200 000 ₽</div>
  <div class="chip-with-icon__text">Senior</div>
  <div class="chip-with-icon__text">Можно удалённо</div>
  <div class="vacancy-card__skills-chip"><span class="basic-chip__text">TestRail</span></div>
  <div class="vacancy-card__skills-chip"><span class="basic-chip__text">SQL</span></div>
</div>
<a rel="next" href="/vacancies?page=2&q=QA&type=all">Далее</a>
</body></html>
"""

LIST_PAGE_2 = """
<html><body>
<div class="vacancy-card">
  <div class="vacancy-card__company"><a href="/companies/bank">Банк России</a></div>
  <a class="vacancy-card__title-link" href="/vacancies/1000162655">QA Automation Engineer / SDET</a>
  <div class="chip-with-icon__text">Middle</div>
  <div class="vacancy-card__skills-chip"><span class="basic-chip__text">Python</span></div>
</div>
</body></html>
"""

DETAIL_PAGE = """
<html><body>
<div class="vacancy-description__text">
  <h3>О компании:</h3>
  <p>Мы разрабатываем технологичные продукты.</p>
  <p>Требуется сильный QA инженер.</p>
</div>
</body></html>
"""


class _ForbiddenBrowserContext:
    def new_page(self):  # pragma: no cover - failure path only
        raise AssertionError("Habr search/detail must not require Playwright")


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class HabrCareerScraperTest(unittest.TestCase):
    def test_parse_search_results_extracts_core_listing_fields(self) -> None:
        scraper = HabrCareerScraper(context=_ForbiddenBrowserContext(), max_results=10)

        listings, next_url = scraper._parse_search_results(
            LIST_PAGE_1,
            "https://career.habr.com/vacancies?q=QA&type=all",
        )

        self.assertEqual(1, len(listings))
        listing = listings[0]
        self.assertEqual("Lead QA Engineer", listing.title)
        self.assertEqual("https://career.habr.com/vacancies/1000166719", listing.url)
        self.assertEqual("Ninsar", listing.company)
        self.assertEqual("200 000 ₽", listing.salary)
        self.assertEqual("senior", listing.experience)
        self.assertTrue(listing.remote)
        self.assertEqual(("TestRail", "SQL"), listing.skills)
        self.assertEqual(
            "https://career.habr.com/vacancies?page=2&q=QA&type=all",
            next_url,
        )

    def test_search_paginates_without_playwright(self) -> None:
        scraper = HabrCareerScraper(context=_ForbiddenBrowserContext(), max_results=2)
        pages = [LIST_PAGE_1, LIST_PAGE_2]

        def fake_fetch(url: str) -> str:
            return pages.pop(0)

        with patch.object(scraper, "_fetch_html", side_effect=fake_fetch) as fetch:
            listings = scraper.search(SearchParams(query="QA", max_results=2))

        self.assertEqual(2, len(listings))
        self.assertEqual("Lead QA Engineer", listings[0].title)
        self.assertEqual("QA Automation Engineer / SDET", listings[1].title)
        self.assertEqual(2, fetch.call_count)

    def test_search_url_uses_qualification_for_single_exact_level(self) -> None:
        scraper = HabrCareerScraper(context=_ForbiddenBrowserContext())
        url = scraper._build_search_url(
            SearchParams(query="QA", experience_levels=("middle",))
        )
        self.assertIn("qualification=middle", url)

    def test_search_url_uses_salary_param(self) -> None:
        scraper = HabrCareerScraper(context=_ForbiddenBrowserContext())
        url = scraper._build_search_url(SearchParams(query="QA", salary_from=200000))
        self.assertIn("salary=200000", url)

    def test_search_url_does_not_guess_qualification_for_multi_level(self) -> None:
        scraper = HabrCareerScraper(context=_ForbiddenBrowserContext())
        url = scraper._build_search_url(
            SearchParams(query="QA", experience_levels=("middle", "senior"))
        )
        self.assertNotIn("qualification=", url)

    def test_fetch_detail_extracts_description_without_playwright(self) -> None:
        scraper = HabrCareerScraper(context=_ForbiddenBrowserContext(), max_results=1)
        listing = RawListing(
            title="Lead QA Engineer",
            url="https://career.habr.com/vacancies/1000166719",
            company="Ninsar",
            source="habr_career",
        )

        with patch.object(scraper, "_fetch_html", return_value=DETAIL_PAGE):
            detailed = scraper.fetch_detail(listing)

        self.assertIn("Мы разрабатываем технологичные продукты.", detailed.description or "")
        self.assertIn("Требуется сильный QA инженер.", detailed.description or "")
        self.assertEqual(listing.url, detailed.url)

    def test_fetch_html_retries_transient_timeouts(self) -> None:
        scraper = HabrCareerScraper(context=_ForbiddenBrowserContext(), max_results=1)
        responses = [
            TimeoutError("first timeout"),
            TimeoutError("second timeout"),
            _FakeResponse(LIST_PAGE_1),
        ]

        def fake_urlopen(request, timeout):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with patch("job_harness.scrapers.habr_career.urlopen", side_effect=fake_urlopen) as urlopen:
            html = scraper._fetch_html("https://career.habr.com/vacancies?q=QA&type=all")

        self.assertIn("Lead QA Engineer", html)
        self.assertEqual(3, urlopen.call_count)


if __name__ == "__main__":
    unittest.main()
