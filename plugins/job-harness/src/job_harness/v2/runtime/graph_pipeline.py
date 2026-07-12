"""Durable data-dependency graph search pipeline."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from job_harness.v2.contracts import (
    FactProviderSpec,
    ParserInvocationSpec,
    ParserRef,
    ParserRegistry,
    ParserType,
    ProviderStage,
    SearchRequest,
    TaskClass,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.ports import ParserRuntimeFactory
from job_harness.v2.presentation import render_processed_results_html
from job_harness.v2.runtime.executors import ManagedTaskRunner
from job_harness.v2.runtime.final_assembly import ExecutionNotDrainedError, FinalAssembler
from job_harness.v2.runtime.graph_coordinator import GraphCoordinator
from job_harness.v2.runtime.run_layout import RunLayout, RunPaths
from job_harness.v2.serialization import JsonObject, to_jsonable

_MAX_DRAIN_ITERATIONS = 100_000


@dataclass(frozen=True)
class GraphSearchPipelineConfig:
    runs_dir: Path = Path(".job-harness/v2/runs")
    source_ids: tuple[str, ...] = ()
    task_batch_size: int = 16
    event_batch_size: int = 100
    lease_seconds: float = 300.0
    execution_timeout_seconds: float = 360.0
    discovery_plan_budget: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs_dir", Path(self.runs_dir))
        if self.task_batch_size < 1 or self.event_batch_size < 1:
            raise ValueError("graph batch sizes must be >= 1")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if self.execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds must be > 0")
        if self.discovery_plan_budget < 0:
            raise ValueError("discovery_plan_budget must be >= 0")


@dataclass(frozen=True)
class GraphSearchPipelineExecution:
    run_id: str
    execution_id: str
    append_sequence: int
    paths: RunPaths
    final_items: tuple[JsonObject, ...]
    processed_payload: JsonObject


class GraphSearchPipeline:
    def __init__(
        self,
        *,
        config: GraphSearchPipelineConfig,
        registry: ParserRegistry,
        runtime_factory: ParserRuntimeFactory,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._registry = registry
        self._runtime_factory = runtime_factory
        self._clock = clock

    async def run(self, request: SearchRequest, *, run_id: str | None = None) -> GraphSearchPipelineExecution:
        paths = self._resolve_paths(request, run_id)
        repository = SqliteGraphRepository(paths.database_path)
        try:
            append_sequence = repository.next_append_sequence(paths.run_id)
            created_at = self._clock()
            execution_id = repository.create_execution(
                run_id=paths.run_id,
                intent=_json_object(request),
                append_sequence=append_sequence,
                policy_version="selection-v1",
                runtime_config_version="runtime-v1",
                deadline_at=created_at + self._config.execution_timeout_seconds,
                discovery_plan_budget=self._config.discovery_plan_budget,
                now=created_at,
            )
            self._plan_initial_graph(repository, execution_id, request)
            final_items = await self._drain(repository, execution_id, request)
            processed_payload = _processed_payload(
                run_id=paths.run_id,
                execution_id=execution_id,
                append_sequence=append_sequence,
                request=request,
                final_items=final_items,
            )
            paths.report_html_path.write_text(
                render_processed_results_html(processed_payload),
                encoding="utf-8",
            )
        finally:
            repository.close()
        return GraphSearchPipelineExecution(
            run_id=paths.run_id,
            execution_id=execution_id,
            append_sequence=append_sequence,
            paths=paths,
            final_items=final_items,
            processed_payload=processed_payload,
        )

    def _plan_initial_graph(
        self,
        repository: SqliteGraphRepository,
        execution_id: str,
        request: SearchRequest,
    ) -> None:
        selected = set(self._config.source_ids or request.sources)
        selected_types = {source_type.value for source_type in request.source_types}
        bundles = tuple(
            bundle
            for bundle in self._registry.search_bundles()
            if not selected or bundle.manifest.parser_id.removesuffix(".search") in selected
            if not selected_types or selected_types.intersection(bundle.manifest.source_kinds)
        )
        if not bundles:
            raise ValueError("search intent selected no listing scraper bundles")
        for bundle in bundles:
            manifest = bundle.manifest
            source_id = manifest.parser_id.removesuffix(".search")
            initial_inputs = bundle.plan_initial(request, {"kind": "catalog"})
            source_plan_id = repository.create_source_plan(
                execution_id=execution_id,
                source_id=source_id,
                manifest=manifest,
                queries=request.query_variants,
                unit_budget=manifest.default_unit_budget or 1,
                item_budget=manifest.default_item_budget or 1,
                invocation_budget=manifest.default_invocation_budget or 1,
            )
            detail_ref = ParserRef(f"{source_id}.detail", "1.0")
            if self._registry.contains(detail_ref):
                repository.add_fact_requirement(
                    source_plan_id=source_plan_id,
                    criterion="optional_description_enrichment",
                    fact_path="description",
                    comparison={"operator": "exists"},
                    provider=FactProviderSpec(
                        provider_id=f"{source_plan_id}:detail-description",
                        stage=ProviderStage.DETAIL_OUTPUT,
                        parser_ref=detail_ref,
                        fact_path="description",
                        depends_on_fact_paths=(),
                        required_for_final=False,
                        cost_class="detail",
                        ordering=10,
                    ),
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
                        task_key=f"search_listing:{manifest.parser_id}:{source_plan_id}:{fingerprint}",
                        available_at=0.0,
                        reserved_collection_units=manifest.max_units_per_invocation,
                    )
                )

    async def _drain(
        self,
        repository: SqliteGraphRepository,
        execution_id: str,
        request: SearchRequest,
    ) -> tuple[JsonObject, ...]:
        runner = ManagedTaskRunner(
            repository=repository,
            registry=self._registry,
            runtime_factory=self._runtime_factory,
            owner_id=f"runner-{secrets.token_hex(4)}",
            lease_seconds=self._config.lease_seconds,
            clock=self._clock,
        )
        coordinator = GraphCoordinator(
            repository=repository,
            registry=self._registry,
            owner_id=f"coordinator-{secrets.token_hex(4)}",
            request=request,
        )
        for _ in range(_MAX_DRAIN_ITERATIONS):
            now = self._clock()
            repository.settle_deadline(execution_id, now=now)
            ran = await runner.run_once(
                execution_id,
                limit=self._config.task_batch_size,
                now=now,
            )
            processed = coordinator.process_once(
                execution_id,
                limit=self._config.event_batch_size,
                lease_seconds=self._config.lease_seconds,
                now=self._clock(),
            )
            if ran or processed:
                continue
            try:
                return FinalAssembler(repository).assemble(
                    execution_id,
                    now=self._clock(),
                ).items
            except ExecutionNotDrainedError as exc:
                raise RuntimeError(f"graph stopped making progress: {exc}") from exc
        raise RuntimeError("graph exceeded the maximum drain iterations")

    def _resolve_paths(self, request: SearchRequest, run_id: str | None) -> RunPaths:
        layout = RunLayout(self._config.runs_dir)
        append_run_id = request.append_to_run_id
        if append_run_id is not None:
            if run_id is not None and run_id != append_run_id:
                raise ValueError("run_id must match append_to_run_id")
            return layout.existing_run(append_run_id)
        resolved_run_id = run_id or _new_run_id()
        return layout.create_new_run(resolved_run_id)


def _processed_payload(
    *,
    run_id: str,
    execution_id: str,
    append_sequence: int,
    request: SearchRequest,
    final_items: tuple[JsonObject, ...],
) -> JsonObject:
    return {
        "schema_version": 2,
        "record_type": "processed_results",
        "phase": "final",
        "run_id": run_id,
        "execution_id": execution_id,
        "append_sequence": append_sequence,
        "search_request": _json_object(request),
        "raw_records_read": len(final_items),
        "result_count": len(final_items),
        "results": list(final_items),
        "filtered_out_results": [],
    }


def _fingerprint(value: object) -> str:
    payload = json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _json_object(value: object) -> JsonObject:
    payload = to_jsonable(value)
    if not isinstance(payload, dict):
        raise TypeError("value must serialize to a JSON object")
    return payload


def _new_run_id() -> str:
    now = datetime.now(UTC)
    return f"r-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
