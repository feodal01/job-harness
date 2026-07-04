"""Explicit two-phase v2 search pipeline."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from job_harness.v2.contracts import DetailEnrichmentScraper, SearchRequest, SourceAttemptRecord
from job_harness.v2.ports import ArtifactFetcher, RunStore, RunStoreFactory, StoredRawRecord
from job_harness.v2.postprocessing import ProcessedResults, ProcessingPhase, ResultTablePostProcessor
from job_harness.v2.presentation import render_processed_results_html
from job_harness.v2.runtime.application_channel_records import listing_from_record
from job_harness.v2.runtime.application_channels import (
    ApplicationChannelEnrichmentRunner,
    application_channel_summary,
    application_channel_work_items,
)
from job_harness.v2.runtime.catalog import SourceCatalog
from job_harness.v2.runtime.config import SearchServiceConfig
from job_harness.v2.runtime.detail_enrichment import DetailEnrichmentRunner, DetailRunResult, DetailWorkItem
from job_harness.v2.runtime.http import HttpArtifactFetcher
from job_harness.v2.runtime.orchestrator import OrchestratorConfig, SearchOrchestrator, SearchRunResult
from job_harness.v2.runtime.run_layout import RunLayout, RunPaths
from job_harness.v2.runtime.source_registry import build_supported_source_catalog
from job_harness.v2.serialization import JsonObject, to_jsonable


@dataclass(frozen=True)
class SearchPipelineConfig:
    runs_dir: Path = Path(".job-harness/v2/runs")
    source_ids: tuple[str, ...] = ()
    service_config: SearchServiceConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs_dir", Path(self.runs_dir))


@dataclass(frozen=True)
class SearchPipelineExecution:
    run_id: str
    append_sequence: int
    paths: RunPaths
    attempts: tuple[SourceAttemptRecord, ...]
    raw_records_written: int
    processed_results: ProcessedResults
    detail_summary: JsonObject
    application_channel_summary: JsonObject
    runtime_summary: JsonObject


class SearchPipeline:
    def __init__(
        self,
        *,
        config: SearchPipelineConfig,
        fetcher: ArtifactFetcher | None,
        postprocessor: ResultTablePostProcessor,
        run_store_factory: RunStoreFactory,
        catalog: SourceCatalog | None = None,
    ) -> None:
        self._config = config
        self._fetcher = fetcher
        self._postprocessor = postprocessor
        self._run_store_factory = run_store_factory
        self._catalog = catalog

    async def run(self, request: SearchRequest, *, run_id: str | None = None) -> SearchPipelineExecution:
        pipeline_started = monotonic()
        stage_timings: dict[str, int] = {}
        setup_started = monotonic()
        paths, service_config, fetcher, owned_fetcher, catalog = _pipeline_setup(
            config=self._config,
            request=request,
            run_id=run_id,
            fetcher=self._fetcher,
            catalog=self._catalog,
        )
        stage_timings["setup_ms"] = _elapsed_ms(setup_started)

        try:
            with self._run_store_factory(paths.database_path, run_id=paths.run_id) as store:
                append_sequence = store.reserve_append_attempt(to_jsonable(request))
                try:
                    collect_started = monotonic()
                    search_result = await self._collect_search_records(
                        request=request,
                        run_id=paths.run_id,
                        append_sequence=append_sequence,
                        catalog=catalog,
                        fetcher=fetcher,
                        store=store,
                        service_config=service_config,
                    )
                    stage_timings["source_collection_ms"] = _elapsed_ms(collect_started)
                    pre_process_started = monotonic()
                    pre_processed = self._process_records(
                        request=request,
                        run_id=paths.run_id,
                        append_sequence=append_sequence,
                        phase=ProcessingPhase.PRE_ENRICHMENT,
                        store=store,
                    )
                    store.write_processed_results(pre_processed.payload)
                    stage_timings["pre_processing_ms"] = _elapsed_ms(pre_process_started)

                    detail_plan_started = monotonic()
                    pre_raw_rows = store.read_raw_record_rows()
                    work_items = _detail_work_items(
                        processed_payload=pre_processed.payload,
                        raw_rows=pre_raw_rows,
                        catalog=catalog,
                    )
                    stage_timings["detail_planning_ms"] = _elapsed_ms(detail_plan_started)
                    detail_started = monotonic()
                    detail_result = await DetailEnrichmentRunner(
                        catalog=catalog,
                        fetcher=fetcher,
                        writer=store,
                        config=service_config.detail,
                    ).run(work_items)
                    detail_summary = _detail_summary(
                        total_work_items=len(work_items),
                        result=detail_result,
                    )
                    stage_timings["detail_enrichment_ms"] = _elapsed_ms(detail_started)
                    channel_plan_started = monotonic()
                    application_channel_raw_rows = store.read_raw_record_rows()
                    channel_work_items = application_channel_work_items(
                        processed_payload=pre_processed.payload,
                        raw_rows=application_channel_raw_rows,
                    )
                    stage_timings["application_channel_planning_ms"] = _elapsed_ms(channel_plan_started)
                    channel_started = monotonic()
                    application_channel_result = await ApplicationChannelEnrichmentRunner(
                        fetcher=fetcher,
                        writer=store,
                        config=service_config.application_channels,
                        request_concurrency_by_source=service_config.application_channels.request_concurrency_by_source,
                    ).run(channel_work_items)
                    channel_summary = application_channel_summary(
                        total_work_items=len(channel_work_items),
                        result=application_channel_result,
                    )
                    stage_timings["application_channel_enrichment_ms"] = _elapsed_ms(channel_started)

                    final_process_started = monotonic()
                    final_processed = self._process_records(
                        request=request,
                        run_id=paths.run_id,
                        append_sequence=append_sequence,
                        phase=ProcessingPhase.FINAL,
                        store=store,
                        detail_summary=detail_summary,
                        application_channel_summary=channel_summary,
                    )
                    store.write_processed_results(final_processed.payload)
                    stage_timings["final_processing_ms"] = _elapsed_ms(final_process_started)
                    report_started = monotonic()
                    paths.report_html_path.write_text(
                        render_processed_results_html(final_processed.payload),
                        encoding="utf-8",
                    )
                    stage_timings["report_render_write_ms"] = _elapsed_ms(report_started)
                    runtime_summary = _runtime_summary(stage_timings, pipeline_started=pipeline_started)
                    _update_run_manifest(
                        store=store,
                        detail_summary=detail_summary,
                        application_channel_summary=channel_summary,
                        pre_result_count=pre_processed.result_count,
                        final_result_count=final_processed.result_count,
                        runtime_summary=runtime_summary,
                    )
                    store.mark_append_attempt_completed()
                except Exception:
                    store.mark_append_attempt_failed()
                    raise
        finally:
            if owned_fetcher is not None:
                await owned_fetcher.aclose()

        return SearchPipelineExecution(
            run_id=search_result.run_id,
            append_sequence=append_sequence,
            paths=paths,
            attempts=search_result.attempts,
            raw_records_written=search_result.raw_records_written,
            processed_results=final_processed,
            detail_summary=detail_summary,
            application_channel_summary=channel_summary,
            runtime_summary=runtime_summary,
        )

    async def _collect_search_records(
        self,
        *,
        request: SearchRequest,
        run_id: str,
        append_sequence: int,
        catalog: SourceCatalog,
        fetcher: ArtifactFetcher,
        store: RunStore,
        service_config: SearchServiceConfig,
    ) -> SearchRunResult:
        orchestrator = SearchOrchestrator(
            catalog=catalog,
            fetcher=fetcher,
            writer=store,
            config=OrchestratorConfig(
                source_attempt_timeout_seconds=service_config.source_attempt_timeout_seconds,
                run_timeout_seconds=service_config.run_timeout_seconds,
                retry_policy=service_config.retry.to_retry_policy(),
            ),
        )
        return await orchestrator.run(
            request,
            run_id=run_id,
            append_sequence=append_sequence,
        )

    def _process_records(
        self,
        *,
        request: SearchRequest,
        run_id: str,
        append_sequence: int,
        phase: ProcessingPhase,
        store: RunStore,
        detail_summary: dict[str, object] | None = None,
        application_channel_summary: dict[str, object] | None = None,
    ) -> ProcessedResults:
        return self._postprocessor.process(
            request=request,
            run_id=run_id,
            append_sequence=append_sequence,
            phase=phase,
            raw_records=_raw_records_for_processing(store.read_raw_record_rows()),
            source_attempts=store.read_source_attempts(),
            detail_summary=detail_summary,
            application_channel_summary=application_channel_summary,
        )


def new_run_id() -> str:
    now = datetime.now(UTC)
    return f"r-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def _resolve_paths(
    *,
    layout: RunLayout,
    request: SearchRequest,
    run_id: str | None,
) -> RunPaths:
    if request.append_to_run_id is not None:
        if run_id is not None and run_id != request.append_to_run_id:
            raise ValueError("run_id must match append_to_run_id")
        paths = layout.existing_run(request.append_to_run_id)
        if not paths.database_path.exists():
            raise FileNotFoundError(f"v2 run database does not exist: {paths.database_path}")
        return paths

    effective_run_id = run_id or new_run_id()
    return layout.create_new_run(effective_run_id)


def _pipeline_setup(
    *,
    config: SearchPipelineConfig,
    request: SearchRequest,
    run_id: str | None,
    fetcher: ArtifactFetcher | None,
    catalog: SourceCatalog | None,
) -> tuple[RunPaths, SearchServiceConfig, ArtifactFetcher, HttpArtifactFetcher | None, SourceCatalog]:
    layout = RunLayout(config.runs_dir)
    paths = _resolve_paths(layout=layout, request=request, run_id=run_id)
    service_config = config.service_config or SearchServiceConfig.from_package_resource()
    resolved_fetcher, owned_fetcher = _pipeline_fetcher(fetcher, service_config)
    resolved_catalog = catalog or build_supported_source_catalog(_catalog_source_ids(config, request))
    return paths, service_config, resolved_fetcher, owned_fetcher, resolved_catalog


def _catalog_source_ids(config: SearchPipelineConfig, request: SearchRequest) -> tuple[str, ...]:
    if config.source_ids:
        return config.source_ids
    return request.sources


def _pipeline_fetcher(
    fetcher: ArtifactFetcher | None,
    service_config: SearchServiceConfig,
) -> tuple[ArtifactFetcher, HttpArtifactFetcher | None]:
    if fetcher is not None:
        return fetcher, None
    owned_fetcher = HttpArtifactFetcher(timeout_seconds=service_config.fetch_timeout_seconds)
    return owned_fetcher, owned_fetcher


def _raw_records_for_processing(raw_rows: tuple[StoredRawRecord, ...]) -> tuple[JsonObject, ...]:
    records: list[JsonObject] = []
    for row in raw_rows:
        records.append({**row.payload, "raw_record_id": row.raw_record_id})
    return tuple(records)


def _detail_work_items(
    *,
    processed_payload: JsonObject,
    raw_rows: tuple[StoredRawRecord, ...],
    catalog: SourceCatalog,
) -> tuple[DetailWorkItem, ...]:
    results = processed_payload.get("results")
    if not isinstance(results, list):
        raise ValueError("processed results payload must contain results list")
    raw_by_id = {row.raw_record_id: row.payload for row in raw_rows}
    work_items: list[DetailWorkItem] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("processed results must contain row objects")
        raw_record_id = result.get("raw_record_id")
        if not isinstance(raw_record_id, int):
            raise ValueError("processed result row is missing raw_record_id")
        source = _required_text(result, "source")
        scraper = catalog.get(source)
        if not isinstance(scraper, DetailEnrichmentScraper):
            continue
        if result.get("detail_fetched") is True:
            continue
        raw_record = raw_by_id.get(raw_record_id)
        if raw_record is None:
            raise ValueError(f"raw record row does not exist: {raw_record_id}")
        work_items.append(
            DetailWorkItem(
                raw_record_id=raw_record_id,
                source=source,
                query_variant=_required_text(result, "query_variant"),
                listing=listing_from_record(raw_record),
            )
        )
    return tuple(work_items)


def _detail_summary(*, total_work_items: int, result: DetailRunResult) -> JsonObject:
    return {
        "total_detail_work_items": total_work_items,
        "attempted": result.attempted,
        "enriched": result.enriched,
        "failed": result.failed,
        "stopped_sources": list(result.stopped_sources),
    }


def _update_run_manifest(
    *,
    store: RunStore,
    detail_summary: JsonObject,
    application_channel_summary: JsonObject,
    pre_result_count: int,
    final_result_count: int,
    runtime_summary: JsonObject,
) -> None:
    manifest = store.read_run_manifest()
    manifest["detail_enrichment"] = detail_summary
    manifest["application_channel_enrichment"] = application_channel_summary
    manifest["pre_enrichment_result_count"] = pre_result_count
    manifest["final_result_count"] = final_result_count
    manifest["runtime_summary"] = runtime_summary
    store.replace_run_manifest(manifest)


def _runtime_summary(stage_timings: dict[str, int], *, pipeline_started: float) -> JsonObject:
    return {
        "total_elapsed_ms": _elapsed_ms(pipeline_started),
        "stages": dict(stage_timings),
    }


def _elapsed_ms(started: float) -> int:
    return int((monotonic() - started) * 1000)


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value
