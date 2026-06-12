from __future__ import annotations

import unittest
from unittest.mock import patch

from job_harness.models import SearchParams
from job_harness.scrapers.cis_sources import (
    FinderWorkScraper,
    GeekJobScraper,
    GetmatchScraper,
    HireHiScraper,
    HirifyScraper,
    ItJobsUzScraper,
    JobTurboScraper,
    StaffAmScraper,
    TalentoScraper,
)

HIREHI_HTML = """
<html><body>
  <a href="/qa/automation-qa-48400">
    Automation QA в Test Labs, ~ 250 000 ₽, удалённо
  </a>
  <a href="/devops/support-engineer-48424">
    Middle Support Engineer в Infra Team, 150 000 ₽, удалённо
  </a>
  <a href="/development/c-developer-48427">
    Senior C# Developer в Game Studio, 280 000 ₽, удалённо
  </a>
  <a href="/vacancies/qa">тестировщикам</a>
</body></html>
"""

HIRIFY_JSON = {
    "data": [
        {
            "id": 603209,
            "slug": "603209-senior-qa-automation-engineer-fintech",
            "title": "Senior QA Automation Engineer (Fintech)",
            "company_title": "Company hidden",
            "work_format": ["remote"],
            "location": "Armenia",
            "salary_from": 4000,
            "salary_to": 6000,
            "currency": "USD",
            "grade": "senior",
            "updated_at": "2026-06-02T12:50:26.000000Z",
        }
    ]
}

STAFF_HTML = """
<html><body>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "jobs": [
        {
          "id": 100,
          "title": {"en": "Promoted Company"},
          "companiesStruct": {"title": {"en": "No URL Parts"}}
        },
        {
          "id": 101,
          "title": {"en": "Senior QA Engineer", "ru": "Старший QA инженер"},
          "slug": {"en": "senior-qa-engineer"},
          "category": {"code": "quality-assurance"},
          "companiesStruct": {"title": {"en": "Armenian Tech"}},
          "job_city": {"title": {"en": "Yerevan"}},
          "is_remote": true,
          "activated_at": {"staffam": "2026-06-02 10:00:00"}
        },
        {
          "id": 102,
          "title": {"en": "Accountant"},
          "slug": {"en": "accountant"},
          "category": {"code": "finance"},
          "companiesStruct": {"title": {"en": "Finance LLC"}},
          "is_remote": false,
          "activated_at": {"staffam": "2026-06-01 10:00:00"}
        }
      ]
    }
  }
}
</script>
</body></html>
"""

GEEKJOB_HTML = """
<html><body>
  <a href="/vacancy/6a1eb96b6c029d292402f049">Ереван, Армения от 500K ₽</a>
  <a href="/vacancy/6a1eb96b6c029d292402f049">Security Researcher /Reverse Engineer (Senior/Lead)</a>
  <a href="/vacancy/6a1eb96b6c029d292402f049">Fresh Talent</a>
  <a href="/vacancy/6a1eb96b6c029d292402f049">2 июня</a>
</body></html>
"""

TALENTO_HTML = """
<html><body>
  <article class="vacancy-card">
    <a aria-label="Т-Банк: QA Automation Engineer" href="/jobs/fa8eafd1-9c6b-4900-b905-b720bba90428"></a>
  </article>
</body></html>
"""

FINDER_JSON = {
    "items": [
        {
            "id": 30243980,
            "title": "QA Engineer",
            "salary_from": 150000,
            "salary_to": 0,
            "currency_symbol": "RUR",
            "experience": "three_years_more",
            "short_description": "Должность: <mark>QA</mark>-инженер",
            "publication_at": "2026-06-02T01:03:19.955657Z",
            "distant_work": True,
            "external_url": {"label": "hh.ru", "value": "https://hh.ru/vacancy/1"},
            "locations": [{"name": "Москва"}],
            "company": {"title": "Дженикс"},
        }
    ]
}

IT_JOBS_UZ_JSON = {
    "data": [
        {
            "id": "job-1",
            "title": "Middle QA Engineer",
            "slug": "middle-qa-engineer-company",
            "description": "Test web and mobile products",
            "requirements": "API testing",
            "companyName": "UzTech",
            "location": "Tashkent",
            "workType": "REMOTE",
            "experienceLevel": "MIDDLE",
            "salaryMin": 1000,
            "salaryMax": 2000,
            "salaryCurrency": "USD",
            "salaryPeriod": "month",
            "applyUrl": "https://example.test/apply",
            "tags": ["qa", "api"],
            "publishedAt": "2026-06-01T00:00:00.000Z",
        }
    ]
}

JOBTURBO_HTML = """
<html><body>
<script type="application/ld+json">
{
  "@type": "ItemList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "url": "https://jobturbo.ru/vakansiya/35564", "name": "QA Engineer"},
    {"@type": "ListItem", "position": 2, "url": "https://jobturbo.ru/vakansiya/35563", "name": "Load QA, Senior и выше"}
  ]
}
</script>
</body></html>
"""

GETMATCH_SPECIALIZATIONS = [
    {"id": 11, "name": "QA Auto", "slug": "qa_auto", "category": {"name": "QA", "slug": "qa"}},
    {"id": 8, "name": "QA Manual", "slug": "qa_manual", "category": {"name": "QA", "slug": "qa"}},
    {"id": 2, "name": "Backend", "slug": "backend", "category": {"name": "Development", "slug": "development"}},
]

GETMATCH_OFFERS = {
    "meta": {"total": 1},
    "offers": [
        {
            "id": 29886,
            "analytics_id": "KvoBw4R0",
            "position": "Старший инженер по автоматизации тестирования (Python, API)",
            "url": "/vacancies/29886-starshii-inzhener-po-avtomatizatsii-testirovaniia-python",
            "company": {"name": "Ozon Банк"},
            "salary_description": "от 365 000 ₽/мес до налогов",
            "location_items": [{"label": "Россия", "format": "remote"}],
            "location_requirements": [{"country": "Россия", "format": "remote"}],
            "skills_objects": [{"name": "PyTest"}, {"name": "Docker"}],
            "offer_description": "<b>Что делать:</b> Улучшать процессы тестирования",
            "published_at": "2026-06-02T07:43:52.546022",
        }
    ],
}


class CisSourcesTest(unittest.TestCase):
    def test_hirehi_parses_search_results(self) -> None:
        scraper = HireHiScraper(context=None, max_results=5)

        listings = scraper._parse_search_results(HIREHI_HTML, SearchParams(query="QA", country="RU"))

        self.assertEqual(3, len(listings))
        self.assertEqual("Automation QA", listings[0].title)
        self.assertEqual("Test Labs", listings[0].company)
        self.assertEqual("RU", listings[0].country)
        self.assertEqual("250 000 ₽", listings[0].salary)
        self.assertIsNone(listings[0].experience)
        self.assertTrue(listings[0].remote)

    def test_hirehi_search_filters_by_any_it_role_not_only_qa(self) -> None:
        scraper = HireHiScraper(context=None, max_results=5)

        with patch("job_harness.scrapers.cis_sources.fetch_text", return_value=HIREHI_HTML):
            listings = scraper.search(SearchParams(query="DevOps", country="RU"))

        self.assertEqual(1, len(listings))
        self.assertEqual("Middle Support Engineer", listings[0].title)
        self.assertIn("/devops/", listings[0].url)
        self.assertIsNone(listings[0].experience)

    def test_hirify_maps_api_response(self) -> None:
        scraper = HirifyScraper(context=None, max_results=5)

        with patch("job_harness.scrapers.cis_sources.fetch_json", return_value=HIRIFY_JSON):
            listings = scraper.search(SearchParams(query="QA", country="AM"))

        self.assertEqual(1, len(listings))
        self.assertEqual("Senior QA Automation Engineer (Fintech)", listings[0].title)
        self.assertEqual("AM", listings[0].country)
        self.assertEqual("4000 - 6000 USD", listings[0].salary)
        self.assertTrue(listings[0].remote)
        self.assertIsNone(listings[0].experience)

    def test_hirify_search_url_uses_salary_from(self) -> None:
        scraper = HirifyScraper(context=None, max_results=5)
        url = scraper._build_search_url(SearchParams(query="QA", salary_from=4000))
        self.assertIn("salary_from=4000", url)

    def test_hirify_reads_nested_company_and_marks_missing_company(self) -> None:
        scraper = HirifyScraper(context=None, max_results=5)
        payload = {
            "data": [
                {
                    "id": 1,
                    "slug": "qa-nested-company",
                    "title": "QA Engineer",
                    "company_title": "%hirify_global%",
                    "company": {"name": "Nested Co"},
                },
                {
                    "id": 2,
                    "slug": "qa-missing-company",
                    "title": "QA Lead",
                    "company_title": "%hirify_global%",
                },
            ]
        }

        with patch("job_harness.scrapers.cis_sources.fetch_json", return_value=payload):
            listings = scraper.search(SearchParams(query="QA", country="AM"))

        self.assertEqual("Nested Co", listings[0].company)
        self.assertFalse(listings[0].raw.get("company_missing", False))
        self.assertEqual("", listings[1].company)
        self.assertTrue(listings[1].raw["company_missing"])

    def test_staff_am_extracts_next_data_jobs_and_filters_by_query(self) -> None:
        scraper = StaffAmScraper(context=None, max_results=5)

        with patch("job_harness.scrapers.cis_sources.fetch_text", return_value=STAFF_HTML) as fetch:
            listings = scraper.search(SearchParams(query="QA", country="AM"))

        self.assertEqual("https://staff.am/en/jobs/quality-assurance", fetch.call_args.args[0])
        self.assertEqual(1, len(listings))
        self.assertEqual("Senior QA Engineer", listings[0].title)
        self.assertEqual("Armenian Tech", listings[0].company)
        self.assertEqual("AM", listings[0].country)
        self.assertEqual("Yerevan", listings[0].location)
        self.assertTrue(listings[0].remote)

    def test_geekjob_groups_multiple_links_for_same_vacancy(self) -> None:
        scraper = GeekJobScraper(context=None, max_results=5)

        listings = scraper._parse_search_results(GEEKJOB_HTML, SearchParams(query="security", country="AM"))

        self.assertEqual(1, len(listings))
        self.assertEqual("Security Researcher /Reverse Engineer (Senior/Lead)", listings[0].title)
        self.assertEqual("Fresh Talent", listings[0].company)
        self.assertEqual("AM", listings[0].country)
        self.assertEqual("от 500K ₽", listings[0].salary)
        self.assertIsNone(listings[0].experience)

    def test_talento_uses_aria_label_for_title_and_company(self) -> None:
        scraper = TalentoScraper(context=None, max_results=5)

        listings = scraper._parse_search_results(TALENTO_HTML, SearchParams(query="QA", country="RU"))

        self.assertEqual(1, len(listings))
        self.assertEqual("QA Automation Engineer", listings[0].title)
        self.assertEqual("Т-Банк", listings[0].company)
        self.assertEqual("RU", listings[0].country)
        self.assertIsNone(listings[0].experience)

    def test_finder_work_maps_api_response(self) -> None:
        scraper = FinderWorkScraper(context=None, max_results=5)

        with patch("job_harness.scrapers.cis_sources.fetch_json", return_value=FINDER_JSON):
            listings = scraper.search(SearchParams(query="QA", country="RU"))

        self.assertEqual(1, len(listings))
        self.assertEqual("QA Engineer", listings[0].title)
        self.assertEqual("Дженикс", listings[0].company)
        self.assertEqual("RU", listings[0].country)
        self.assertEqual("from 150000 RUR", listings[0].salary)
        self.assertEqual("senior", listings[0].experience)
        self.assertTrue(listings[0].remote)
        self.assertEqual("https://hh.ru/vacancy/1", listings[0].raw["external_url"])

    def test_finder_work_search_url_uses_salary_from(self) -> None:
        scraper = FinderWorkScraper(context=None, max_results=5)
        with patch("job_harness.scrapers.cis_sources.fetch_json", return_value={"items": []}) as fetch:
            scraper.search(SearchParams(query="QA", country="RU", salary_from=200000))
        self.assertIn("salary_from=200000", fetch.call_args.args[0])

    def test_it_jobs_uz_maps_api_response(self) -> None:
        scraper = ItJobsUzScraper(context=None, max_results=5)

        with patch("job_harness.scrapers.cis_sources.fetch_json", return_value=IT_JOBS_UZ_JSON) as fetch:
            listings = scraper.search(SearchParams(query="QA", country="UZ"))

        self.assertIn("category=qa", fetch.call_args.args[0])
        self.assertEqual(1, len(listings))
        self.assertEqual("Middle QA Engineer", listings[0].title)
        self.assertEqual("UzTech", listings[0].company)
        self.assertEqual("UZ", listings[0].country)
        self.assertEqual("1000 - 2000 USD/month", listings[0].salary)
        self.assertEqual("middle", listings[0].experience)
        self.assertTrue(listings[0].remote)

    def test_it_jobs_uz_routes_common_it_roles_to_categories(self) -> None:
        scraper = ItJobsUzScraper(context=None, max_results=5)

        cases = {
            "Frontend React": "category=frontend",
            "DevOps Kubernetes": "category=devops",
            "Python backend": "category=backend",
            "Product manager": "category=pm",
            "Security engineer": "category=security",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertIn(expected, scraper._build_search_url(SearchParams(query=query, country="UZ")))

    def test_it_jobs_uz_search_url_uses_salary_min(self) -> None:
        scraper = ItJobsUzScraper(context=None, max_results=5)
        url = scraper._build_search_url(SearchParams(query="QA", salary_from=3000))
        self.assertIn("salaryMin=3000", url)

    def test_jobturbo_extracts_json_ld_item_list(self) -> None:
        scraper = JobTurboScraper(context=None, max_results=5)

        with patch("job_harness.scrapers.cis_sources.fetch_text", return_value=JOBTURBO_HTML):
            listings = scraper.search(SearchParams(query="QA", country="RU"))

        self.assertEqual(2, len(listings))
        self.assertEqual("QA Engineer", listings[0].title)
        self.assertEqual("https://jobturbo.ru/vakansiya/35564", listings[0].url)
        self.assertEqual("RU", listings[0].country)
        self.assertTrue(listings[0].remote)
        self.assertIsNone(listings[1].experience)

    def test_getmatch_uses_matching_specialization_and_maps_offer(self) -> None:
        scraper = GetmatchScraper(context=None, max_results=5)

        def fake_fetch_json(url: str, *, timeout_seconds=None):
            if url == scraper.SPECIALIZATIONS_URL:
                return GETMATCH_SPECIALIZATIONS
            self.assertIn("sp=qa_auto", url)
            return GETMATCH_OFFERS

        with patch("job_harness.scrapers.cis_sources.fetch_json", side_effect=fake_fetch_json):
            listings = scraper.search(SearchParams(query="QA Auto", country="RU"))

        self.assertEqual(1, len(listings))
        self.assertEqual("Старший инженер по автоматизации тестирования (Python, API)", listings[0].title)
        self.assertEqual("Ozon Банк", listings[0].company)
        self.assertEqual("RU", listings[0].country)
        self.assertEqual("от 365 000 ₽/мес до налогов", listings[0].salary)
        self.assertIsNone(listings[0].experience)
        self.assertTrue(listings[0].remote)
        self.assertEqual(("PyTest", "Docker"), listings[0].skills)

    def test_getmatch_matches_non_qa_it_specializations(self) -> None:
        scraper = GetmatchScraper(context=None, max_results=5)

        with patch("job_harness.scrapers.cis_sources.fetch_json", return_value=GETMATCH_SPECIALIZATIONS):
            slugs = scraper._matching_specialization_slugs("Backend")

        self.assertEqual(["backend"], slugs)


if __name__ == "__main__":
    unittest.main()
