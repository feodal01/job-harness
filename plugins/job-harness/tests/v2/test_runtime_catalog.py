from __future__ import annotations

import unittest

from tests.v2._support.contract_runtime import FakeScraper, descriptor, listing, supported

from job_harness.v2.contracts import SearchRequest, SourceType
from job_harness.v2.runtime import SourceCatalog


class SourceCatalogTest(unittest.TestCase):
    def test_rejects_duplicate_source_ids(self) -> None:
        # Arrange
        scraper = FakeScraper(
            source_descriptor=descriptor("hh_ru"),
            raw_listings=(listing("hh_ru"),),
        )

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "duplicate source_id"):
            SourceCatalog((supported(scraper), supported(scraper)))

    def test_selects_by_source_id_and_type(self) -> None:
        # Arrange
        hh = FakeScraper(
            source_descriptor=descriptor("hh_ru", source_type=SourceType.AGGREGATOR, countries=("RU",)),
            raw_listings=(listing("hh_ru"),),
        )
        career = FakeScraper(
            source_descriptor=descriptor(
                "career:acme",
                source_type=SourceType.COMPANY_CAREER,
                countries=("US",),
            ),
            raw_listings=(listing("career:acme"),),
        )
        catalog = SourceCatalog((supported(hh), supported(career)))
        request = SearchRequest(
            query_variants=("QA",),
            source_types=(SourceType.COMPANY_CAREER,),
            vacancy_geographies=("country:US",),
        )

        # Act
        selected = catalog.select(request)

        # Assert
        self.assertEqual(("career:acme",), tuple(scraper.descriptor.source_id for scraper in selected))

    def test_rejects_unknown_requested_source(self) -> None:
        # Arrange
        hh = FakeScraper(
            source_descriptor=descriptor("hh_ru"),
            raw_listings=(listing("hh_ru"),),
        )
        catalog = SourceCatalog((supported(hh),))
        request = SearchRequest(query_variants=("QA",), sources=("missing",))

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "unknown sources: missing"):
            catalog.select(request)

    def test_exclude_companies_removes_matching_company_source(self) -> None:
        # Arrange
        hh = FakeScraper(
            source_descriptor=descriptor("hh_ru", source_type=SourceType.AGGREGATOR),
            raw_listings=(listing("hh_ru"),),
        )
        career = FakeScraper(
            source_descriptor=descriptor("career:acme", source_type=SourceType.COMPANY_CAREER),
            raw_listings=(listing("career:acme"),),
        )
        catalog = SourceCatalog((supported(hh), supported(career)))
        request = SearchRequest(query_variants=("QA",), exclude_companies=("ACME",))

        # Act
        selected = catalog.select(request)

        # Assert
        self.assertEqual(("hh_ru",), tuple(scraper.descriptor.source_id for scraper in selected))


if __name__ == "__main__":
    unittest.main()
