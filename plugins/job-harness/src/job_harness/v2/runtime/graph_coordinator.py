"""Execution-scoped event coordinator for the durable scraper graph."""

from __future__ import annotations

from typing import cast

from job_harness.v2.contracts import (
    ParserRef,
    ParserRegistry,
    SearchListingInput,
    SearchRequest,
    SearchScraperBundle,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.runtime.fact_derivers import derive_selection_facts
from job_harness.v2.runtime.selection import GraphVacancySelector, keep_all
from job_harness.v2.serialization import JsonObject


class GraphCoordinator:
    def __init__(
        self,
        *,
        repository: SqliteGraphRepository,
        registry: ParserRegistry,
        owner_id: str,
        request: SearchRequest | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        self._repository = repository
        self._registry = registry
        self._owner_id = owner_id
        self._request = request
        self._selection_evaluator = (
            keep_all if request is None else GraphVacancySelector(request).evaluate
        )

    def process_once(
        self,
        execution_id: str,
        *,
        limit: int,
        lease_seconds: float,
        now: float,
    ) -> int:
        coordinator = self._repository.acquire_coordinator(
            execution_id=execution_id,
            owner_id=self._owner_id,
            lease_seconds=lease_seconds,
            now=now,
        )
        if coordinator is None:
            return 0
        events = self._repository.read_unprocessed_events(execution_id, limit=limit)
        self._repository.process_listing_event_batch(
            coordinator,
            events,
            self._registry.manifest,
            self._selection_evaluator,
            derive_selection_facts,
            self._registry.resolve_target,
            self._plan_discovered,
            now=now,
        )
        return len(events)

    def _plan_discovered(
        self,
        parser_ref: ParserRef,
        target: JsonObject,
    ) -> tuple[SearchListingInput, ...]:
        if self._request is None:
            return ()
        bundle = cast(SearchScraperBundle, self._registry.get(parser_ref))
        return bundle.plan_initial(self._request, target)
