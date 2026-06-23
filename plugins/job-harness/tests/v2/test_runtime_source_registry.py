from __future__ import annotations

import unittest

from job_harness.v2.runtime import build_supported_source_catalog, implemented_source_ids
from job_harness.v2.source_catalog import source_catalog_entries


class RuntimeSourceRegistryTest(unittest.TestCase):
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
