from __future__ import annotations

import unittest

from job_harness.v2.runtime import build_supported_source_catalog, implemented_source_ids
from job_harness.v2.source_catalog import source_catalog_entries


class RuntimeSourceRegistryTest(unittest.TestCase):
    def test_implemented_sources_are_catalog_backed(self) -> None:
        # Arrange
        catalog_ids = {entry.source_id for entry in source_catalog_entries()}

        # Act / Assert
        self.assertEqual(
            ("habr_career", "hh_ru", "talanto", "career:vk", "career:jetbrains", "geekjob", "talento", "finder_work"),
            implemented_source_ids(),
        )
        self.assertTrue(set(implemented_source_ids()) <= catalog_ids)

    def test_builds_catalog_for_explicit_source_subset(self) -> None:
        # Arrange / Act
        catalog = build_supported_source_catalog(("career:vk",))

        # Assert
        self.assertEqual(("career:vk",), catalog.source_ids)

    def test_rejects_unimplemented_source_id(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "unknown or unimplemented"):
            build_supported_source_catalog(("unknown",))


if __name__ == "__main__":
    unittest.main()
