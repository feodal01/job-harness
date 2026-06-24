"""Ports between v2 layers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self

from job_harness.v2.contracts import (
    RawSearchRecord,
    SourceAttemptRecord,
    SourceFetchRequest,
    SourceResponseArtifact,
)
from job_harness.v2.serialization import JsonObject


class ArtifactFetcher(Protocol):
    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        """Fetch one source response artifact for a source-native request."""


class CorpusWriter(Protocol):
    def append_raw_record(self, record: RawSearchRecord) -> None:
        """Append one immutable raw listing record."""

    def append_attempt_record(self, record: SourceAttemptRecord) -> None:
        """Append one immutable source attempt record."""

    def replace_run_manifest(self, manifest: Mapping[str, object]) -> None:
        """Atomically replace the machine-readable run manifest."""


class RunStore(CorpusWriter, Protocol):
    @property
    def database_path(self) -> Path:
        """Path to the backing run database."""

    @property
    def run_id(self) -> str:
        """Run id owned by this store."""

    @property
    def append_sequence(self) -> int:
        """Reserved append sequence for the active append attempt."""

    def reserve_append_attempt(self, request: Mapping[str, object]) -> int:
        """Reserve a unique append sequence for this run."""

    def mark_append_attempt_completed(self) -> None:
        """Mark the reserved append attempt completed."""

    def mark_append_attempt_failed(self) -> None:
        """Mark the reserved append attempt failed."""

    def write_processed_results(self, payload: Mapping[str, object]) -> None:
        """Persist one processed-results snapshot."""

    def read_raw_records(self) -> tuple[JsonObject, ...]:
        """Read raw listing record payloads for the run."""

    def read_source_attempts(self) -> tuple[JsonObject, ...]:
        """Read source attempt payloads for the run."""

    def read_run_manifest(self) -> JsonObject:
        """Read the latest run manifest payload."""

    def read_processed_results(self, *, append_sequence: int | None = None) -> JsonObject:
        """Read a processed-results payload."""

    def close(self) -> None:
        """Close any backing resources."""

    def __enter__(self) -> Self:
        """Enter the run store context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the run store context."""


class RunStoreFactory(Protocol):
    def __call__(self, database_path: Path, *, run_id: str) -> RunStore:
        """Create a run store for one run database."""
