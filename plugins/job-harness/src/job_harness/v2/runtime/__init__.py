"""Strict runtime layer built on the search contracts."""

from job_harness.v2.ports import ArtifactFetcher, CorpusWriter
from job_harness.v2.runtime.catalog import SourceCatalog, SupportedSource
from job_harness.v2.runtime.config import DetailServiceConfig, RetryServiceConfig, SearchServiceConfig
from job_harness.v2.runtime.detail_enrichment import DetailEnrichmentRunner, DetailRunResult, DetailWorkItem
from job_harness.v2.runtime.errors import ClassifiedSourceError
from job_harness.v2.runtime.http import HttpArtifactFetcher
from job_harness.v2.runtime.orchestrator import (
    OrchestratorConfig,
    SearchOrchestrator,
    SearchRunResult,
)
from job_harness.v2.runtime.pipeline import SearchPipeline, SearchPipelineConfig, SearchPipelineExecution, new_run_id
from job_harness.v2.runtime.retry import RetryPolicy
from job_harness.v2.runtime.run_layout import RunLayout, RunPaths
from job_harness.v2.runtime.source_registry import build_supported_source_catalog, implemented_source_ids

__all__ = [
    "ArtifactFetcher",
    "build_supported_source_catalog",
    "ClassifiedSourceError",
    "CorpusWriter",
    "DetailEnrichmentRunner",
    "DetailRunResult",
    "DetailServiceConfig",
    "DetailWorkItem",
    "HttpArtifactFetcher",
    "implemented_source_ids",
    "new_run_id",
    "OrchestratorConfig",
    "RetryServiceConfig",
    "RetryPolicy",
    "RunLayout",
    "RunPaths",
    "SearchPipeline",
    "SearchPipelineConfig",
    "SearchPipelineExecution",
    "SearchServiceConfig",
    "SearchOrchestrator",
    "SearchRunResult",
    "SourceCatalog",
    "SupportedSource",
]
