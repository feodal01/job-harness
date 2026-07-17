from __future__ import annotations

import unittest

from job_harness.v2.contracts import (
    CompensationCriterion,
    CompensationPeriod,
    Grade,
    SearchRequest,
    SearchScenario,
    WorkFormat,
)
from job_harness.v2.runtime.fact_requirement_planner import (
    plan_selection_fact_requirements,
)


class FactRequirementPlannerTests(unittest.TestCase):
    def test_plans_only_network_facts_that_can_change_selection(self) -> None:
        request = SearchRequest(
            query_variants=("QA",),
            grades=(Grade.SENIOR,),
            compensation=CompensationCriterion(200_000, "RUB", CompensationPeriod.MONTH),
            relocation=True,
            work_formats=(WorkFormat.REMOTE,),
            remote_scopes=("country:RU",),
            vacancy_geographies=("country:RU",),
            employer_geographies=("country:RU",),
        )

        requirements = plan_selection_fact_requirements(
            request,
            source_plan_id="plan-1",
            detail_available=True,
            profile_available=True,
        )

        self.assertEqual(
            {
                "grades",
                "compensation",
                "relocation",
                "work_formats",
                "remote_scopes",
                "vacancy_geographies",
                "employer_geographies",
            },
            {requirement.criterion for requirement in requirements},
        )
        self.assertTrue(all(requirement.provider.required_for_final for requirement in requirements))
        self.assertTrue(all(requirement.comparison == {"operator": "known"} for requirement in requirements))
        self.assertEqual(
            {
                "grades": "derived_facts.structured-selection-facts.grade.resolved",
                "compensation": "derived_facts.structured-selection-facts.compensation.minimum",
                "relocation": "derived_facts.structured-selection-facts.relocation.supported",
                "work_formats": "derived_facts.structured-selection-facts.workplace.formats",
                "remote_scopes": "derived_facts.structured-selection-facts.workplace.remote_scopes",
                "vacancy_geographies": "derived_facts.structured-selection-facts.location.countries",
                "employer_geographies": (
                    "derived_facts.structured-selection-facts.employer_geographies"
                ),
            },
            {requirement.criterion: requirement.fact_path for requirement in requirements},
        )

    def test_scenario_criteria_share_the_same_fact_contract(self) -> None:
        request = SearchRequest(
            query_variants=("QA",),
            scenarios=(
                SearchScenario(
                    work_formats=(WorkFormat.REMOTE,),
                    remote_scopes=("global",),
                ),
                SearchScenario(
                    relocation=True,
                    employer_geographies=("country:RU",),
                ),
            ),
        )

        requirements = plan_selection_fact_requirements(
            request,
            source_plan_id="plan-1",
            detail_available=True,
            profile_available=True,
        )

        self.assertEqual(
            {"work_formats", "remote_scopes", "relocation", "employer_geographies"},
            {requirement.criterion for requirement in requirements},
        )

    def test_omits_requirements_without_a_capable_parser(self) -> None:
        request = SearchRequest(
            query_variants=("QA",),
            relocation=True,
            employer_geographies=("country:RU",),
        )

        self.assertEqual(
            (),
            plan_selection_fact_requirements(
                request,
                source_plan_id="plan-1",
                detail_available=False,
                profile_available=False,
            ),
        )


if __name__ == "__main__":
    unittest.main()
