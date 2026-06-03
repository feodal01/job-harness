from __future__ import annotations

import unittest

import job_harness.scrapers  # noqa: F401
from job_harness.countries import CIS_COUNTRIES, normalize_country_code
from job_harness.registry import get_scraper_metadata, list_scrapers


class CountriesAndRegistryTest(unittest.TestCase):
    def test_cis_directory_contains_active_associate_and_former_countries(self) -> None:
        countries = {country.code: country.status for country in CIS_COUNTRIES}

        self.assertEqual("member", countries["RU"])
        self.assertEqual("member", countries["AM"])
        self.assertEqual("member", countries["UZ"])
        self.assertEqual("associate", countries["TM"])
        self.assertEqual("former", countries["GE"])
        self.assertEqual("former", countries["UA"])
        self.assertEqual(12, len(countries))

    def test_country_normalization_accepts_codes_and_russian_names(self) -> None:
        self.assertEqual("RU", normalize_country_code("РФ"))
        self.assertEqual("AM", normalize_country_code("Армения"))
        self.assertEqual("KZ", normalize_country_code("kazakhstan"))

    def test_country_normalization_rejects_unknown_country(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown CIS country"):
            normalize_country_code("Germany")

    def test_registry_filters_sources_by_country(self) -> None:
        armenia_sources = set(list_scrapers(country="AM"))
        russia_sources = set(list_scrapers(country="RU"))

        self.assertIn("staff_am", armenia_sources)
        self.assertIn("talento", armenia_sources)
        self.assertNotIn("hirehi", armenia_sources)
        self.assertIn("hirehi", russia_sources)
        self.assertIn("habr_career", russia_sources)
        self.assertIn("hh_kz", list_scrapers(country="KZ"))
        self.assertIn("hh_uz", list_scrapers(country="UZ"))
        self.assertIn("rabota_by", list_scrapers(country="BY"))
        self.assertIn("headhunter_kg", list_scrapers(country="KG"))

    def test_source_metadata_exposes_countries(self) -> None:
        metadata = get_scraper_metadata()

        self.assertEqual(["RU"], metadata["hirehi"]["countries"])
        self.assertEqual(["AM"], metadata["staff_am"]["countries"])


if __name__ == "__main__":
    unittest.main()
