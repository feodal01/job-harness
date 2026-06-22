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
            SearchCriterion.REMOTE_IN_COUNTRY,
            SearchCriterion.REMOTE_GLOBAL,
            SearchCriterion.COUNTRIES,
            SearchCriterion.CITIES,
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
