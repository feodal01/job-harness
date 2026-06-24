"""Filesystem layout for v2 search runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.runtime.artifacts import REPORT_HTML_FILENAME, RUN_DATABASE_FILENAME


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    run_dir: Path
    database_path: Path
    report_html_path: Path


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


def _clean_run_id(run_id: str) -> str:
    value = run_id.strip()
    if not value:
        raise ValueError("run_id must be non-empty")
    if "/" in value or "\\" in value:
        raise ValueError("run_id must not contain path separators")
    return value
