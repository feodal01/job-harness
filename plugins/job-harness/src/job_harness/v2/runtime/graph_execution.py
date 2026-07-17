"""Planning and draining services for durable graph executions."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from job_harness.v2.contracts import (
    ParserInvocationSpec,
    ParserRegistry,
    ParserType,
    SearchRequest,
    SearchScraperBundle,
    TargetParserResolver,
    TaskClass,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.ports import ParserRuntimeFactory
from job_harness.v2.runtime.executors import ManagedTaskRunner
from job_harness.v2.runtime.fact_requirement_planner import plan_source_fact_requirements
from job_harness.v2.runtime.final_assembly import ExecutionNotDrainedError, FinalAssembler
from job_harness.v2.runtime.graph_coordinator import GraphCoordinator
from job_harness.v2.runtime.graph_pipeline_models import (
    GraphSearchPipelineConfig,
    PipelineDriverSpec,
)
from job_harness.v2.runtime.graph_scheduler import (
    GraphSchedulerDriver,
    GraphSearchProgress,
    GraphTaskScheduler,
)
from job_harness.v2.runtime.invocation_resources import invocation_resource_key
from job_harness.v2.runtime.ranking import GraphVacancyRanker
from job_harness.v2.serialization import JsonObject, to_jsonable
from job_harness.v2.source_catalog import ListingParserBinding


@dataclass(frozen=True)
class GraphExecutionEngine:
    config: GraphSearchPipelineConfig
    registry: ParserRegistry
    runtime_factory: ParserRuntimeFactory
    source_bindings: tuple[ListingParserBinding, ...]
    target_resolver: TargetParserResolver
    resource_key_resolver: Callable[[str], str]
    clock: Callable[[], float]

    def __post_init__(self) -> None:
        source_ids = tuple(binding.source_id for binding in self.source_bindings)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("listing parser bindings contain duplicate source ids")

    def plan_initial(
        self,
        repository: SqliteGraphRepository,
        execution_id: str,
        request: SearchRequest,
        *,
        available_at: float,
    ) -> None:
        selected = set(self.config.source_ids or request.sources)
        selected_types = {source_type.value for source_type in request.source_types}
        bindings = tuple(
            binding
            for binding in self.source_bindings
            if not selected or binding.source_id in selected
            if not selected_types or binding.source_type.value in selected_types
        )
        if not bindings:
            raise ValueError("search intent selected no listing scraper bundles")
        for binding in bindings:
            self._plan_source(repository, execution_id, request, binding, available_at)

    def _plan_source(
        self,
        repository: SqliteGraphRepository,
        execution_id: str,
        request: SearchRequest,
        binding: ListingParserBinding,
        available_at: float,
    ) -> None:
        registered = self.registry.get(binding.parser_ref)
        if registered.manifest.parser_type != ParserType.SEARCH_LISTING:
            raise TypeError(f"catalog binding is not a search parser: {binding.source_id}")
        bundle = cast(SearchScraperBundle, registered)
        manifest = bundle.manifest
        initial_inputs = bundle.plan_initial(request, {"kind": "catalog"})
        if not initial_inputs or any(item.source_id != binding.source_id for item in initial_inputs):
            raise ValueError(
                f"listing parser input disagrees with catalog source: {binding.source_id}"
            )
        multiplier = len(request.query_variants) if manifest.query_mode == "per_query" else 1
        source_plan_id = repository.create_source_plan(
            execution_id=execution_id,
            source_id=binding.source_id,
            manifest=manifest,
            queries=request.query_variants,
            unit_budget=(manifest.default_unit_budget or 1) * multiplier,
            item_budget=(manifest.default_item_budget or 1) * multiplier,
            invocation_budget=(manifest.default_invocation_budget or 1) * multiplier,
        )
        for requirement in plan_source_fact_requirements(
            request,
            source_plan_id=source_plan_id,
            detail_available=self.target_resolver.has_candidate(
                ParserType.VACANCY_DETAIL, binding.source_id
            ),
            profile_available=self.target_resolver.has_candidate(
                ParserType.COMPANY_PROFILE, binding.source_id
            ),
            site_available=self.target_resolver.has_candidate(
                ParserType.COMPANY_SITE, binding.source_id
            ),
            company_enrichment_enabled=self.config.company_enrichment_enabled,
        ):
            repository.add_fact_requirement(
                source_plan_id=source_plan_id,
                criterion=requirement.criterion,
                fact_path=requirement.fact_path,
                comparison=requirement.comparison,
                provider=requirement.provider,
                skip_when_final_keep=requirement.skip_when_final_keep,
            )
        for parser_input in initial_inputs:
            fingerprint = _fingerprint(parser_input)
            repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=manifest.ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=manifest.input_schema_id,
                    parser_input=parser_input,
                    task_class=TaskClass.LISTING,
                    task_key=(
                        f"search_listing:{manifest.parser_id}:{source_plan_id}:{fingerprint}"
                    ),
                    available_at=available_at,
                    reserved_collection_units=manifest.max_units_per_invocation,
                    resource_key=invocation_resource_key(
                        self.registry,
                        manifest.ref,
                        parser_input,
                        self.resource_key_resolver,
                    ),
                )
            )

    async def drain(
        self,
        repository: SqliteGraphRepository,
        request: SearchRequest,
        *,
        drivers: tuple[PipelineDriverSpec, ...],
        assembly_execution_id: str,
        emit_progress: bool,
    ) -> tuple[JsonObject, ...]:
        scheduler_drivers = tuple(
            self._scheduler_driver(repository, driver) for driver in drivers
        )
        stats = await GraphTaskScheduler(
            repository=repository,
            drivers=scheduler_drivers,
            concurrency=self.config.task_batch_size,
            event_batch_size=self.config.event_batch_size,
            lease_seconds=self.config.lease_seconds,
            lease_heartbeat_seconds=self.config.lease_heartbeat_seconds,
            clock=self.clock,
            progress_callback=self.config.progress_callback if emit_progress else None,
            progress_interval_seconds=self.config.progress_interval_seconds,
        ).run_until_quiescent()
        try:
            items = FinalAssembler(
                repository,
                scorer=GraphVacancyRanker(request).score,
            ).assemble(assembly_execution_id, now=self.clock()).items
        except ExecutionNotDrainedError as exc:
            raise RuntimeError(f"graph stopped making progress: {exc}") from exc
        if emit_progress and self.config.progress_callback is not None:
            finished_at = self.clock()
            self.config.progress_callback(
                GraphSearchProgress(
                    tasks_completed=stats.tasks_completed,
                    events_processed=stats.events_processed,
                    elapsed_seconds=max(0.0, finished_at - stats.started_at),
                    done=True,
                )
            )
        return items

    def _scheduler_driver(
        self,
        repository: SqliteGraphRepository,
        driver: PipelineDriverSpec,
    ) -> GraphSchedulerDriver:
        runner = ManagedTaskRunner(
            repository=repository,
            registry=self.registry,
            runtime_factory=self.runtime_factory,
            owner_id=f"runner-{secrets.token_hex(4)}",
            lease_seconds=self.config.lease_seconds,
            request_retry_policy=self.config.request_retry_policy,
            attempt_timeout_seconds=self.config.attempt_timeout_seconds,
            resource_key_resolver=self.resource_key_resolver,
            clock=self.clock,
        )
        coordinator = GraphCoordinator(
            repository=repository,
            registry=self.registry,
            target_resolver=self.target_resolver,
            owner_id=f"coordinator-{secrets.token_hex(4)}",
            request=driver.selection_request,
            discovery_request=driver.discovery_request,
            requirement_scope=driver.requirement_scope,
            optional_execution_id=driver.optional_execution_id,
            discovery_execution_id=driver.discovery_execution_id,
            company_enrichment_enabled=self.config.company_enrichment_enabled,
        )
        return GraphSchedulerDriver(
            execution_id=driver.execution_id,
            runner=runner,
            coordinator=coordinator,
            optional=driver.requirement_scope == "optional",
        )


def merge_enriched_items(
    search_items: tuple[JsonObject, ...],
    enrichment_items: tuple[JsonObject, ...],
) -> tuple[JsonObject, ...]:
    enriched_by_claim: dict[str, JsonObject] = {}
    for item in enrichment_items:
        for claim in _item_identity_claims(item):
            enriched_by_claim.setdefault(claim, item)
    merged: list[JsonObject] = []
    for item in search_items:
        enriched = next(
            (
                enriched_by_claim[claim]
                for claim in _item_identity_claims(item)
                if claim in enriched_by_claim
            ),
            None,
        )
        merged.append(item if enriched is None else _combine_items(enriched, item))
    return tuple(merged)


def merge_workflow_items(
    search_items: tuple[JsonObject, ...],
    enrichment_items: tuple[JsonObject, ...],
    discovered_items: tuple[JsonObject, ...],
) -> tuple[JsonObject, ...]:
    merged_search = merge_enriched_items(search_items, enrichment_items)
    items_by_key: dict[int, JsonObject] = {}
    claims_by_key: dict[int, set[str]] = {}
    key_by_claim: dict[str, int] = {}
    next_key = 0
    for item in (*merged_search, *discovered_items):
        claims = _item_identity_claims(item)
        matched_keys = tuple(
            dict.fromkeys(
                key_by_claim[claim]
                for claim in claims
                if claim in key_by_claim
            )
        )
        if not matched_keys:
            key = next_key
            next_key += 1
            items_by_key[key] = dict(item)
            claims_by_key[key] = set(claims)
            for claim in claims:
                key_by_claim[claim] = key
            continue
        key = matched_keys[0]
        combined = items_by_key[key]
        for duplicate_key in matched_keys[1:]:
            combined = _combine_items(combined, items_by_key.pop(duplicate_key))
            duplicate_claims = claims_by_key.pop(duplicate_key)
            claims_by_key[key].update(duplicate_claims)
            for claim in duplicate_claims:
                key_by_claim[claim] = key
        items_by_key[key] = _combine_items(combined, item)
        claims_by_key[key].update(claims)
        for claim in claims:
            key_by_claim[claim] = key
    return tuple(
        sorted(
            items_by_key.values(),
            key=lambda item: (
                -_relevance_score(item),
                str(item.get("vacancyUrl", "")),
                str(item.get("sourceId", "")),
                str(item.get("sourceListingId", "")),
            ),
        )
    )


def _combine_items(primary: JsonObject, secondary: JsonObject) -> JsonObject:
    combined = dict(primary)
    for field, value in secondary.items():
        if field not in combined or combined[field] in (None, "", [], {}):
            combined[field] = value
    combined["relevanceScore"] = max(
        _relevance_score(primary),
        _relevance_score(secondary),
    )
    variants = tuple(
        dict.fromkeys(
            (*_source_variants(primary), *_source_variants(secondary))
        )
    )
    if variants:
        combined["sourceVariants"] = list(variants)
    return combined


def _item_identity_claims(item: JsonObject) -> tuple[str, ...]:
    claims: list[str] = []
    source_id = item.get("sourceId")
    source_listing_id = item.get("sourceListingId")
    if isinstance(source_id, str) and isinstance(source_listing_id, str):
        claims.append(f"listing:{_fingerprint((source_id, source_listing_id))}")
    vacancy_url = item.get("vacancyUrl")
    if isinstance(vacancy_url, str):
        claims.append(f"url:{_normalize_url(vacancy_url)}")
    return tuple(dict.fromkeys(claims)) or (f"payload:{_fingerprint(item)}",)


def _normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _relevance_score(item: JsonObject) -> float:
    score = item.get("relevanceScore")
    return float(score) if isinstance(score, int | float) else 0.0


def _source_variants(item: JsonObject) -> tuple[str, ...]:
    variants = item.get("sourceVariants")
    if not isinstance(variants, list | tuple):
        return ()
    return tuple(value for value in variants if isinstance(value, str))


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
