"""Crash-safe artifact finalization for graph workflow executions."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.contracts import SearchRequest
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.presentation import render_processed_results_html
from job_harness.v2.runtime.atomic_artifacts import artifact_for_bytes, verify_artifact
from job_harness.v2.runtime.execution_artifacts import (
    execution_receipt,
    execution_snapshot_payload,
    processed_payload,
    required_int,
    required_object,
    required_text,
)
from job_harness.v2.runtime.public_projection import public_vacancy_projection
from job_harness.v2.runtime.run_layout import RunPaths
from job_harness.v2.serialization import JsonObject


@dataclass(frozen=True)
class _ArtifactContent:
    name: str
    path: Path
    content: bytes
    schema_version: int = 2


@dataclass(frozen=True)
class GraphArtifactManager:
    clock: Callable[[], float]
    writer: Callable[[Path, bytes], None]

    def finalize_snapshot(
        self,
        repository: SqliteGraphRepository,
        *,
        paths: RunPaths,
        execution_id: str,
        execution_kind: str,
        append_sequence: int,
        items: tuple[JsonObject, ...],
        artifact_name: str,
        artifact_path: Path,
    ) -> JsonObject:
        diagnostics = self.completed_diagnostics(repository, execution_id)
        snapshot = execution_snapshot_payload(
            run_id=paths.run_id,
            execution_id=execution_id,
            execution_kind=execution_kind,
            append_sequence=append_sequence,
            items=items,
            diagnostics=diagnostics,
        )
        self._finalize(
            repository,
            execution_id,
            contents=(
                _ArtifactContent(
                    name=artifact_name,
                    path=artifact_path,
                    content=_json_bytes(snapshot),
                ),
            ),
        )
        return diagnostics

    def finalize_workflow(
        self,
        repository: SqliteGraphRepository,
        *,
        paths: RunPaths,
        request: SearchRequest,
        execution_id: str,
        enrichment_execution_id: str,
        discovered_search_execution_id: str,
        append_sequence: int,
        final_items: tuple[JsonObject, ...],
        enrichment_items: tuple[JsonObject, ...],
    ) -> tuple[JsonObject, JsonObject]:
        filtered_items = repository.project_filtered_vacancies(
            execution_id,
            projector=public_vacancy_projection,
        )
        diagnostics = repository.execution_diagnostics(execution_id)
        enrichment_diagnostics = self.completed_diagnostics(
            repository,
            enrichment_execution_id,
        )
        discovered_diagnostics = repository.execution_diagnostics(
            discovered_search_execution_id
        )
        results = processed_payload(
            run_id=paths.run_id,
            execution_id=execution_id,
            append_sequence=append_sequence,
            request=request,
            final_items=final_items,
            filtered_items=filtered_items,
            raw_records_read=required_int(diagnostics, "listing_observation_count"),
            execution_quality=required_text(diagnostics, "execution_quality"),
            source_coverage=required_object(diagnostics, "source_coverage"),
        )
        receipt = execution_receipt(
            paths=paths,
            execution_id=execution_id,
            enrichment_execution_id=enrichment_execution_id,
            discovered_search_execution_id=discovered_search_execution_id,
            append_sequence=append_sequence,
            diagnostics=diagnostics,
            enrichment_diagnostics=enrichment_diagnostics,
            discovered_search_diagnostics=discovered_diagnostics,
        )
        enrichment_snapshot = execution_snapshot_payload(
            run_id=paths.run_id,
            execution_id=enrichment_execution_id,
            execution_kind="enrichment",
            append_sequence=append_sequence,
            items=enrichment_items,
            diagnostics=enrichment_diagnostics,
        )
        self._finalize(
            repository,
            enrichment_execution_id,
            contents=(
                _ArtifactContent(
                    name="enrichment_results",
                    path=paths.enrichment_results_json_path,
                    content=_json_bytes(enrichment_snapshot),
                ),
                _ArtifactContent(
                    name="report_html",
                    path=paths.report_html_path,
                    content=render_processed_results_html(results).encode("utf-8"),
                ),
                _ArtifactContent(
                    name="execution_receipt",
                    path=paths.execution_json_path,
                    content=_json_bytes(receipt),
                ),
            ),
        )
        return results, receipt

    @staticmethod
    def completed_diagnostics(
        repository: SqliteGraphRepository,
        execution_id: str,
    ) -> JsonObject:
        diagnostics = dict(repository.execution_diagnostics(execution_id))
        diagnostics["execution_status"] = "completed"
        return diagnostics

    def _finalize(
        self,
        repository: SqliteGraphRepository,
        execution_id: str,
        *,
        contents: tuple[_ArtifactContent, ...],
    ) -> None:
        expected = tuple(
            artifact_for_bytes(
                name=content.name,
                path=content.path,
                schema_version=content.schema_version,
                content=content.content,
            )
            for content in contents
        )
        repository.prepare_execution_artifacts(
            execution_id,
            artifacts=expected,
            now=self.clock(),
        )
        for content in contents:
            self.writer(content.path, content.content)
        verified = tuple(verify_artifact(artifact) for artifact in expected)
        repository.complete_execution_artifacts(
            execution_id,
            verified_artifacts=verified,
            now=self.clock(),
        )


def _json_bytes(payload: JsonObject) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
