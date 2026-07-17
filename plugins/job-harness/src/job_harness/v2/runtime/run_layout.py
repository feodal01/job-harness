"""Filesystem layout for v2 search runs."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.runtime.artifacts import (
    DISCOVERED_SEARCH_RESULTS_JSON_FILENAME,
    ENRICHMENT_RESULTS_JSON_FILENAME,
    EXECUTION_JSON_FILENAME,
    REPORT_HTML_FILENAME,
    RUN_DATABASE_FILENAME,
    SEARCH_RESULTS_JSON_FILENAME,
)


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    run_dir: Path
    database_path: Path
    report_html_path: Path
    execution_json_path: Path
    search_results_json_path: Path
    enrichment_results_json_path: Path
    discovered_search_results_json_path: Path


@dataclass(frozen=True)
class RunLayout:
    runs_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs_dir", Path(self.runs_dir).resolve())

    def paths_for(self, run_id: str) -> RunPaths:
        clean_run_id = _clean_run_id(run_id)
        run_dir = self.runs_dir / clean_run_id
        return RunPaths(
            run_id=clean_run_id,
            run_dir=run_dir,
            database_path=run_dir / RUN_DATABASE_FILENAME,
            report_html_path=run_dir / REPORT_HTML_FILENAME,
            execution_json_path=run_dir / EXECUTION_JSON_FILENAME,
            search_results_json_path=run_dir / SEARCH_RESULTS_JSON_FILENAME,
            enrichment_results_json_path=run_dir / ENRICHMENT_RESULTS_JSON_FILENAME,
            discovered_search_results_json_path=(
                run_dir / DISCOVERED_SEARCH_RESULTS_JSON_FILENAME
            ),
        )

    def create_new_run(self, run_id: str) -> RunPaths:
        paths = self.paths_for(run_id)
        paths.run_dir.mkdir(parents=True, exist_ok=False)
        return paths

    def existing_run(self, run_id: str) -> RunPaths:
        paths = self.paths_for(run_id)
        if not paths.run_dir.is_dir():
            raise FileNotFoundError(f"v2 run does not exist: {paths.run_dir}")
        return paths

    def find_execution(self, execution_id: str) -> RunPaths:
        value = execution_id.strip()
        if not value:
            raise ValueError("execution_id must be non-empty")
        matches: list[RunPaths] = []
        for database_path in sorted(self.runs_dir.glob(f"*/{RUN_DATABASE_FILENAME}")):
            try:
                with closing(sqlite3.connect(database_path)) as connection:
                    row = connection.execute(
                        """
                        SELECT run_id FROM search_executions
                        WHERE execution_id = ?
                        """,
                        (value,),
                    ).fetchone()
            except sqlite3.DatabaseError:
                continue
            if row is not None:
                matches.append(self.existing_run(str(row[0])))
        if not matches:
            raise FileNotFoundError(f"v2 execution was not found: {value}")
        if len(matches) > 1:
            raise RuntimeError(f"execution id is not unique across run directories: {value}")
        return matches[0]


def _clean_run_id(run_id: str) -> str:
    value = run_id.strip()
    if not value:
        raise ValueError("run_id must be non-empty")
    if "/" in value or "\\" in value:
        raise ValueError("run_id must not contain path separators")
    return value
