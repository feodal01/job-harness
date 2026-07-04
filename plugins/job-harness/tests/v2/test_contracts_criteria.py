from __future__ import annotations

import unittest

from job_harness.v2.contracts import (
    ALL_SEARCH_CRITERIA,
    SearchCriterion,
    TextEnrichmentPolicy,
    TextField,
    all_search_criterion_descriptors,
    search_criterion_descriptor,
)


class SearchCriterionDescriptorTest(unittest.TestCase):
    def test_every_search_criterion_has_exactly_one_descriptor(self) -> None:
        # Arrange / Act
        descriptors = all_search_criterion_descriptors()

        # Assert
        self.assertEqual(
            set(ALL_SEARCH_CRITERIA),
            {descriptor.criterion for descriptor in descriptors},
        )
        self.assertEqual(len(ALL_SEARCH_CRITERIA), len(descriptors))

    def test_descriptor_policies_are_explicit_for_known_criterion_groups(self) -> None:
        # Arrange / Act / Assert
        self.assertEqual(
            TextEnrichmentPolicy.ALLOWED,
            search_criterion_descriptor(SearchCriterion.QUERY).text_enrichment,
        )
        for criterion in (
            SearchCriterion.QUERY,
            SearchCriterion.GRADES,
            SearchCriterion.SALARY_FROM,
            SearchCriterion.RELOCATION,
            SearchCriterion.WORK_FORMATS,
            SearchCriterion.REMOTE_SCOPES,
            SearchCriterion.VACANCY_GEOGRAPHIES,
        ):
            with self.subTest(criterion=criterion):
                descriptor = search_criterion_descriptor(criterion)
                self.assertEqual(TextEnrichmentPolicy.ALLOWED, descriptor.text_enrichment)
                self.assertNotEqual((), descriptor.text_enrichment_fields)

        descriptor = search_criterion_descriptor(SearchCriterion.PUBLISHED_SINCE)
        self.assertEqual(
            TextEnrichmentPolicy.REQUIRES_STRUCTURED_EVIDENCE,
            descriptor.text_enrichment,
        )
        self.assertEqual((), descriptor.text_enrichment_fields)

    def test_grade_descriptor_allows_centralized_text_estimation(self) -> None:
        # Arrange / Act
        descriptor = search_criterion_descriptor(SearchCriterion.GRADES)

        # Assert
        self.assertEqual(("native_grade",), descriptor.source_fact_fields)
        self.assertEqual(
            (
                TextField.TITLE,
                TextField.DESCRIPTION,
                TextField.REQUIREMENTS,
                TextField.SKILLS,
                TextField.RAW_TEXT,
            ),
            descriptor.text_enrichment_fields,
        )

    def test_search_criteria_are_new_remote_geography_contract(self) -> None:
        # Arrange / Act
        criteria = tuple(criterion.value for criterion in ALL_SEARCH_CRITERIA)

        # Assert
        self.assertEqual(
            (
                "query",
                "grades",
                "salary_from",
                "published_since",
                "relocation",
                "work_formats",
                "remote_scopes",
                "vacancy_geographies",
            ),
            criteria,
        )

    def test_remote_geography_descriptors_read_postprocessing_facts(self) -> None:
        # Arrange / Act
        work_formats = search_criterion_descriptor(SearchCriterion.WORK_FORMATS)
        remote_scopes = search_criterion_descriptor(SearchCriterion.REMOTE_SCOPES)
        vacancy = search_criterion_descriptor(SearchCriterion.VACANCY_GEOGRAPHIES)

        # Assert
        self.assertEqual(
            ("work_format", "remote_in_country", "remote_global", "location_text", "raw"),
            work_formats.source_fact_fields,
        )
        self.assertEqual(
            ("remote_in_country", "remote_global", "country", "location_text", "raw"),
            remote_scopes.source_fact_fields,
        )
        self.assertEqual(
            ("country", "city", "location_text", "raw"),
            vacancy.source_fact_fields,
        )

    def test_salary_descriptor_allows_text_extraction(self) -> None:
        # Arrange / Act
        descriptor = search_criterion_descriptor(SearchCriterion.SALARY_FROM)

        # Assert
        self.assertEqual(
            ("salary_text", "salary_min", "salary_max", "salary_currency"),
            descriptor.source_fact_fields,
        )
        self.assertEqual(
            (
                TextField.DESCRIPTION,
                TextField.REQUIREMENTS,
                TextField.RAW_TEXT,
            ),
            descriptor.text_enrichment_fields,
        )


if __name__ == "__main__":
    unittest.main()
