"""Application service for the v2 job search workflow."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from job_harness.v2.contracts import SearchRequest, SourceAttemptRecord
from job_harness.v2.persistence import SqliteRunStore
from job_harness.v2.ports import ArtifactFetcher, RunStoreFactory
from job_harness.v2.postprocessing import (
    ProcessedResults,
    ResultTablePostProcessor,
)
from job_harness.v2.presentation import render_processed_results_html
from job_harness.v2.runtime import (
    HttpArtifactFetcher,
    OrchestratorConfig,
    RetryPolicy,
    RunLayout,
    RunPaths,
    SearchOrchestrator,
    build_supported_source_catalog,
)
from job_harness.v2.serialization import to_jsonable

__all__ = [
    "V2SearchApplication",
    "V2SearchConfig",
    "V2SearchExecution",
    "new_run_id",
]


@dataclass(frozen=True)
class V2SearchConfig:
    runs_dir: Path = Path(".job-harness/v2/runs")
    source_ids: tuple[str, ...] = ()
    source_attempt_timeout_seconds: float = 180.0
    run_timeout_seconds: float = 360.0
    fetch_timeout_seconds: float = 15.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if self.source_attempt_timeout_seconds <= 0:
            raise ValueError("source_attempt_timeout_seconds must be > 0")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be > 0")
        if self.fetch_timeout_seconds <= 0:
            raise ValueError("fetch_timeout_seconds must be > 0")
        object.__setattr__(self, "runs_dir", Path(self.runs_dir))


@dataclass(frozen=True)
class V2SearchExecution:
    run_id: str
    append_sequence: int
    paths: RunPaths
    attempts: tuple[SourceAttemptRecord, ...]
    raw_records_written: int
    processed_results: ProcessedResults


class V2SearchApplication:
    """Run the complete v2 search workflow behind CLI/MCP-style entrypoints."""

    def __init__(
        self,
        *,
        config: V2SearchConfig | None = None,
        fetcher: ArtifactFetcher | None = None,
        postprocessor: ResultTablePostProcessor | None = None,
        run_store_factory: RunStoreFactory | None = None,
    ) -> None:
        self._config = config or V2SearchConfig()
        self._fetcher = fetcher or HttpArtifactFetcher(timeout_seconds=self._config.fetch_timeout_seconds)
        self._postprocessor = postprocessor or ResultTablePostProcessor()
        self._run_store_factory = run_store_factory or SqliteRunStore

    async def search(self, request: SearchRequest, *, run_id: str | None = None) -> V2SearchExecution:
        layout = RunLayout(self._config.runs_dir)
        paths = _resolve_paths(layout=layout, request=request, run_id=run_id)
        catalog = build_supported_source_catalog(self._config.source_ids)
        with self._run_store_factory(paths.database_path, run_id=paths.run_id) as store:
            append_sequence = store.reserve_append_attempt(to_jsonable(request))
            orchestrator = SearchOrchestrator(
                catalog=catalog,
                fetcher=self._fetcher,
                writer=store,
                config=OrchestratorConfig(
                    source_attempt_timeout_seconds=self._config.source_attempt_timeout_seconds,
                    run_timeout_seconds=self._config.run_timeout_seconds,
                    retry_policy=self._config.retry_policy,
                ),
            )
            try:
                run_result = await orchestrator.run(
                    request,
                    run_id=paths.run_id,
                    append_sequence=append_sequence,
                )
                processed = self._postprocessor.process(
                    request=request,
                    run_id=paths.run_id,
                    append_sequence=append_sequence,
                    raw_records=store.read_raw_records(),
                    source_attempts=store.read_source_attempts(),
                )
                store.write_processed_results(processed.payload)
                paths.report_html_path.write_text(
                    render_processed_results_html(processed.payload),
                    encoding="utf-8",
                )
                store.mark_append_attempt_completed()
            except Exception:
                store.mark_append_attempt_failed()
                raise
        return V2SearchExecution(
            run_id=run_result.run_id,
            append_sequence=append_sequence,
            paths=paths,
            attempts=run_result.attempts,
            raw_records_written=run_result.raw_records_written,
            processed_results=processed,
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
