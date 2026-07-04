from __future__ import annotations

import unittest
from datetime import date

from job_harness.v2.contracts import SearchRequest, WorkFormat
from job_harness.v2.postprocessing import VacancyFilterCriteria, VacancyFilterFacts, decide_vacancy_filter
from job_harness.v2.postprocessing.filter_ast import (
    AllFilter,
    FieldFilter,
    FilterFacts,
    evaluate_filter_ast,
    filter_ast_from_search_request,
)


class VacancyFilterPolicyTest(unittest.TestCase):
    def test_title_mismatch_is_removed_without_second_chance(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(queries=("QA",)),
            vacancy=VacancyFilterFacts(
                title="Ֆրանչայզինգային սրճարանների որակի վերահսկման մասնագետ",
                company="Coffee House Company",
            ),
        )

        self.assertFalse(decision.keep)
        self.assertFalse(decision.title_matches)
        self.assertFalse(decision.include_in_filtered_out)
        self.assertEqual(("query_mismatch",), decision.reasons)

    def test_country_only_listing_does_not_satisfy_requested_remote_work_format(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(
                queries=("Quality Assurance",),
                work_formats=("remote",),
                remote_scopes=("global", "country:RU"),
                vacancy_geographies=("country:RU", "country:AM"),
            ),
            vacancy=VacancyFilterFacts(
                title="Quality Assurance Specialist",
                company="Coffee House Company",
                countries=("AM",),
                city="Yerevan",
            ),
        )

        self.assertFalse(decision.keep)
        self.assertTrue(decision.title_matches)
        self.assertTrue(decision.include_in_filtered_out)
        self.assertEqual(("work_format_mismatch",), decision.reasons)

    def test_unknown_ast_filter_facts_are_removed_by_default(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(
                queries=("QA",),
                grades=("senior",),
                salary_from=5000,
                published_since=date(2026, 1, 1),
                relocation=True,
                work_formats=("remote",),
                remote_scopes=("global", "country:RU"),
                vacancy_geographies=("country:RU",),
            ),
            vacancy=VacancyFilterFacts(
                title="QA Engineer",
                remote_scopes=("unknown",),
            ),
        )

        self.assertFalse(decision.keep)
        self.assertTrue(decision.title_matches)
        self.assertTrue(decision.include_in_filtered_out)
        self.assertEqual(
            ("work_format_mismatch", "vacancy_geography_mismatch"),
            decision.reasons,
        )

    def test_title_matches_any_query_variant(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(queries=("Quality Assurance", "SDET")),
            vacancy=VacancyFilterFacts(title="Senior SDET Engineer"),
        )

        self.assertTrue(decision.keep)
        self.assertTrue(decision.title_matches)

    def test_global_remote_request_compiles_to_structured_filter_ast(self) -> None:
        ast = filter_ast_from_search_request(
            SearchRequest(
                query_variants=("AI Lead",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("global",),
                vacancy_geographies=("country:RU",),
            )
        )

        self.assertEqual(
            AllFilter(
                filters=(
                    AllFilter(
                        filters=(
                            FieldFilter(
                                field="work_format",
                                op="any_of",
                                values=("remote",),
                                reason="work_format_mismatch",
                            ),
                            FieldFilter(
                                field="remote_scope",
                                op="any_of",
                                values=("global",),
                                reason="remote_scope_mismatch",
                            ),
                        ),
                        short_circuit_on_first_failure=True,
                    ),
                    FieldFilter(
                        field="vacancy_geography",
                        op="intersects",
                        values=("country:RU",),
                        reason="vacancy_geography_mismatch",
                    ),
                )
            ),
            ast,
        )

    def test_country_remote_scope_intersects_global_and_matching_country(self) -> None:
        ast = filter_ast_from_search_request(
            SearchRequest(
                query_variants=("AI Lead",),
                work_formats=(WorkFormat.REMOTE,),
                remote_scopes=("country:RU",),
            )
        )

        self.assertTrue(
            evaluate_filter_ast(
                ast,
                FilterFacts(work_formats=("remote",), remote_scopes=("global",)),
            ).keep
        )
        self.assertTrue(
            evaluate_filter_ast(
                ast,
                FilterFacts(work_formats=("remote",), remote_scopes=("country:RU",)),
            ).keep
        )
        self.assertFalse(
            evaluate_filter_ast(
                ast,
                FilterFacts(work_formats=("remote",), remote_scopes=("country:TR",)),
            ).keep
        )

    def test_filter_ast_intersects_prefixed_geography_values(self) -> None:
        expression = AllFilter(
            filters=(
                FieldFilter(
                    field="vacancy_geography",
                    op="intersects",
                    values=("country:RU",),
                    reason="vacancy_geography_mismatch",
                ),
                FieldFilter(
                    field="vacancy_geography",
                    op="intersects",
                    values=("city:Moscow",),
                    reason="vacancy_geography_mismatch",
                ),
            )
        )

        evaluation = evaluate_filter_ast(
            expression,
            FilterFacts(vacancy_geographies=("country:RU", "city:Moscow")),
        )

        self.assertTrue(evaluation.keep)
        self.assertEqual((), evaluation.reasons)


if __name__ == "__main__":
    unittest.main()
