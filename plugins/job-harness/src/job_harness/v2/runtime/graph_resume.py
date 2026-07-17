"""Durable workflow resume and artifact repair."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import SearchRequest, search_request_from_json
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.runtime.execution_artifacts import (
    required_int,
    required_object,
    required_text,
)
from job_harness.v2.runtime.final_assembly import FinalAssembler
from job_harness.v2.runtime.graph_artifacts import GraphArtifactManager
from job_harness.v2.runtime.graph_execution import (
    GraphExecutionEngine,
    merge_workflow_items,
)
from job_harness.v2.runtime.graph_pipeline_models import (
    GraphSearchPipelineExecution,
    PipelineDriverSpec,
)
from job_harness.v2.runtime.ranking import GraphVacancyRanker
from job_harness.v2.runtime.run_layout import RunLayout
from job_harness.v2.serialization import JsonObject

_ACTIVE_STATUSES = frozenset({"running", "stopping"})


@dataclass(frozen=True)
class GraphWorkflowResumer:
    engine: GraphExecutionEngine
    artifacts: GraphArtifactManager

    async def resume(self, execution_id: str) -> GraphSearchPipelineExecution:
        paths = RunLayout(self.engine.config.runs_dir).find_execution(execution_id)
        repository = SqliteGraphRepository(paths.database_path)
        try:
            workflow = repository.workflow_snapshot(execution_id)
            request = search_request_from_json(required_object(workflow, "intent"))
            append_sequence = required_int(workflow, "append_sequence")
            executions = required_object(workflow, "executions")
            search = required_object(executions, "search")
            enrichment = required_object(executions, "enrichment")
            discovered = required_object(executions, "discovered_search")
            self._validate(executions)
            search_execution_id = required_text(search, "execution_id")
            enrichment_execution_id = required_text(enrichment, "execution_id")
            discovered_execution_id = required_text(discovered, "execution_id")

            search_items = await self._resume_search(
                repository,
                request=request,
                search=search,
                enrichment=enrichment,
                discovered=discovered,
            )
            self.artifacts.finalize_snapshot(
                repository,
                paths=paths,
                execution_id=search_execution_id,
                execution_kind="search",
                append_sequence=append_sequence,
                items=search_items,
                artifact_name="search_results",
                artifact_path=paths.search_results_json_path,
            )

            workflow = repository.workflow_snapshot(search_execution_id)
            executions = required_object(workflow, "executions")
            enrichment = required_object(executions, "enrichment")
            discovered = required_object(executions, "discovered_search")
            enrichment_items, discovered_items = await self._resume_children(
                repository,
                request=request,
                enrichment=enrichment,
                discovered=discovered,
            )
            self.artifacts.finalize_snapshot(
                repository,
                paths=paths,
                execution_id=discovered_execution_id,
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
            processed, receipt = self.artifacts.finalize_workflow(
                repository,
                paths=paths,
                request=request,
                execution_id=search_execution_id,
                enrichment_execution_id=enrichment_execution_id,
                discovered_search_execution_id=discovered_execution_id,
                append_sequence=append_sequence,
                final_items=final_items,
                enrichment_items=enrichment_items,
            )
        finally:
            repository.close()
        return GraphSearchPipelineExecution(
            run_id=paths.run_id,
            execution_id=search_execution_id,
            enrichment_execution_id=enrichment_execution_id,
            discovered_search_execution_id=discovered_execution_id,
            append_sequence=append_sequence,
            paths=paths,
            final_items=final_items,
            processed_payload=processed,
            receipt=receipt,
        )

    async def _resume_search(
        self,
        repository: SqliteGraphRepository,
        *,
        request: SearchRequest,
        search: JsonObject,
        enrichment: JsonObject,
        discovered: JsonObject,
    ) -> tuple[JsonObject, ...]:
        execution_id = required_text(search, "execution_id")
        if required_text(search, "status") not in _ACTIVE_STATUSES:
            return repository.final_items(execution_id)
        return await self.engine.drain(
            repository,
            request,
            drivers=_initial_drivers(
                request=request,
                search=search,
                enrichment=enrichment,
                discovered=discovered,
            ),
            assembly_execution_id=execution_id,
            emit_progress=True,
        )

    async def _resume_children(
        self,
        repository: SqliteGraphRepository,
        *,
        request: SearchRequest,
        enrichment: JsonObject,
        discovered: JsonObject,
    ) -> tuple[tuple[JsonObject, ...], tuple[JsonObject, ...]]:
        enrichment_id = required_text(enrichment, "execution_id")
        discovered_id = required_text(discovered, "execution_id")
        enrichment_active = required_text(enrichment, "status") in _ACTIVE_STATUSES
        discovered_active = required_text(discovered, "status") in _ACTIVE_STATUSES
        enrichment_items: tuple[JsonObject, ...] | None = None
        discovered_items: tuple[JsonObject, ...] | None = None
        drivers = _child_drivers(
            request=request,
            enrichment=enrichment,
            discovered=discovered,
        )
        if drivers:
            assembly_id = enrichment_id if enrichment_active else discovered_id
            assembled = await self.engine.drain(
                repository,
                request,
                drivers=drivers,
                assembly_execution_id=assembly_id,
                emit_progress=False,
            )
            if enrichment_active:
                enrichment_items = assembled
            else:
                discovered_items = assembled
        if enrichment_items is None:
            enrichment_items = repository.final_items(enrichment_id)
        if discovered_items is None:
            discovered_items = (
                FinalAssembler(
                    repository,
                    scorer=GraphVacancyRanker(request).score,
                ).assemble(discovered_id, now=self.engine.clock()).items
                if discovered_active
                else repository.final_items(discovered_id)
            )
        return enrichment_items, discovered_items

    @staticmethod
    def _validate(executions: JsonObject) -> None:
        for kind in ("search", "enrichment", "discovered_search"):
            execution = required_object(executions, kind)
            if required_text(execution, "runtime_config_version") != "runtime-v2":
                raise RuntimeError(f"unsupported persisted runtime config for {kind}")
            if required_text(execution, "status") == "failed":
                raise RuntimeError(f"cannot resume failed {kind} execution")


def _initial_drivers(
    *,
    request: SearchRequest,
    search: JsonObject,
    enrichment: JsonObject,
    discovered: JsonObject,
) -> tuple[PipelineDriverSpec, ...]:
    enrichment_active = required_text(enrichment, "status") in _ACTIVE_STATUSES
    discovered_active = required_text(discovered, "status") in _ACTIVE_STATUSES
    drivers = [
        PipelineDriverSpec(
            execution_id=required_text(search, "execution_id"),
            selection_request=request,
            discovery_request=request if discovered_active else None,
            requirement_scope="required",
            optional_execution_id=(
                required_text(enrichment, "execution_id") if enrichment_active else None
            ),
            discovery_execution_id=(
                required_text(discovered, "execution_id") if discovered_active else None
            ),
        )
    ]
    drivers.extend(
        _child_drivers(
            request=request,
            enrichment=enrichment,
            discovered=discovered,
        )
    )
    return tuple(drivers)


def _child_drivers(
    *,
    request: SearchRequest,
    enrichment: JsonObject,
    discovered: JsonObject,
) -> tuple[PipelineDriverSpec, ...]:
    discovered_active = required_text(discovered, "status") in _ACTIVE_STATUSES
    enrichment_active = required_text(enrichment, "status") in _ACTIVE_STATUSES
    drivers: list[PipelineDriverSpec] = []
    if discovered_active:
        drivers.append(
            PipelineDriverSpec(
                execution_id=required_text(discovered, "execution_id"),
                selection_request=request,
                discovery_request=None,
                requirement_scope="required",
            )
        )
    if enrichment_active:
        drivers.append(
            PipelineDriverSpec(
                execution_id=required_text(enrichment, "execution_id"),
                selection_request=None,
                discovery_request=request if discovered_active else None,
                requirement_scope="optional",
                discovery_execution_id=(
                    required_text(discovered, "execution_id")
                    if discovered_active
                    else None
                ),
            )
        )
    return tuple(drivers)
