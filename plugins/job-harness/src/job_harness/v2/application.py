"""Application service for the v2 job search workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.contracts import SearchRequest, SourceAttemptRecord
from job_harness.v2.persistence import SqliteRunStore
from job_harness.v2.ports import ArtifactFetcher, RunStore, RunStoreFactory
from job_harness.v2.postprocessing import (
    ProcessedResults,
    ResultTablePostProcessor,
)
from job_harness.v2.runtime import (
    RunPaths,
    SearchPipeline,
    SearchPipelineConfig,
    SearchServiceConfig,
    new_run_id,
)

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
    service_config: SearchServiceConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs_dir", Path(self.runs_dir))


@dataclass(frozen=True)
class V2SearchExecution:
    run_id: str
    append_sequence: int
    paths: RunPaths
    attempts: tuple[SourceAttemptRecord, ...]
    raw_records_written: int
    processed_results: ProcessedResults
    detail_summary: dict[str, object]


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
        self._fetcher = fetcher
        self._postprocessor = postprocessor or ResultTablePostProcessor()
        self._run_store_factory = run_store_factory or _sqlite_run_store_factory

    async def search(self, request: SearchRequest, *, run_id: str | None = None) -> V2SearchExecution:
        execution = await SearchPipeline(
            config=SearchPipelineConfig(
                runs_dir=self._config.runs_dir,
                source_ids=self._config.source_ids,
                service_config=self._config.service_config,
            ),
            fetcher=self._fetcher,
            postprocessor=self._postprocessor,
            run_store_factory=self._run_store_factory,
        ).run(request, run_id=run_id)
        return V2SearchExecution(
            run_id=execution.run_id,
            append_sequence=execution.append_sequence,
            paths=execution.paths,
            attempts=execution.attempts,
            raw_records_written=execution.raw_records_written,
            processed_results=execution.processed_results,
            detail_summary=execution.detail_summary,
        )


def _sqlite_run_store_factory(database_path: Path, *, run_id: str) -> RunStore:
    return SqliteRunStore(database_path, run_id=run_id)
