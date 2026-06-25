from __future__ import annotations

import unittest
from datetime import date
from typing import Any, cast

from job_harness.v2.contracts import (
    Grade,
    RemoteMode,
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
            remote_mode=RemoteMode.COMPATIBLE_REMOTE,
            hybrid_ok=True,
            office_ok=True,
            work_from_geographies=(" europe ", "EU", "pl", "PL"),
            vacancy_geographies=(" cy ", "CY", "UK"),
            cities=(" Москва ", "москва", "Ереван"),
            sources=(" hh_ru ", "hh_ru", "career:vk"),
            source_types=(SourceType.AGGREGATOR, SourceType.AGGREGATOR),
        )

        # Assert
        self.assertEqual(("QA", "тестировщик"), request.query_variants)
        self.assertEqual((Grade.MIDDLE,), request.grades)
        self.assertEqual(("Acme", "Beta"), request.exclude_companies)
        self.assertEqual(RemoteMode.COMPATIBLE_REMOTE, request.remote_mode)
        self.assertTrue(request.hybrid_ok)
        self.assertTrue(request.office_ok)
        self.assertEqual(("europe", "EU", "PL"), request.work_from_geographies)
        self.assertEqual(("CY", "GB"), request.vacancy_geographies)
        self.assertEqual(("Москва", "Ереван"), request.cities)
        self.assertEqual(("hh_ru", "career:vk"), request.sources)
        self.assertEqual((SourceType.AGGREGATOR,), request.source_types)

    def test_rejects_compatible_remote_without_work_from_geography(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "work_from_geographies"):
            SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
            )

    def test_rejects_work_from_without_compatible_remote(self) -> None:
        for remote_mode in (
            None,
            RemoteMode.ANY,
            RemoteMode.GLOBAL_REMOTE_ONLY,
            RemoteMode.NON_REMOTE_ONLY,
        ):
            # Arrange / Act / Assert
            with self.subTest(remote_mode=remote_mode), self.assertRaisesRegex(ValueError, "work_from_geographies"):
                SearchRequest(
                    query_variants=("QA",),
                    remote_mode=remote_mode,
                    work_from_geographies=("RU",),
                )

    def test_rejects_invalid_request_geographies(self) -> None:
        for value in ("global", "moon", "not a country"):
            # Arrange / Act / Assert
            with (
                self.subTest(value=value, field="work_from_geographies"),
                self.assertRaisesRegex(
                    ValueError,
                    "unsupported geography",
                ),
            ):
                SearchRequest(
                    query_variants=("QA",),
                    remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                    work_from_geographies=(value,),
                )

            # Arrange / Act / Assert
            with (
                self.subTest(value=value, field="vacancy_geographies"),
                self.assertRaisesRegex(
                    ValueError,
                    "unsupported geography",
                ),
            ):
                SearchRequest(
                    query_variants=("QA",),
                    vacancy_geographies=(value,),
                )

    def test_rejects_old_request_fields(self) -> None:
        for field_name, value in (
            ("remote_in_country", True),
            ("remote_global", True),
            ("countries", ("RU",)),
        ):
            # Arrange / Act / Assert
            with self.subTest(field_name=field_name), self.assertRaises(TypeError):
                kwargs: dict[str, Any] = {field_name: value}
                cast(Any, SearchRequest)(query_variants=("QA",), **kwargs)

    def test_rejects_invalid_work_format_flags(self) -> None:
        for field_name in ("hybrid_ok", "office_ok"):
            # Arrange / Act / Assert
            with self.subTest(field_name=field_name), self.assertRaisesRegex(ValueError, field_name):
                kwargs: dict[str, Any] = {field_name: "true"}
                cast(Any, SearchRequest)(query_variants=("QA",), **kwargs)

    def test_rejects_physical_format_flags_with_global_remote_only(self) -> None:
        for field_name in ("hybrid_ok", "office_ok"):
            # Arrange / Act / Assert
            with self.subTest(field_name=field_name), self.assertRaisesRegex(ValueError, "global_remote_only"):
                kwargs: dict[str, Any] = {field_name: True}
                SearchRequest(
                    query_variants=("QA",),
                    remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY,
                    **kwargs,
                )

    def test_requested_criteria_reflects_optional_filters(self) -> None:
        # Arrange
        request = SearchRequest(
            query_variants=("QA",),
            grades=(Grade.SENIOR,),
            salary_from=100000,
            published_since=date(2026, 6, 1),
            relocation=True,
            remote_mode=RemoteMode.COMPATIBLE_REMOTE,
            hybrid_ok=True,
            office_ok=True,
            work_from_geographies=("RU",),
            vacancy_geographies=("CY",),
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
                SearchCriterion.REMOTE_MODE,
                SearchCriterion.WORK_FROM_GEOGRAPHIES,
                SearchCriterion.VACANCY_GEOGRAPHIES,
                SearchCriterion.CITIES,
            },
            criteria,
        )

    def test_rejects_invalid_numeric_fields(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "salary_from"):
            SearchRequest(query_variants=("QA",), salary_from=0)


if __name__ == "__main__":
    unittest.main()
