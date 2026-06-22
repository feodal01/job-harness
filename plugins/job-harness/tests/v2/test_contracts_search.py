from __future__ import annotations

import unittest
from datetime import date

from job_harness.v2.contracts import (
    Grade,
    SearchCriterion,
    SearchRequest,
    SourceType,
    TextExclusion,
    TextExclusionMode,
    TextField,
)


class TextExclusionTest(unittest.TestCase):
    def test_rejects_empty_pattern(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "pattern"):
            TextExclusion(pattern="   ")

    def test_normalizes_pattern_and_fields(self) -> None:
        # Arrange / Act
        exclusion = TextExclusion(
            pattern="  selenium ",
            mode=TextExclusionMode.SUBSTRING,
            fields=(TextField.DESCRIPTION, TextField.DESCRIPTION, TextField.REQUIREMENTS),
        )

        # Assert
        self.assertEqual("selenium", exclusion.pattern)
        self.assertEqual(
            (TextField.DESCRIPTION, TextField.REQUIREMENTS),
            exclusion.fields,
        )


class SearchRequestTest(unittest.TestCase):
    def test_requires_query_variants(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "query_variants"):
            SearchRequest(query_variants=("  ",))

    def test_normalizes_public_request_fields(self) -> None:
        # Arrange / Act
        request = SearchRequest(
            query_variants=(" QA ", "qa", "тестировщик"),
            grades=(Grade.MIDDLE, Grade.MIDDLE),
            salary_from=200000,
            published_since=date(2026, 6, 1),
            exclude_companies=(" Acme ", "acme", "Beta"),
            exclude_text=(TextExclusion(pattern="selenium"),),
            relocation=False,
            remote_in_country=True,
            remote_global=None,
            countries=("ru", "RU", "am"),
            cities=(" Москва ", "москва", "Ереван"),
            max_results=0,
            sources=(" hh_ru ", "hh_ru", "career:vk"),
            source_types=(SourceType.AGGREGATOR, SourceType.AGGREGATOR),
        )

        # Assert
        self.assertEqual(("QA", "тестировщик"), request.query_variants)
        self.assertEqual((Grade.MIDDLE,), request.grades)
        self.assertEqual(("Acme", "Beta"), request.exclude_companies)
        self.assertEqual(("RU", "AM"), request.countries)
        self.assertEqual(("Москва", "Ереван"), request.cities)
        self.assertEqual(("hh_ru", "career:vk"), request.sources)
        self.assertEqual((SourceType.AGGREGATOR,), request.source_types)

    def test_requested_criteria_reflects_optional_filters(self) -> None:
        # Arrange
        request = SearchRequest(
            query_variants=("QA",),
            grades=(Grade.SENIOR,),
            salary_from=100000,
            published_since=date(2026, 6, 1),
            relocation=True,
            remote_in_country=True,
            remote_global=False,
            countries=("RU",),
            cities=("Москва",),
        )

        # Act
        criteria = request.requested_criteria

        # Assert
        self.assertEqual(
            {
                SearchCriterion.QUERY,
                SearchCriterion.GRADES,
                SearchCriterion.SALARY_FROM,
                SearchCriterion.PUBLISHED_SINCE,
                SearchCriterion.RELOCATION,
                SearchCriterion.REMOTE_IN_COUNTRY,
                SearchCriterion.REMOTE_GLOBAL,
                SearchCriterion.COUNTRIES,
                SearchCriterion.CITIES,
            },
            criteria,
        )

    def test_rejects_invalid_numeric_fields(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "salary_from"):
            SearchRequest(query_variants=("QA",), salary_from=0)
        with self.assertRaisesRegex(ValueError, "max_results"):
            SearchRequest(query_variants=("QA",), max_results=-1)


if __name__ == "__main__":
    unittest.main()
