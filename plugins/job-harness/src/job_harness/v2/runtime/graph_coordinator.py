"""Execution-scoped event coordinator for the durable scraper graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

from job_harness.v2.contracts import (
    ParserManifest,
    ParserRef,
    ParserRegistry,
    ParserType,
    SearchListingInput,
    SearchRequest,
    SearchScraperBundle,
    SelectionDecision,
    TargetParserResolver,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.runtime.fact_derivers import derive_selection_facts
from job_harness.v2.runtime.fact_requirement_planner import (
    PlannedFactRequirement,
    plan_source_fact_requirements,
)
from job_harness.v2.runtime.ranking import GraphVacancyRanker
from job_harness.v2.runtime.selection import GraphVacancySelector, keep_all
from job_harness.v2.serialization import JsonObject


class GraphCoordinator:
    def __init__(
        self,
        *,
        repository: SqliteGraphRepository,
        registry: ParserRegistry,
        target_resolver: TargetParserResolver | None = None,
        owner_id: str,
        request: SearchRequest | None = None,
        discovery_request: SearchRequest | None = None,
        requirement_scope: Literal["all", "required", "optional"] = "all",
        optional_execution_id: str | None = None,
        discovery_execution_id: str | None = None,
        company_enrichment_enabled: bool = True,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        self._repository = repository
        self._registry = registry
        self._target_resolver = target_resolver or TargetParserResolver(registry.manifests())
        self._owner_id = owner_id
        self._request = request
        self._discovery_request = discovery_request or request
        self._requirement_scope = requirement_scope
        self._optional_execution_id = optional_execution_id
        self._discovery_execution_id = discovery_execution_id
        self._company_enrichment_enabled = company_enrichment_enabled
        selector = None if request is None else GraphVacancySelector(request)
        self._preliminary_selection_evaluator = (
            keep_all if selector is None else selector.evaluate_preliminary
        )
        self._final_selection_evaluator = keep_all if selector is None else selector.evaluate
        ranker = None if request is None else GraphVacancyRanker(request)
        self._score_evaluator = (lambda _facts: 0.0) if ranker is None else ranker.score

    @property
    def final_selection_evaluator(self) -> Callable[[JsonObject], SelectionDecision]:
        return self._final_selection_evaluator

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def process_once(
        self,
        execution_id: str,
        *,
        limit: int,
        lease_seconds: float,
        now: float,
    ) -> int:
        if not self._repository.has_unprocessed_events(execution_id):
            return 0
        coordinator = self._repository.acquire_coordinator(
            execution_id=execution_id,
            owner_id=self._owner_id,
            lease_seconds=lease_seconds,
            now=now,
        )
        if coordinator is None:
            return 0
        events = self._repository.read_unprocessed_events(execution_id, limit=limit)
        if not events:
            return 0
        self._repository.process_listing_event_batch(
            coordinator,
            events,
            self._registry.manifest,
            self._preliminary_selection_evaluator,
            self._final_selection_evaluator,
            self._score_evaluator,
            derive_selection_facts,
            self._target_resolver.resolve,
            self._plan_discovered,
            self._plan_discovered_requirements,
            requirement_scope=self._requirement_scope,
            optional_execution_id=self._optional_execution_id,
            discovery_execution_id=self._discovery_execution_id,
            now=now,
        )
        return len(events)

    def _plan_discovered(
        self,
        parser_ref: ParserRef,
        target: JsonObject,
    ) -> tuple[SearchListingInput, ...]:
        if self._discovery_request is None:
            return ()
        bundle = cast(SearchScraperBundle, self._registry.get(parser_ref))
        return bundle.plan_initial(self._discovery_request, target)

    def _plan_discovered_requirements(
        self,
        source_plan_id: str,
        _manifest: ParserManifest,
        initial_inputs: tuple[SearchListingInput, ...],
    ) -> tuple[PlannedFactRequirement, ...]:
        if self._discovery_request is None or not initial_inputs:
            return ()
        provider_hint = initial_inputs[0].target_provider_id
        return plan_source_fact_requirements(
            self._discovery_request,
            source_plan_id=source_plan_id,
            detail_available=self._target_resolver.has_candidate(
                ParserType.VACANCY_DETAIL,
                provider_hint,
            ),
            profile_available=self._target_resolver.has_candidate(
                ParserType.COMPANY_PROFILE,
                provider_hint,
            ),
            site_available=self._target_resolver.has_candidate(
                ParserType.COMPANY_SITE,
                provider_hint,
            ),
            company_enrichment_enabled=self._company_enrichment_enabled,
        )
