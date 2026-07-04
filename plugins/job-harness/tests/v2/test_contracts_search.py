from __future__ import annotations

import unittest
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from job_harness.v2.contracts import (
    Grade,
    SearchCriterion,
    SearchRequest,
    SourceType,
    TextExclusion,
    TextExclusionMode,
    TextField,
    WorkFormat,
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
            work_formats=(WorkFormat.REMOTE, WorkFormat.REMOTE, WorkFormat.HYBRID),
            remote_scopes=("global", "country:ru", "country:RU", "region:eu"),
            vacancy_geographies=("country:cy", "country:CY", "country:UK", "city: Москва ", "city:москва"),
            sources=(" hh_ru ", "hh_ru", "career:vk"),
            source_types=(SourceType.AGGREGATOR, SourceType.AGGREGATOR),
        )

        # Assert
        self.assertEqual(("QA", "тестировщик"), request.query_variants)
        self.assertEqual((Grade.MIDDLE,), request.grades)
        self.assertEqual(("Acme", "Beta"), request.exclude_companies)
        self.assertEqual((WorkFormat.REMOTE, WorkFormat.HYBRID), request.work_formats)
        self.assertEqual(("global", "country:RU", "region:EU"), request.remote_scopes)
        self.assertEqual(("country:CY", "country:GB", "city:Москва"), request.vacancy_geographies)
        self.assertEqual(("hh_ru", "career:vk"), request.sources)
        self.assertEqual((SourceType.AGGREGATOR,), request.source_types)

    def test_rejects_remote_scope_without_remote_work_format(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "remote_scopes"):
            SearchRequest(
                query_variants=("QA",),
                remote_scopes=("global",),
            )

    def test_rejects_invalid_scope_values(self) -> None:
        for value in ("RU", "moon", "country:europe", "region:RU", "city:Moscow"):
            # Arrange / Act / Assert
            with (
                self.subTest(value=value, field="remote_scopes"),
                self.assertRaisesRegex(
                    ValueError,
                    "remote_scopes",
                ),
            ):
                SearchRequest(
                    query_variants=("QA",),
                    work_formats=(WorkFormat.REMOTE,),
                    remote_scopes=(value,),
                )

        for value in ("RU", "global", "moon", "country:europe", "region:RU"):
            # Arrange / Act / Assert
            with (
                self.subTest(value=value, field="vacancy_geographies"),
                self.assertRaisesRegex(
                    ValueError,
                    "vacancy_geographies",
                ),
            ):
                SearchRequest(
                    query_variants=("QA",),
                    vacancy_geographies=(value,),
                )

    def test_rejects_unknown_as_only_workplace_filter_value(self) -> None:
        cases: tuple[tuple[str, Callable[[], SearchRequest]], ...] = (
            (
                "work_formats",
                lambda: SearchRequest(query_variants=("QA",), work_formats=(WorkFormat.UNKNOWN,)),
            ),
            (
                "remote_scopes",
                lambda: SearchRequest(
                    query_variants=("QA",),
                    work_formats=(WorkFormat.REMOTE,),
                    remote_scopes=("unknown",),
                ),
            ),
            (
                "vacancy_geographies",
                lambda: SearchRequest(query_variants=("QA",), vacancy_geographies=("unknown",)),
            ),
        )
        for field_name, build_request in cases:
            # Arrange / Act / Assert
            with self.subTest(field_name=field_name), self.assertRaisesRegex(ValueError, "only unknown"):
                build_request()

    def test_allows_unknown_alongside_concrete_workplace_filter_values(self) -> None:
        # Arrange / Act
        request = SearchRequest(
            query_variants=("QA",),
            work_formats=(WorkFormat.REMOTE, WorkFormat.UNKNOWN),
            remote_scopes=("global", "unknown"),
            vacancy_geographies=("country:RU", "unknown"),
        )

        # Assert
        self.assertEqual((WorkFormat.REMOTE, WorkFormat.UNKNOWN), request.work_formats)
        self.assertEqual(("global", "unknown"), request.remote_scopes)
        self.assertEqual(("country:RU", "unknown"), request.vacancy_geographies)

    def test_rejects_old_request_fields(self) -> None:
        for field_name, value in (
            ("remote_in_country", True),
            ("remote_global", True),
            ("countries", ("RU",)),
            ("remote_mode", "global_remote_only"),
            ("work_from_geographies", ("RU",)),
            ("hybrid_ok", True),
            ("office_ok", True),
            ("cities", ("Moscow",)),
        ):
            # Arrange / Act / Assert
            with self.subTest(field_name=field_name), self.assertRaises(TypeError):
                kwargs: dict[str, Any] = {field_name: value}
                cast(Any, SearchRequest)(query_variants=("QA",), **kwargs)

    def test_requested_criteria_reflects_optional_filters(self) -> None:
        # Arrange
        request = SearchRequest(
            query_variants=("QA",),
            grades=(Grade.SENIOR,),
            salary_from=100000,
            published_since=date(2026, 6, 1),
            relocation=True,
            work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID),
            remote_scopes=("global", "country:RU"),
            vacancy_geographies=("country:CY", "city:Москва"),
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
                SearchCriterion.WORK_FORMATS,
                SearchCriterion.REMOTE_SCOPES,
                SearchCriterion.VACANCY_GEOGRAPHIES,
            },
            criteria,
        )

    def test_rejects_invalid_numeric_fields(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "salary_from"):
            SearchRequest(query_variants=("QA",), salary_from=0)


if __name__ == "__main__":
    unittest.main()
