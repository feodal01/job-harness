from __future__ import annotations

import unittest
from pathlib import Path

from job_harness.v2.contracts import (
    ParserFixtureKind,
    SearchCriterion,
    SourceType,
    SupportedSourceContract,
    Transport,
)
from job_harness.v2.runtime import build_supported_source_catalog
from job_harness.v2.source_catalog import (
    country_catalog_entries,
    source_catalog_entries,
    source_descriptor,
    source_fixture_suite,
    source_required_fixture_kinds,
)

_PLUGIN_ROOT_PARENT_INDEX = 2
_PLUGIN_ROOT = Path(__file__).resolve().parents[_PLUGIN_ROOT_PARENT_INDEX]


class SourceCatalogTableTest(unittest.TestCase):
    def test_catalog_contains_unique_supported_v2_source_rows(self) -> None:
        # Arrange / Act
        entries = source_catalog_entries()
        source_ids = tuple(entry.source_id for entry in entries)

        # Assert
        self.assertGreater(len(entries), 0)
        self.assertEqual(len(source_ids), len(set(source_ids)))

    def test_country_catalog_contains_supported_search_countries(self) -> None:
        # Arrange / Act
        countries = country_catalog_entries()

        # Assert
        self.assertEqual(("AM", "RU"), tuple(country.country_code for country in countries))
        self.assertTrue(all(country.search_enabled for country in countries))

    def test_habr_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("habr_career")
        required_fixture_kinds = source_required_fixture_kinds("habr_career")
        fixture_suite = source_fixture_suite("habr_career")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual(("RU",), descriptor.countries)
        self.assertEqual(50, descriptor.source_limit)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.QUERY,
                    SearchCriterion.GRADES,
                    SearchCriterion.SALARY_FROM,
                }
            ),
            descriptor.native_request_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertTrue(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.PAGINATION,
                ParserFixtureKind.DETAIL,
                ParserFixtureKind.OPTIONAL_FIELDS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_vk_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("career:vk")
        required_fixture_kinds = source_required_fixture_kinds("career:vk")
        fixture_suite = source_fixture_suite("career:vk")

        # Assert
        self.assertEqual(SourceType.COMPANY_CAREER, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual(("RU",), descriptor.countries)
        self.assertEqual(25, descriptor.source_limit)
        self.assertEqual(frozenset({SearchCriterion.QUERY}), descriptor.native_request_criteria)
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_ibs_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("career:ibs")
        required_fixture_kinds = source_required_fixture_kinds("career:ibs")
        fixture_suite = source_fixture_suite("career:ibs")

        # Assert
        self.assertEqual(SourceType.COMPANY_CAREER, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual(("RU",), descriptor.countries)
        self.assertEqual(100, descriptor.source_limit)
        self.assertEqual(frozenset({SearchCriterion.REMOTE_IN_COUNTRY}), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.QUERY,
                    SearchCriterion.COUNTRIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertFalse(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_hh_ru_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("hh_ru")
        required_fixture_kinds = source_required_fixture_kinds("hh_ru")
        fixture_suite = source_fixture_suite("hh_ru")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual(("RU",), descriptor.countries)
        self.assertEqual(100, descriptor.source_limit)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.QUERY,
                    SearchCriterion.SALARY_FROM,
                }
            ),
            descriptor.native_request_criteria,
        )
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.GRADES,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertTrue(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertTrue(required_fixture_kinds.optional_fields)
        self.assertTrue(required_fixture_kinds.blocked)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.PAGINATION,
                ParserFixtureKind.DETAIL,
                ParserFixtureKind.BLOCKED,
                ParserFixtureKind.OPTIONAL_FIELDS,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_talanto_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("talanto")
        required_fixture_kinds = source_required_fixture_kinds("talanto")
        fixture_suite = source_fixture_suite("talanto")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(50, descriptor.source_limit)
        self.assertEqual(frozenset({SearchCriterion.QUERY}), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.GRADES,
                    SearchCriterion.SALARY_FROM,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_geekjob_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("geekjob")
        required_fixture_kinds = source_required_fixture_kinds("geekjob")
        fixture_suite = source_fixture_suite("geekjob")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(50, descriptor.source_limit)
        self.assertEqual(frozenset(), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.QUERY,
                    SearchCriterion.SALARY_FROM,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                    SearchCriterion.COUNTRIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_talento_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("talento")
        required_fixture_kinds = source_required_fixture_kinds("talento")
        fixture_suite = source_fixture_suite("talento")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(50, descriptor.source_limit)
        self.assertEqual(frozenset({SearchCriterion.QUERY}), descriptor.native_request_criteria)
        self.assertEqual(frozenset(), descriptor.structured_output_criteria)
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_finder_work_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("finder_work")
        required_fixture_kinds = source_required_fixture_kinds("finder_work")
        fixture_suite = source_fixture_suite("finder_work")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(100, descriptor.source_limit)
        self.assertEqual(
            frozenset({SearchCriterion.QUERY, SearchCriterion.SALARY_FROM}),
            descriptor.native_request_criteria,
        )
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.GRADES,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_getmatch_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("getmatch")
        required_fixture_kinds = source_required_fixture_kinds("getmatch")
        fixture_suite = source_fixture_suite("getmatch")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(100, descriptor.source_limit)
        self.assertEqual(frozenset({SearchCriterion.QUERY}), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_it_jobs_uz_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("it_jobs_uz")
        required_fixture_kinds = source_required_fixture_kinds("it_jobs_uz")
        fixture_suite = source_fixture_suite("it_jobs_uz")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(100, descriptor.source_limit)
        self.assertEqual(
            frozenset({SearchCriterion.QUERY, SearchCriterion.SALARY_FROM}),
            descriptor.native_request_criteria,
        )
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.GRADES,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_hirify_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("hirify")
        required_fixture_kinds = source_required_fixture_kinds("hirify")
        fixture_suite = source_fixture_suite("hirify")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(100, descriptor.source_limit)
        self.assertEqual(
            frozenset({SearchCriterion.QUERY, SearchCriterion.SALARY_FROM}),
            descriptor.native_request_criteria,
        )
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.GRADES,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_jobturbo_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("jobturbo")
        required_fixture_kinds = source_required_fixture_kinds("jobturbo")
        fixture_suite = source_fixture_suite("jobturbo")

        # Assert
        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(50, descriptor.source_limit)
        self.assertEqual(frozenset(), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.QUERY,
                    SearchCriterion.GRADES,
                    SearchCriterion.SALARY_FROM,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_hirehi_catalog_row_declares_source_contract(self) -> None:
        descriptor = source_descriptor("hirehi")
        required_fixture_kinds = source_required_fixture_kinds("hirehi")
        fixture_suite = source_fixture_suite("hirehi")

        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual(("RU",), descriptor.countries)
        self.assertEqual(50, descriptor.source_limit)
        self.assertEqual(frozenset({SearchCriterion.QUERY}), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.GRADES,
                    SearchCriterion.SALARY_FROM,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_staff_am_catalog_row_declares_source_contract(self) -> None:
        descriptor = source_descriptor("staff_am")
        required_fixture_kinds = source_required_fixture_kinds("staff_am")
        fixture_suite = source_fixture_suite("staff_am")

        self.assertEqual(SourceType.AGGREGATOR, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual(("AM",), descriptor.countries)
        self.assertEqual(100, descriptor.source_limit)
        self.assertEqual(frozenset({SearchCriterion.QUERY}), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.GRADES,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.RELOCATION,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertTrue(required_fixture_kinds.no_results)
        self.assertFalse(required_fixture_kinds.pagination)
        self.assertTrue(required_fixture_kinds.detail)
        self.assertEqual(
            (
                ParserFixtureKind.SUCCESS_NON_EMPTY,
                ParserFixtureKind.NO_RESULTS,
                ParserFixtureKind.DETAIL,
            ),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_jetbrains_catalog_row_declares_source_contract(self) -> None:
        # Arrange / Act
        descriptor = source_descriptor("career:jetbrains")
        required_fixture_kinds = source_required_fixture_kinds("career:jetbrains")
        fixture_suite = source_fixture_suite("career:jetbrains")

        # Assert
        self.assertEqual(SourceType.COMPANY_CAREER, descriptor.source_type)
        self.assertEqual(Transport.HTTP, descriptor.transport)
        self.assertEqual((), descriptor.countries)
        self.assertEqual(120, descriptor.source_limit)
        self.assertEqual(frozenset(), descriptor.native_request_criteria)
        self.assertEqual(
            frozenset(
                {
                    SearchCriterion.QUERY,
                    SearchCriterion.PUBLISHED_SINCE,
                    SearchCriterion.REMOTE_IN_COUNTRY,
                    SearchCriterion.REMOTE_GLOBAL,
                    SearchCriterion.COUNTRIES,
                    SearchCriterion.CITIES,
                }
            ),
            descriptor.structured_output_criteria,
        )
        self.assertFalse(required_fixture_kinds.no_results)
        self.assertEqual(
            (ParserFixtureKind.SUCCESS_NON_EMPTY,),
            tuple(case.kind for case in fixture_suite.cases),
        )

    def test_catalog_fixture_paths_exist_under_plugin_root(self) -> None:
        for entry in source_catalog_entries():
            with self.subTest(source_id=entry.source_id):
                # Arrange / Act
                fixture_suite = entry.fixture_suite()
                fixture_paths = tuple(
                    path
                    for case in fixture_suite.cases
                    for path in (case.captured_artifact_path, case.metadata_path, case.golden_path)
                )

                # Assert
                missing = tuple(path for path in fixture_paths if not (_PLUGIN_ROOT / path).exists())
                self.assertEqual((), missing)

    def test_supported_source_contracts_are_built_from_catalog_rows(self) -> None:
        for entry in source_catalog_entries():
            with self.subTest(source_id=entry.source_id):
                # Arrange / Act
                contract = SupportedSourceContract(
                    descriptor=entry.descriptor(),
                    required_fixture_kinds=entry.required_fixture_kinds,
                    fixture_suite=entry.fixture_suite(),
                )

                # Assert
                self.assertEqual(entry.source_id, contract.descriptor.source_id)

    def test_scraper_metadata_is_read_from_source_catalog(self) -> None:
        # Arrange
        catalog = build_supported_source_catalog()

        for source_id in catalog.source_ids:
            with self.subTest(source_id=source_id):
                scraper = catalog.get(source_id)

                # Act / Assert
                self.assertEqual(source_descriptor(source_id), scraper.descriptor)
                self.assertEqual(source_required_fixture_kinds(source_id), scraper.required_fixture_kinds)


if __name__ == "__main__":
    unittest.main()
