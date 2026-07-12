from __future__ import annotations

import unittest

from job_harness.v2.contracts import ParserType
from job_harness.v2.runtime import (
    build_independent_parser_registry,
    build_supported_source_catalog,
    implemented_source_ids,
)
from job_harness.v2.source_catalog import source_catalog_entries


class RuntimeSourceRegistryTest(unittest.TestCase):
    def test_independent_registry_has_one_search_bundle_per_source(self) -> None:
        registry = build_independent_parser_registry()

        search_manifests = tuple(
            manifest
            for manifest in registry.manifests()
            if manifest.parser_type == ParserType.SEARCH_LISTING
        )

        self.assertEqual(
            tuple(manifest.parser_id for manifest in search_manifests),
            tuple(f"{source_id}.search" for source_id in implemented_source_ids()),
        )

    def test_independent_registry_exposes_each_scraper_type_separately(self) -> None:
        registry = build_independent_parser_registry(("hh_ru",))

        self.assertEqual(
            tuple(manifest.parser_type for manifest in registry.manifests()),
            (
                ParserType.SEARCH_LISTING,
                ParserType.VACANCY_DETAIL,
                ParserType.COMPANY_PROFILE,
                ParserType.COMPANY_SITE,
            ),
        )

    def test_implemented_sources_match_catalog_rows(self) -> None:
        # Arrange
        catalog_ids = tuple(entry.source_id for entry in source_catalog_entries())

        # Act
        catalog = build_supported_source_catalog()

        # Assert
        self.assertEqual(catalog_ids, implemented_source_ids())
        self.assertEqual(catalog_ids, catalog.source_ids)

    def test_builds_catalog_for_explicit_source_subset(self) -> None:
        # Arrange
        catalog_ids = tuple(entry.source_id for entry in source_catalog_entries())
        requested = (catalog_ids[-1], catalog_ids[0])

        # Act
        catalog = build_supported_source_catalog(requested)

        # Assert
        self.assertEqual((catalog_ids[0], catalog_ids[-1]), catalog.source_ids)

    def test_rejects_unimplemented_source_id(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "unknown or unimplemented"):
            build_supported_source_catalog(("unknown",))


if __name__ == "__main__":
    unittest.main()
