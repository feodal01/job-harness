from __future__ import annotations

import unittest
from typing import cast

from job_harness.v2.contracts import (
    ParserType,
    SearchRequest,
    SearchScraperBundle,
    TargetParserResolver,
    TransportKind,
)
from job_harness.v2.runtime import (
    build_independent_parser_registry,
    build_supported_source_catalog,
    implemented_source_ids,
)
from job_harness.v2.runtime.invocation_resources import invocation_resource_key
from job_harness.v2.source_catalog import (
    source_catalog_entries,
    source_required_fixture_kinds,
)


class RuntimeSourceRegistryTest(unittest.TestCase):
    def test_every_production_http_bundle_exposes_preflight_action(self) -> None:
        registry = build_independent_parser_registry()

        missing = tuple(
            manifest.parser_id
            for manifest in registry.manifests()
            if manifest.transport == TransportKind.HTTP
            and not callable(getattr(registry.get(manifest.ref), "build_action", None))
        )

        self.assertEqual(missing, ())

    def test_independent_registry_has_one_search_bundle_per_source(self) -> None:
        registry = build_independent_parser_registry()

        catalog_search_manifests = tuple(
            manifest
            for manifest in registry.manifests()
            if manifest.parser_type == ParserType.SEARCH_LISTING
            and manifest.parser_id != "ats.discovered.search"
        )

        self.assertEqual(
            tuple(manifest.parser_id for manifest in catalog_search_manifests),
            tuple(f"{source_id}.search" for source_id in implemented_source_ids()),
        )

    def test_discovered_ats_url_routes_to_input_derived_listing_request(self) -> None:
        registry = build_independent_parser_registry(("hh_ru",))
        resolver = TargetParserResolver(registry.manifests())
        discovered_url = "https://jobs.lever.co/example-company"

        resolution = resolver.resolve(
            ParserType.SEARCH_LISTING,
            None,
            discovered_url,
        )

        self.assertEqual(resolution.kind, "resolved")
        assert resolution.parser_ref is not None
        self.assertEqual(resolution.parser_ref.parser_id, "ats.discovered.search")
        bundle = cast(SearchScraperBundle, registry.get(resolution.parser_ref))
        planned = bundle.plan_initial(
            SearchRequest(query_variants=("AI lead",)),
            {"kind": "discovered_url", "url": discovered_url},
        )
        self.assertEqual(len(planned), 1)
        self.assertEqual(
            planned[0].cursor["request"]["url"],
            "https://api.lever.co/v0/postings/example-company?mode=json",
        )
        self.assertEqual(planned[0].source_id, planned[0].target_provider_id)
        self.assertTrue(planned[0].source_id.startswith("ats:lever:"))
        self.assertEqual(
            invocation_resource_key(
                registry,
                resolution.parser_ref,
                planned[0],
                lambda host: host,
            ),
            "api.lever.co",
        )

    def test_independent_registry_exposes_each_scraper_type_separately(self) -> None:
        registry = build_independent_parser_registry(("hh_ru",))

        self.assertEqual(
            tuple(
                (manifest.parser_id, manifest.parser_type)
                for manifest in registry.manifests()
            ),
            (
                ("hh_ru.search", ParserType.SEARCH_LISTING),
                ("hh_ru.detail", ParserType.VACANCY_DETAIL),
                ("hh_ru.company-profile", ParserType.COMPANY_PROFILE),
                ("ats.discovered.search", ParserType.SEARCH_LISTING),
                ("web.company-site", ParserType.COMPANY_SITE),
            ),
        )

    def test_registry_exposes_only_fixture_verified_detail_capabilities(self) -> None:
        registry = build_independent_parser_registry()

        detail_parser_ids = tuple(
            manifest.parser_id
            for manifest in registry.manifests()
            if manifest.parser_type == ParserType.VACANCY_DETAIL
        )
        expected = tuple(
            f"{source_id}.detail"
            for source_id in implemented_source_ids()
            if source_required_fixture_kinds(source_id).detail
        )

        self.assertEqual(expected, detail_parser_ids)

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
