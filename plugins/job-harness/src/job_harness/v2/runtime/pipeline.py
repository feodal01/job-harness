"""Explicit two-phase v2 search pipeline."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from job_harness.v2.contracts import DetailEnrichmentScraper, RawListing, SearchRequest, SourceAttemptRecord
from job_harness.v2.ports import ArtifactFetcher, RunStore, RunStoreFactory, StoredRawRecord
from job_harness.v2.postprocessing import ProcessedResults, ProcessingPhase, ResultTablePostProcessor
from job_harness.v2.presentation import render_processed_results_html
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
        layout = RunLayout(self._config.runs_dir)
        paths = _resolve_paths(layout=layout, request=request, run_id=run_id)
        service_config = self._config.service_config or SearchServiceConfig.from_package_resource()
        fetcher = self._fetcher or HttpArtifactFetcher(timeout_seconds=service_config.fetch_timeout_seconds)
        catalog = self._catalog or build_supported_source_catalog(self._config.source_ids)

        with self._run_store_factory(paths.database_path, run_id=paths.run_id) as store:
            append_sequence = store.reserve_append_attempt(to_jsonable(request))
            try:
                search_result = await self._collect_search_records(
                    request=request,
                    run_id=paths.run_id,
                    append_sequence=append_sequence,
                    catalog=catalog,
                    fetcher=fetcher,
                    store=store,
                    service_config=service_config,
                )
                pre_processed = self._process_records(
                    request=request,
                    run_id=paths.run_id,
                    append_sequence=append_sequence,
                    phase=ProcessingPhase.PRE_ENRICHMENT,
                    store=store,
                )
                store.write_processed_results(pre_processed.payload)

                pre_raw_rows = store.read_raw_record_rows()
                work_items = _detail_work_items(
                    processed_payload=pre_processed.payload,
                    raw_rows=pre_raw_rows,
                    catalog=catalog,
                )
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
                application_channel_raw_rows = store.read_raw_record_rows()
                channel_work_items = application_channel_work_items(
                    processed_payload=pre_processed.payload,
                    raw_rows=application_channel_raw_rows,
                )
                application_channel_result = await ApplicationChannelEnrichmentRunner(
                    fetcher=fetcher,
                    writer=store,
                    config=service_config.application_channels,
                    request_concurrency_by_source=service_config.detail.per_source_concurrency,
                ).run(channel_work_items)
                channel_summary = application_channel_summary(
                    total_work_items=len(channel_work_items),
                    result=application_channel_result,
                )

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
                _update_run_manifest(
                    store=store,
                    detail_summary=detail_summary,
                    application_channel_summary=channel_summary,
                    pre_result_count=pre_processed.result_count,
                    final_result_count=final_processed.result_count,
                )
                paths.report_html_path.write_text(
                    render_processed_results_html(final_processed.payload),
                    encoding="utf-8",
                )
                store.mark_append_attempt_completed()
            except Exception:
                store.mark_append_attempt_failed()
                raise

        return SearchPipelineExecution(
            run_id=search_result.run_id,
            append_sequence=append_sequence,
            paths=paths,
            attempts=search_result.attempts,
            raw_records_written=search_result.raw_records_written,
            processed_results=final_processed,
            detail_summary=detail_summary,
            application_channel_summary=channel_summary,
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
                listing=_listing_from_record(raw_record),
            )
        )
    return tuple(work_items)


def _listing_from_record(record: JsonObject) -> RawListing:
    listing = record.get("listing")
    if not isinstance(listing, dict):
        raise ValueError("raw record is missing listing object")
    return RawListing(
        source_listing_id=_optional_text(listing, "source_listing_id"),
        title=_required_text(listing, "title"),
        url=_required_text(listing, "url"),
        source=_required_text(listing, "source"),
        company=_optional_text(listing, "company"),
        country=_optional_text(listing, "country"),
        city=_optional_text(listing, "city"),
        location_text=_optional_text(listing, "location_text"),
        salary_text=_optional_text(listing, "salary_text"),
        salary_min=_optional_int(listing, "salary_min"),
        salary_max=_optional_int(listing, "salary_max"),
        salary_currency=_optional_text(listing, "salary_currency"),
        posted_at=_optional_text(listing, "posted_at"),
        remote_in_country=_optional_bool(listing, "remote_in_country"),
        remote_global=_optional_bool(listing, "remote_global"),
        relocation=_optional_bool(listing, "relocation"),
        native_grade=_optional_text(listing, "native_grade"),
        description=_optional_text(listing, "description"),
        requirements=_optional_text(listing, "requirements"),
        additional_sections=_text_mapping(listing, "additional_sections"),
        skills=_text_tuple(listing, "skills"),
        raw_text=_optional_text(listing, "raw_text"),
        raw=_object_mapping(listing, "raw"),
    )


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
) -> None:
    manifest = store.read_run_manifest()
    manifest["detail_enrichment"] = detail_summary
    manifest["application_channel_enrichment"] = application_channel_summary
    manifest["pre_enrichment_result_count"] = pre_result_count
    manifest["final_result_count"] = final_result_count
    store.replace_run_manifest(manifest)


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _optional_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _optional_bool(payload: dict[str, object], key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean or null")
    return value


def _text_mapping(payload: dict[str, object], key: str) -> dict[str, str]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    parsed: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise ValueError(f"{key} must map strings to strings")
        parsed[raw_key] = raw_value
    return parsed


def _object_mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return dict(value)


def _text_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{key} must contain strings")
        parsed.append(item)
    return tuple(parsed)
