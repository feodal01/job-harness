"""Append-only raw corpus writer for the contract-first runtime."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any

from job_harness.v2.contracts import RawSearchRecord, SourceAttemptRecord
from job_harness.v2.runtime.artifacts import (
    RAW_LISTINGS_FILENAME,
    RUN_MANIFEST_FILENAME,
    SOURCE_ATTEMPTS_FILENAME,
)
from job_harness.v2.runtime.serialization import to_jsonable


class RawCorpusWriter:
    """Thread-safe JSONL writer for raw listings and source attempt records."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = Path(run_dir)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._raw_listings_path = self._run_dir / RAW_LISTINGS_FILENAME
        self._source_attempts_path = self._run_dir / SOURCE_ATTEMPTS_FILENAME
        self._run_manifest_path = self._run_dir / RUN_MANIFEST_FILENAME
        self._raw_fd = os.open(
            str(self._raw_listings_path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        self._attempt_fd = os.open(
            str(self._source_attempts_path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        self._lock = threading.Lock()
        self._closed = False

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def raw_listings_path(self) -> Path:
        return self._raw_listings_path

    @property
    def source_attempts_path(self) -> Path:
        return self._source_attempts_path

    @property
    def run_manifest_path(self) -> Path:
        return self._run_manifest_path

    def append_raw_record(self, record: RawSearchRecord) -> None:
        self._append(self._raw_fd, to_jsonable(record))

    def append_attempt_record(self, record: SourceAttemptRecord) -> None:
        payload = to_jsonable(record)
        payload["record_type"] = "source_attempt"
        self._append(self._attempt_fd, payload)

    def replace_run_manifest(self, manifest: Mapping[str, object]) -> None:
        payload = json.dumps(to_jsonable(dict(manifest)), ensure_ascii=False, sort_keys=True)
        tmp_path = self._run_manifest_path.with_suffix(".json.tmp")
        tmp_fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(tmp_fd, payload.encode("utf-8"))
            os.write(tmp_fd, b"\n")
            os.fsync(tmp_fd)
        finally:
            os.close(tmp_fd)
        os.replace(tmp_path, self._run_manifest_path)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            os.close(self._raw_fd)
            os.close(self._attempt_fd)
            self._closed = True

    def __enter__(self) -> RawCorpusWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _append(self, fd: int, record: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("raw corpus writer is closed")
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        encoded = payload.encode("utf-8")
        with self._lock:
            if self._closed:
                raise RuntimeError("raw corpus writer is closed")
            os.write(fd, encoded)
            os.fsync(fd)
