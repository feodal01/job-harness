from __future__ import annotations

import unittest
from datetime import date

from job_harness.v2.contracts import (
    CompensationCriterion,
    CompensationFact,
    CompensationPeriod,
    CriterionState,
    SearchRequest,
    SearchScenario,
    SelectionOutcome,
    WorkFormat,
)
from job_harness.v2.postprocessing import VacancyFilterCriteria, VacancyFilterFacts, decide_vacancy_filter
from job_harness.v2.postprocessing.filter_ast import (
    AllFilter,
    AnyFilter,
    FieldFilter,
    FilterFacts,
    evaluate_filter_ast,
    filter_ast_from_search_request,
)


class VacancyFilterPolicyTest(unittest.TestCase):
    def test_unknown_grade_is_not_a_final_keep(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(queries=("QA",), grades=("senior",)),
            vacancy=VacancyFilterFacts(title="QA Engineer"),
        )

        self.assertEqual(SelectionOutcome.NEEDS_EVIDENCE, decision.outcome)
        self.assertFalse(decision.keep)
        self.assertTrue(decision.can_enrich)
        self.assertIn("insufficient_evidence:grades", decision.reasons)

    def test_dimensionally_incomparable_compensation_is_unknown(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(
                queries=("QA",),
                compensation=CompensationCriterion(
                    250_000,
                    "RUB",
                    CompensationPeriod.MONTH,
                ),
            ),
            vacancy=VacancyFilterFacts(
                title="QA Engineer",
                compensation=CompensationFact(
                    minimum=300_000,
                    maximum=400_000,
                    currency="USD",
                    period=CompensationPeriod.YEAR,
                    gross=None,
                    evidence=("salary",),
                ),
            ),
        )

        self.assertEqual(SelectionOutcome.NEEDS_EVIDENCE, decision.outcome)
        self.assertIn("insufficient_evidence:compensation", decision.reasons)

    def test_compensation_requires_explicit_matching_lower_bound(self) -> None:
        criteria = VacancyFilterCriteria(
            queries=("QA",),
            compensation=CompensationCriterion(250_000, "RUB", CompensationPeriod.MONTH),
        )
        maximum_only = VacancyFilterFacts(
            title="QA Engineer",
            compensation=CompensationFact(
                minimum=None,
                maximum=400_000,
                currency="RUB",
                period=CompensationPeriod.MONTH,
                gross=None,
                evidence=("salary",),
            ),
        )

        self.assertEqual(
            SelectionOutcome.NEEDS_EVIDENCE,
            decide_vacancy_filter(criteria=criteria, vacancy=maximum_only).outcome,
        )

    def test_matching_or_branch_short_circuits_unknown_alternative(self) -> None:
        expression = AnyFilter(
            filters=(
                FieldFilter("relocation", "any_of", ("true",), "relocation_mismatch"),
                FieldFilter("work_format", "any_of", ("hybrid",), "work_format_mismatch"),
            )
        )

        evaluation = evaluate_filter_ast(
            expression,
            FilterFacts(relocation=None, work_formats=("hybrid",)),
        )

        self.assertEqual(CriterionState.MATCH, evaluation.state)
        self.assertEqual((), evaluation.reasons)

    def test_or_scenarios_accept_remote_russian_employer_or_supported_relocation(self) -> None:
        request = SearchRequest(
            query_variants=("AI quality",),
            scenarios=(
                SearchScenario(
                    work_formats=(WorkFormat.REMOTE,),
                    employer_geographies=("country:RU",),
                ),
                SearchScenario(
                    work_formats=(WorkFormat.REMOTE, WorkFormat.HYBRID),
                    relocation=True,
                ),
            ),
        )
        ast = filter_ast_from_search_request(request)

        self.assertIsInstance(ast, AnyFilter)
        self.assertEqual(
            CriterionState.MATCH,
            evaluate_filter_ast(
                ast,
                FilterFacts(
                    work_formats=("remote",),
                    employer_geographies=("country:RU",),
                    relocation=False,
                ),
            ).state,
        )
        self.assertEqual(
            CriterionState.MATCH,
            evaluate_filter_ast(
                ast,
                FilterFacts(
                    work_formats=("hybrid",),
                    employer_geographies=("country:US",),
                    relocation=True,
                ),
            ).state,
        )
        self.assertEqual(
            CriterionState.MISMATCH,
            evaluate_filter_ast(
                ast,
                FilterFacts(
                    work_formats=("remote",),
                    employer_geographies=("country:US",),
                    relocation=False,
                ),
            ).state,
        )

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

        self.assertEqual(SelectionOutcome.NEEDS_EVIDENCE, decision.outcome)
        self.assertFalse(decision.keep)
        self.assertTrue(decision.title_matches)
        self.assertTrue(decision.include_in_filtered_out)
        self.assertEqual(
            (
                "insufficient_evidence:work_formats",
                "insufficient_evidence:remote_scopes",
            ),
            decision.reasons,
        )

    def test_unknown_ast_filter_facts_are_removed_by_default(self) -> None:
        decision = decide_vacancy_filter(
            criteria=VacancyFilterCriteria(
                queries=("QA",),
                grades=("senior",),
                compensation=CompensationCriterion(5_000, "RUB", CompensationPeriod.MONTH),
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

        self.assertEqual(SelectionOutcome.NEEDS_EVIDENCE, decision.outcome)
        self.assertFalse(decision.keep)
        self.assertTrue(decision.title_matches)
        self.assertTrue(decision.include_in_filtered_out)
        self.assertEqual(
            (
                "insufficient_evidence:grades",
                "insufficient_evidence:compensation",
                "insufficient_evidence:published_since",
                "insufficient_evidence:work_formats",
                "insufficient_evidence:remote_scopes",
                "insufficient_evidence:vacancy_geographies",
                "insufficient_evidence:relocation",
            ),
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

        self.assertEqual(
            CriterionState.MATCH,
            evaluate_filter_ast(
                ast,
                FilterFacts(work_formats=("remote",), remote_scopes=("global",)),
            ).state,
        )
        self.assertEqual(
            CriterionState.MATCH,
            evaluate_filter_ast(
                ast,
                FilterFacts(work_formats=("remote",), remote_scopes=("country:RU",)),
            ).state,
        )
        self.assertEqual(
            CriterionState.MISMATCH,
            evaluate_filter_ast(
                ast,
                FilterFacts(work_formats=("remote",), remote_scopes=("country:TR",)),
            ).state,
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

        self.assertEqual(CriterionState.MATCH, evaluation.state)
        self.assertEqual((), evaluation.reasons)


if __name__ == "__main__":
    unittest.main()
