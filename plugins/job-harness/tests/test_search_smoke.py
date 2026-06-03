from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_mcp_server():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mcp-server.py"
    spec = importlib.util.spec_from_file_location("job_harness_smoke_mcp_server", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HIREHI_BACKEND_HTML = """
<html><body>
  <a href="/development/backend-developer-50001">
    Backend Developer в HireHi Tech, 300 000 ₽, удалённо
  </a>
</body></html>
"""

STAFF_BACKEND_HTML = """
<html><body>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "jobs": [
        {
          "id": 201,
          "title": {"en": "Backend Engineer"},
          "slug": {"en": "backend-engineer"},
          "category": {"code": "software-development"},
          "companiesStruct": {"title": {"en": "Armenian Backend"}},
          "job_city": {"title": {"en": "Yerevan"}},
          "is_remote": false,
          "activated_at": {"staffam": "2026-06-02 10:00:00"}
        }
      ]
    }
  }
}
</script>
</body></html>
"""

GEEKJOB_BACKEND_HTML = """
<html><body>
  <a href="/vacancy/6a1eb96b6c029d292402f049">Ереван, Армения</a>
  <a href="/vacancy/6a1eb96b6c029d292402f049">Backend Developer</a>
  <a href="/vacancy/6a1eb96b6c029d292402f049">Fresh Talent</a>
</body></html>
"""

TALENTO_BACKEND_HTML = """
<html><body>
  <a aria-label="Talento Co: Backend Developer" href="/jobs/fa8eafd1-9c6b-4900-b905-b720bba90428"></a>
</body></html>
"""

JOBTURBO_BACKEND_HTML = """
<html><body>
<script type="application/ld+json">
{
  "@type": "ItemList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "url": "https://jobturbo.ru/vakansiya/35599", "name": "Backend Software Engineer"}
  ]
}
</script>
</body></html>
"""

HIRIFY_BACKEND_JSON = {
    "data": [
        {
            "id": 603300,
            "slug": "603300-backend-developer-phpsymfony",
            "title": "Backend Developer (PHP/Symfony)",
            "company_title": "Randstad",
            "work_format": ["remote"],
            "location": "Armenia",
            "updated_at": "2026-06-02T13:17:15.000000Z",
        }
    ]
}

FINDER_BACKEND_JSON = {
    "items": [
        {
            "id": 30240001,
            "title": "Backend Developer",
            "salary_from": 200000,
            "salary_to": 0,
            "currency_symbol": "RUR",
            "experience": "one_three_years",
            "short_description": "Backend services",
            "publication_at": "2026-06-02T01:03:19.955657Z",
            "distant_work": True,
            "locations": [{"name": "Москва"}],
            "company": {"title": "Backend Finder"},
        }
    ]
}

IT_JOBS_BACKEND_JSON = {
    "data": [
        {
            "id": "job-backend-1",
            "title": "Backend Developer",
            "slug": "backend-developer-uztech",
            "description": "Backend API development",
            "requirements": "Python",
            "companyName": "UzTech",
            "location": "Tashkent",
            "workType": "HYBRID",
            "experienceLevel": "MIDDLE",
            "salaryMin": 1000,
            "salaryMax": 2000,
            "salaryCurrency": "USD",
            "salaryPeriod": "month",
            "applyUrl": "https://example.test/apply",
            "tags": ["backend", "python"],
            "publishedAt": "2026-06-01T00:00:00.000Z",
            "category": {"slug": "backend"},
        }
    ]
}

GETMATCH_SPECIALIZATIONS = [
    {"id": 2, "name": "Backend", "slug": "backend", "category": {"name": "Development", "slug": "development"}},
]

GETMATCH_BACKEND_OFFERS = {
    "meta": {"total": 1},
    "offers": [
        {
            "id": 34580,
            "analytics_id": "NRXYe6vz",
            "position": "Backend Developer",
            "url": "/vacancies/34580-backend-developer",
            "company": {"name": "Getmatch Backend"},
            "salary_description": "от 250 000 ₽/мес",
            "location_items": [{"label": "Россия", "format": "remote"}],
            "location_requirements": [{"country": "Россия", "format": "remote"}],
            "skills_objects": [{"name": "Python"}],
            "offer_description": "Backend development",
            "published_at": "2026-06-02T07:43:52.546022",
        }
    ],
}


def fake_fetch_text(url: str, *, verify_ssl: bool = True) -> str:
    if "hirehi.ru" in url:
        return HIREHI_BACKEND_HTML
    if "staff.am" in url:
        return STAFF_BACKEND_HTML
    if "geekjob.ru" in url:
        return GEEKJOB_BACKEND_HTML
    if "talento.works" in url:
        return TALENTO_BACKEND_HTML
    if "jobturbo.ru" in url:
        return JOBTURBO_BACKEND_HTML
    raise AssertionError(f"Unexpected text fetch: {url}")


def fake_fetch_json(url: str) -> object:
    if "api.hirify.me" in url:
        return HIRIFY_BACKEND_JSON
    if "api.finder.work" in url:
        return FINDER_BACKEND_JSON
    if "it-jobs.uz" in url:
        return IT_JOBS_BACKEND_JSON
    if url == "https://getmatch.ru/api/specializations":
        return GETMATCH_SPECIALIZATIONS
    if "getmatch.ru/api/offers" in url:
        return GETMATCH_BACKEND_OFFERS
    raise AssertionError(f"Unexpected JSON fetch: {url}")


class SearchSmokeTest(unittest.TestCase):
    def test_new_non_browser_sources_work_together_through_mcp_search(self) -> None:
        server = _load_mcp_server()
        sources = ",".join([
            "hirehi",
            "hirify",
            "staff_am",
            "geekjob",
            "talento",
            "finder_work",
            "it_jobs_uz",
            "jobturbo",
            "getmatch",
        ])

        with (
            patch.object(server, "_ensure_browser", side_effect=AssertionError("browser not expected")),
            patch("job_harness.scrapers.cis_sources.fetch_text", side_effect=fake_fetch_text),
            patch("job_harness.scrapers.cis_sources.fetch_json", side_effect=fake_fetch_json),
        ):
            data = server._search_impl(query="Backend", sources=sources, max_results=20)

        self.assertEqual([], data["errors"])
        self.assertEqual(9, data["total"])
        self.assertEqual(
            {
                "hirehi",
                "hirify",
                "staff_am",
                "geekjob",
                "talento",
                "finder_work",
                "it_jobs_uz",
                "jobturbo",
                "getmatch",
            },
            {listing["source"] for listing in data["listings"]},
        )

    def test_country_filtered_all_sources_for_armenia_uses_only_non_browser_sources(self) -> None:
        server = _load_mcp_server()

        with (
            patch.object(server, "_ensure_browser", side_effect=AssertionError("browser not expected")),
            patch("job_harness.scrapers.cis_sources.fetch_text", side_effect=fake_fetch_text),
            patch("job_harness.scrapers.cis_sources.fetch_json", side_effect=fake_fetch_json),
        ):
            data = server._search_impl(query="Backend", sources="all", country="AM", max_results=20)

        self.assertEqual([], data["errors"])
        sources = {listing["source"] for listing in data["listings"]}
        self.assertIn("staff_am", sources)
        self.assertIn("hirify", sources)
        self.assertIn("getmatch", sources)
        self.assertNotIn("hirehi", sources)
        self.assertNotIn("it_jobs_uz", sources)

    def test_company_directory_source_works_through_mcp_search(self) -> None:
        server = _load_mcp_server()

        with patch.object(server, "_ensure_browser", side_effect=AssertionError("browser not expected")):
            data = server._search_impl(
                query="QA",
                sources="company_directory",
                location="Armenia",
                max_results=10,
            )

        self.assertEqual([], data["errors"])
        self.assertGreater(data["total"], 0)
        self.assertEqual({"company_directory"}, {listing["source"] for listing in data["listings"]})
        self.assertIn("Miro", [listing["company"] for listing in data["listings"]])


if __name__ == "__main__":
    unittest.main()
