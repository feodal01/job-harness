from __future__ import annotations

import unittest
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from job_harness.v2.contracts import (
    CompensationCriterion,
    CompensationPeriod,
    Grade,
    SearchCriterion,
    SearchRequest,
    SearchScenario,
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
    def test_normalizes_dimensioned_compensation(self) -> None:
        request = SearchRequest(
            query_variants=("QA",),
            compensation=CompensationCriterion(
                minimum=200_000,
                currency="rur",
                period=CompensationPeriod.MONTH,
                gross=False,
            ),
        )

        assert request.compensation is not None
        self.assertEqual(200_000, request.compensation.minimum)
        self.assertEqual("RUB", request.compensation.currency)
        self.assertEqual(CompensationPeriod.MONTH, request.compensation.period)
        self.assertIn(SearchCriterion.COMPENSATION, request.requested_criteria)

    def test_rejects_invalid_compensation_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "minimum"):
            CompensationCriterion(0, "RUB", CompensationPeriod.MONTH)
        with self.assertRaisesRegex(ValueError, "currency"):
            CompensationCriterion(100_000, "rubles", CompensationPeriod.MONTH)

    def test_rejects_removed_salary_from_request_field(self) -> None:
        with self.assertRaises(TypeError):
            cast(Any, SearchRequest)(query_variants=("QA",), salary_from=100_000)

    def test_normalizes_or_scenarios_as_public_search_contract(self) -> None:
        request = SearchRequest(
            query_variants=("AI quality",),
            scenarios=(
                SearchScenario(
                    work_formats=(WorkFormat.REMOTE,),
                    remote_scopes=("global",),
                    employer_geographies=("country:ru",),
                ),
                SearchScenario(
                    relocation=True,
                    work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID),
                ),
            ),
        )

        self.assertEqual(("country:RU",), request.scenarios[0].employer_geographies)
        self.assertEqual(
            {
                SearchCriterion.QUERY,
                SearchCriterion.RELOCATION,
                SearchCriterion.WORK_FORMATS,
                SearchCriterion.REMOTE_SCOPES,
                SearchCriterion.EMPLOYER_GEOGRAPHIES,
            },
            request.requested_criteria,
        )

    def test_rejects_mixing_flat_location_filters_with_or_scenarios(self) -> None:
        with self.assertRaisesRegex(ValueError, "scenarios"):
            SearchRequest(
                query_variants=("AI quality",),
                relocation=True,
                scenarios=(SearchScenario(work_formats=(WorkFormat.REMOTE,)),),
            )

    def test_rejects_empty_scenario(self) -> None:
        with self.assertRaisesRegex(ValueError, "scenario"):
            SearchScenario()

    def test_requires_query_variants(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "query_variants"):
            SearchRequest(query_variants=("  ",))

    def test_normalizes_public_request_fields(self) -> None:
        # Arrange / Act
        request = SearchRequest(
            query_variants=(" QA ", "qa", "тестировщик"),
            grades=(Grade.MIDDLE, Grade.MIDDLE),
            compensation=CompensationCriterion(200_000, "RUB", CompensationPeriod.MONTH),
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

    def test_rejects_unknown_as_public_filter_value(self) -> None:
        cases: tuple[tuple[str, Callable[[], SearchRequest]], ...] = (
            (
                "work_formats",
                lambda: SearchRequest(
                    query_variants=("QA",),
                    work_formats=(WorkFormat.REMOTE, WorkFormat.UNKNOWN),
                ),
            ),
            (
                "remote_scopes",
                lambda: SearchRequest(
                    query_variants=("QA",),
                    work_formats=(WorkFormat.REMOTE,),
                    remote_scopes=("global", "unknown"),
                ),
            ),
            (
                "vacancy_geographies",
                lambda: SearchRequest(
                    query_variants=("QA",),
                    vacancy_geographies=("country:RU", "unknown"),
                ),
            ),
            (
                "employer_geographies",
                lambda: SearchRequest(
                    query_variants=("QA",),
                    employer_geographies=("country:RU", "unknown"),
                ),
            ),
        )
        for field_name, build_request in cases:
            # Arrange / Act / Assert
            with self.subTest(field_name=field_name), self.assertRaisesRegex(
                ValueError,
                "must not contain unknown",
            ):
                build_request()

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
            compensation=CompensationCriterion(100_000, "RUB", CompensationPeriod.MONTH),
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
                SearchCriterion.COMPENSATION,
                SearchCriterion.PUBLISHED_SINCE,
                SearchCriterion.RELOCATION,
                SearchCriterion.WORK_FORMATS,
                SearchCriterion.REMOTE_SCOPES,
                SearchCriterion.VACANCY_GEOGRAPHIES,
            },
            criteria,
        )

    def test_rejects_invalid_compensation_minimum(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "minimum"):
            CompensationCriterion(0, "RUB", CompensationPeriod.MONTH)


if __name__ == "__main__":
    unittest.main()
