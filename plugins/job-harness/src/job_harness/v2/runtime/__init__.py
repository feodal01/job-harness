"""Strict runtime layer built on the search contracts."""

from job_harness.v2.ports import ArtifactFetcher, CorpusWriter
from job_harness.v2.runtime.catalog import SourceCatalog, SupportedSource
from job_harness.v2.runtime.errors import ClassifiedSourceError
from job_harness.v2.runtime.http import HttpArtifactFetcher
from job_harness.v2.runtime.orchestrator import (
    OrchestratorConfig,
    SearchOrchestrator,
    SearchRunResult,
)
from job_harness.v2.runtime.retry import RetryPolicy
from job_harness.v2.runtime.run_layout import RunLayout, RunPaths
from job_harness.v2.runtime.source_registry import build_supported_source_catalog, implemented_source_ids

__all__ = [
    "ArtifactFetcher",
    "build_supported_source_catalog",
    "ClassifiedSourceError",
    "CorpusWriter",
    "HttpArtifactFetcher",
    "implemented_source_ids",
    "OrchestratorConfig",
    "RetryPolicy",
    "RunLayout",
    "RunPaths",
    "SearchOrchestrator",
    "SearchRunResult",
    "SourceCatalog",
    "SupportedSource",
]
