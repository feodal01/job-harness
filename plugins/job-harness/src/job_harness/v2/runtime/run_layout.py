"""Filesystem layout for v2 search runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_harness.v2.runtime.artifacts import (
    PROCESSED_RESULTS_FILENAME,
    RAW_LISTINGS_FILENAME,
    RUN_MANIFEST_FILENAME,
    SOURCE_ATTEMPTS_FILENAME,
)


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    run_dir: Path
    raw_listings_path: Path
    source_attempts_path: Path
    run_manifest_path: Path
    processed_results_path: Path


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
            raw_listings_path=run_dir / RAW_LISTINGS_FILENAME,
            source_attempts_path=run_dir / SOURCE_ATTEMPTS_FILENAME,
            run_manifest_path=run_dir / RUN_MANIFEST_FILENAME,
            processed_results_path=run_dir / PROCESSED_RESULTS_FILENAME,
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

    def next_append_sequence(self, run_id: str) -> int:
        paths = self.existing_run(run_id)
        if paths.run_manifest_path.exists():
            return _latest_append_sequence(paths.run_manifest_path) + 1
        return _max_raw_append_sequence(paths.raw_listings_path) + 1


def _clean_run_id(run_id: str) -> str:
    value = run_id.strip()
    if not value:
        raise ValueError("run_id must be non-empty")
    if "/" in value or "\\" in value:
        raise ValueError("run_id must not contain path separators")
    return value


def _latest_append_sequence(path: Path) -> int:
    value = _read_json_object(path)
    sequence = value.get("latest_append_sequence")
    if not isinstance(sequence, int) or sequence < 0:
        raise ValueError(f"run manifest has invalid latest_append_sequence: {path}")
    return sequence


def _max_raw_append_sequence(path: Path) -> int:
    if not path.exists():
        return -1
    max_sequence = -1
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"raw listings file contains non-object JSON line: {path}")
        sequence = record.get("append_sequence")
        if not isinstance(sequence, int) or sequence < 0:
            raise ValueError(f"raw listings file has invalid append_sequence: {path}")
        max_sequence = max(max_sequence, sequence)
    return max_sequence


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value
