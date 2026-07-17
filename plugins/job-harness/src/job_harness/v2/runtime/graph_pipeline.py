"""Durable data-dependency graph search pipeline."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from job_harness.v2.contracts import (
    ParserRegistry,
    SearchRequest,
    TargetParserResolver,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.ports import ParserRuntimeFactory
from job_harness.v2.runtime.atomic_artifacts import atomic_write_bytes
from job_harness.v2.runtime.final_assembly import FinalAssembler
from job_harness.v2.runtime.graph_artifacts import GraphArtifactManager
from job_harness.v2.runtime.graph_execution import (
    GraphExecutionEngine,
    merge_workflow_items,
)
from job_harness.v2.runtime.graph_pipeline_models import (
    GraphSearchPipelineConfig,
    GraphSearchPipelineExecution,
    PipelineDriverSpec,
)
from job_harness.v2.runtime.graph_resume import GraphWorkflowResumer
from job_harness.v2.runtime.ranking import GraphVacancyRanker
from job_harness.v2.runtime.run_layout import RunLayout, RunPaths
from job_harness.v2.serialization import JsonObject, to_jsonable
from job_harness.v2.source_catalog import ListingParserBinding, listing_parser_bindings


class GraphSearchPipeline:
    def __init__(
        self,
        *,
        config: GraphSearchPipelineConfig,
        registry: ParserRegistry,
        runtime_factory: ParserRuntimeFactory,
        source_bindings: Iterable[ListingParserBinding] | None = None,
        clock: Callable[[], float] = time.time,
        artifact_writer: Callable[[Path, bytes], None] = atomic_write_bytes,
        resource_key_resolver: Callable[[str], str] | None = None,
    ) -> None:
        bindings = tuple(
            listing_parser_bindings() if source_bindings is None else source_bindings
        )
        target_resolver = TargetParserResolver(registry.manifests())
        self._config = config
        self._clock = clock
        self._engine = GraphExecutionEngine(
            config=config,
            registry=registry,
            runtime_factory=runtime_factory,
            source_bindings=bindings,
            target_resolver=target_resolver,
            resource_key_resolver=resource_key_resolver or _identity_resource_key,
            clock=clock,
        )
        self._artifacts = GraphArtifactManager(clock=clock, writer=artifact_writer)
        self._resumer = GraphWorkflowResumer(
            engine=self._engine,
            artifacts=self._artifacts,
        )

    async def run(
        self,
        request: SearchRequest,
        *,
        run_id: str | None = None,
    ) -> GraphSearchPipelineExecution:
        paths = self._resolve_paths(request, run_id)
        repository = SqliteGraphRepository(paths.database_path)
        try:
            append_sequence = repository.next_append_sequence(paths.run_id)
            created_at = self._clock()
            execution_id, enrichment_id, discovered_id = self._create_executions(
                repository,
                paths=paths,
                request=request,
                append_sequence=append_sequence,
                created_at=created_at,
            )
            self._engine.plan_initial(
                repository,
                execution_id,
                request,
                available_at=created_at,
            )
            search_items = await self._engine.drain(
                repository,
                request,
                drivers=_initial_drivers(
                    execution_id,
                    enrichment_id,
                    discovered_id,
                    request,
                ),
                assembly_execution_id=execution_id,
                emit_progress=True,
            )
            self._artifacts.finalize_snapshot(
                repository,
                paths=paths,
                execution_id=execution_id,
                execution_kind="search",
                append_sequence=append_sequence,
                items=search_items,
                artifact_name="search_results",
                artifact_path=paths.search_results_json_path,
            )
            enrichment_items = await self._engine.drain(
                repository,
                request,
                drivers=_child_drivers(enrichment_id, discovered_id, request),
                assembly_execution_id=enrichment_id,
                emit_progress=False,
            )
            discovered_items = FinalAssembler(
                repository,
                scorer=GraphVacancyRanker(request).score,
            ).assemble(discovered_id, now=self._clock()).items
            self._artifacts.finalize_snapshot(
                repository,
                paths=paths,
                execution_id=discovered_id,
                execution_kind="discovered_search",
                append_sequence=append_sequence,
                items=discovered_items,
                artifact_name="discovered_search_results",
                artifact_path=paths.discovered_search_results_json_path,
            )
            final_items = merge_workflow_items(
                search_items,
                enrichment_items,
                discovered_items,
            )
            processed, receipt = self._artifacts.finalize_workflow(
                repository,
                paths=paths,
                request=request,
                execution_id=execution_id,
                enrichment_execution_id=enrichment_id,
                discovered_search_execution_id=discovered_id,
                append_sequence=append_sequence,
                final_items=final_items,
                enrichment_items=enrichment_items,
            )
        finally:
            repository.close()
        return GraphSearchPipelineExecution(
            run_id=paths.run_id,
            execution_id=execution_id,
            enrichment_execution_id=enrichment_id,
            discovered_search_execution_id=discovered_id,
            append_sequence=append_sequence,
            paths=paths,
            final_items=final_items,
            processed_payload=processed,
            receipt=receipt,
        )

    async def resume_execution(
        self,
        execution_id: str,
    ) -> GraphSearchPipelineExecution:
        return await self._resumer.resume(execution_id)

    def _create_executions(
        self,
        repository: SqliteGraphRepository,
        *,
        paths: RunPaths,
        request: SearchRequest,
        append_sequence: int,
        created_at: float,
    ) -> tuple[str, str, str]:
        intent = _json_object(request)
        active_runtime_budget_ms = round(
            self._config.execution_timeout_seconds * 1000
        )
        execution_id = repository.create_execution(
            run_id=paths.run_id,
            intent=intent,
            append_sequence=append_sequence,
            policy_version="selection-v2",
            runtime_config_version="runtime-v2",
            active_runtime_budget_ms=active_runtime_budget_ms,
            discovery_plan_budget=self._config.discovery_plan_budget,
            now=created_at,
        )
        enrichment_id = repository.create_execution(
            run_id=paths.run_id,
            intent=intent,
            append_sequence=append_sequence,
            policy_version="enrichment-v1",
            runtime_config_version="runtime-v2",
            active_runtime_budget_ms=active_runtime_budget_ms,
            discovery_plan_budget=0,
            speculative_admission_budget=25,
            execution_kind="enrichment",
            parent_execution_id=execution_id,
            now=created_at,
        )
        discovered_id = repository.create_execution(
            run_id=paths.run_id,
            intent=intent,
            append_sequence=append_sequence,
            policy_version="discovered-search-v1",
            runtime_config_version="runtime-v2",
            active_runtime_budget_ms=active_runtime_budget_ms,
            discovery_plan_budget=self._config.discovery_plan_budget,
            execution_kind="discovered_search",
            parent_execution_id=enrichment_id,
            now=created_at,
        )
        return execution_id, enrichment_id, discovered_id

    def _resolve_paths(self, request: SearchRequest, run_id: str | None) -> RunPaths:
        layout = RunLayout(self._config.runs_dir)
        append_run_id = request.append_to_run_id
        if append_run_id is not None:
            if run_id is not None and run_id != append_run_id:
                raise ValueError("run_id must match append_to_run_id")
            return layout.existing_run(append_run_id)
        return layout.create_new_run(run_id or _new_run_id())


def _initial_drivers(
    execution_id: str,
    enrichment_id: str,
    discovered_id: str,
    request: SearchRequest,
) -> tuple[PipelineDriverSpec, ...]:
    return (
        PipelineDriverSpec(
            execution_id=execution_id,
            selection_request=request,
            discovery_request=request,
            requirement_scope="required",
            optional_execution_id=enrichment_id,
            discovery_execution_id=discovered_id,
        ),
        PipelineDriverSpec(
            execution_id=discovered_id,
            selection_request=request,
            discovery_request=None,
            requirement_scope="required",
        ),
        PipelineDriverSpec(
            execution_id=enrichment_id,
            selection_request=None,
            discovery_request=request,
            requirement_scope="optional",
            discovery_execution_id=discovered_id,
        ),
    )


def _child_drivers(
    enrichment_id: str,
    discovered_id: str,
    request: SearchRequest,
) -> tuple[PipelineDriverSpec, ...]:
    return (
        PipelineDriverSpec(
            execution_id=discovered_id,
            selection_request=request,
            discovery_request=None,
            requirement_scope="required",
        ),
        PipelineDriverSpec(
            execution_id=enrichment_id,
            selection_request=None,
            discovery_request=request,
            requirement_scope="optional",
            discovery_execution_id=discovered_id,
        ),
    )


def _json_object(value: object) -> JsonObject:
    payload = to_jsonable(value)
    if not isinstance(payload, dict):
        raise TypeError("value must serialize to a JSON object")
    return payload


def _new_run_id() -> str:
    now = datetime.now(UTC)
    return f"r-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _identity_resource_key(host: str) -> str:
    return host
