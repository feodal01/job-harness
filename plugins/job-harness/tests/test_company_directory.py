from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import job_harness.scrapers  # noqa: F401
from job_harness.company_directory import (
    load_company_directory,
    normalize_company_key,
    search_company_directory,
)
from job_harness.models import SearchParams
from job_harness.registry import create_scraper, get_scraper_metadata


class CompanyDirectoryTest(unittest.TestCase):
    def test_bundled_directory_contains_unique_companies_and_rich_fields(self) -> None:
        profiles = load_company_directory()
        keys = [normalize_company_key(profile.name) for profile in profiles]
        miro = next(profile for profile in profiles if profile.name == "Miro")

        self.assertEqual(410, len(profiles))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(1, sum(key == "coding invaders" for key in keys))
        self.assertEqual("https://miro.com/careers/open-positions/", miro.careers_url)
        self.assertIn("QA", miro.job_types)
        self.assertIn("Armenia", miro.countries)

    def test_search_company_directory_matches_role_and_country(self) -> None:
        results = search_company_directory("QA", country="Armenia", max_results=10)

        self.assertIn("Miro", [profile.name for profile in results])
        self.assertTrue(all("Armenia" in profile.countries for profile in results))

    def test_search_company_directory_filters_by_stack(self) -> None:
        results = search_company_directory("backend", stack="Python", max_results=10)

        self.assertGreater(len(results), 0)
        self.assertTrue(all("Python" in profile.stack for profile in results))

    def test_loader_rejects_invalid_directory_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.json"
            path.write_text(json.dumps({"name": "Not a list"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must be a JSON list"):
                load_company_directory(path)

    def test_company_directory_is_registered_as_non_browser_scraper(self) -> None:
        metadata = get_scraper_metadata()["company_directory"]

        self.assertFalse(metadata["requires_browser"])
        self.assertFalse(metadata["detail_requires_browser"])

    def test_company_directory_scraper_returns_career_entrypoints(self) -> None:
        scraper = create_scraper("company_directory", context=None, max_results=5)

        listings = scraper.search(SearchParams(query="QA", location="Armenia", max_results=5))

        self.assertGreater(len(listings), 0)
        self.assertTrue(all(listing.source == "company_directory" for listing in listings))
        self.assertTrue(all(listing.url.startswith("https://") for listing in listings))
        self.assertIn("careers_url", listings[0].raw)


if __name__ == "__main__":
    unittest.main()
