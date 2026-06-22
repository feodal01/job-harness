"""Ports used by the v2 application layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from job_harness.v2.contracts import (
    RawSearchRecord,
    SourceAttemptRecord,
    SourceFetchRequest,
    SourceResponseArtifact,
)


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
