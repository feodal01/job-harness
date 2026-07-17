"""Translate requested selection criteria into network fact requirements."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import (
    FactProviderSpec,
    ProviderStage,
    SearchCriterion,
    SearchRequest,
)
from job_harness.v2.serialization import JsonObject

_DERIVED_SELECTION_FACTS = "derived_facts.structured-selection-facts"


@dataclass(frozen=True)
class PlannedFactRequirement:
    criterion: str
    fact_path: str
    comparison: JsonObject
    provider: FactProviderSpec
    skip_when_final_keep: bool


def plan_selection_fact_requirements(
    request: SearchRequest,
    *,
    source_plan_id: str,
    detail_available: bool,
    profile_available: bool,
) -> tuple[PlannedFactRequirement, ...]:
    requested = request.requested_criteria
    planned: list[PlannedFactRequirement] = []
    if detail_available:
        detail_facts = {
            SearchCriterion.GRADES: (
                f"{_DERIVED_SELECTION_FACTS}.grade.resolved",
                "native_grade",
            ),
            SearchCriterion.COMPENSATION: (
                f"{_DERIVED_SELECTION_FACTS}.compensation.minimum",
                "salary",
            ),
            SearchCriterion.RELOCATION: (
                f"{_DERIVED_SELECTION_FACTS}.relocation.supported",
                "description",
            ),
            SearchCriterion.WORK_FORMATS: (
                f"{_DERIVED_SELECTION_FACTS}.workplace.formats",
                "work_formats",
            ),
            SearchCriterion.REMOTE_SCOPES: (
                f"{_DERIVED_SELECTION_FACTS}.workplace.remote_scopes",
                "remote_scopes",
            ),
            SearchCriterion.VACANCY_GEOGRAPHIES: (
                f"{_DERIVED_SELECTION_FACTS}.location.countries",
                "location",
            ),
        }
        for ordering, criterion in enumerate(detail_facts, start=100):
            if criterion not in requested:
                continue
            fact_path, provider_fact_path = detail_facts[criterion]
            planned.append(
                PlannedFactRequirement(
                    criterion=criterion.value,
                    fact_path=fact_path,
                    comparison={"operator": "known"},
                    provider=FactProviderSpec(
                        provider_id=f"{source_plan_id}:selection:{criterion.value}",
                        stage=ProviderStage.DETAIL_OUTPUT,
                        parser_ref=None,
                        fact_path=provider_fact_path,
                        depends_on_fact_paths=(),
                        required_for_final=True,
                        cost_class="detail",
                        ordering=ordering,
                    ),
                    skip_when_final_keep=bool(request.scenarios),
                )
            )
    if (
        profile_available
        and SearchCriterion.EMPLOYER_GEOGRAPHIES in requested
    ):
        planned.append(
            PlannedFactRequirement(
                criterion=SearchCriterion.EMPLOYER_GEOGRAPHIES.value,
                fact_path=f"{_DERIVED_SELECTION_FACTS}.employer_geographies",
                comparison={"operator": "known"},
                provider=FactProviderSpec(
                    provider_id=f"{source_plan_id}:selection:employer_geographies",
                    stage=ProviderStage.PROFILE_OUTPUT,
                    parser_ref=None,
                    fact_path="locations",
                    depends_on_fact_paths=("company.profile_url",),
                    required_for_final=True,
                    cost_class="profile",
                    ordering=200,
                ),
                skip_when_final_keep=bool(request.scenarios),
            )
        )
    return tuple(planned)


def plan_source_fact_requirements(
    request: SearchRequest,
    *,
    source_plan_id: str,
    detail_available: bool,
    profile_available: bool,
    site_available: bool,
    company_enrichment_enabled: bool,
) -> tuple[PlannedFactRequirement, ...]:
    planned = list(
        plan_selection_fact_requirements(
            request,
            source_plan_id=source_plan_id,
            detail_available=detail_available,
            profile_available=profile_available,
        )
    )
    if detail_available:
        planned.append(
            PlannedFactRequirement(
                criterion="optional_description_enrichment",
                fact_path="description",
                comparison={"operator": "exists"},
                provider=FactProviderSpec(
                    provider_id=f"{source_plan_id}:detail-description",
                    stage=ProviderStage.DETAIL_OUTPUT,
                    parser_ref=None,
                    fact_path="description",
                    depends_on_fact_paths=(),
                    required_for_final=False,
                    cost_class="detail",
                    ordering=10,
                ),
                skip_when_final_keep=False,
            )
        )
    if not company_enrichment_enabled or not profile_available:
        return tuple(planned)
    planned.append(
        PlannedFactRequirement(
            criterion="optional_company_official_site",
            fact_path="official_site_url",
            comparison={"operator": "exists"},
            provider=FactProviderSpec(
                provider_id=f"{source_plan_id}:profile-official-site",
                stage=ProviderStage.PROFILE_OUTPUT,
                parser_ref=None,
                fact_path="official_site_url",
                depends_on_fact_paths=("company.profile_url",),
                required_for_final=False,
                cost_class="profile",
                ordering=20,
            ),
            skip_when_final_keep=False,
        )
    )
    if site_available:
        planned.append(
            PlannedFactRequirement(
                criterion="optional_company_career_endpoints",
                fact_path="career_endpoints",
                comparison={"operator": "exists"},
                provider=FactProviderSpec(
                    provider_id=f"{source_plan_id}:site-career-endpoints",
                    stage=ProviderStage.SITE_OUTPUT,
                    parser_ref=None,
                    fact_path="career_endpoints",
                    depends_on_fact_paths=("official_site_url",),
                    required_for_final=False,
                    cost_class="site",
                    ordering=30,
                ),
                skip_when_final_keep=False,
            )
        )
    return tuple(planned)
