"""Configuration and result models for the graph search pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from job_harness.v2.contracts import SearchRequest
from job_harness.v2.runtime.graph_scheduler import GraphSearchProgress
from job_harness.v2.runtime.request_retry import RequestRetryPolicy
from job_harness.v2.runtime.run_layout import RunPaths
from job_harness.v2.serialization import JsonObject

_MAX_EVENT_BATCH_SIZE = 20


@dataclass(frozen=True)
class GraphSearchPipelineConfig:
    runs_dir: Path = Path(".job-harness/v2/runs")
    source_ids: tuple[str, ...] = ()
    task_batch_size: int = 128
    event_batch_size: int = 20
    lease_seconds: float = 30.0
    lease_heartbeat_seconds: float = 10.0
    execution_timeout_seconds: float = 360.0
    discovery_plan_budget: int = 20
    attempt_timeout_seconds: float = 180.0
    request_retry_policy: RequestRetryPolicy = field(default_factory=RequestRetryPolicy)
    company_enrichment_enabled: bool = True
    progress_callback: Callable[[GraphSearchProgress], None] | None = None
    progress_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs_dir", Path(self.runs_dir))
        if self.task_batch_size < 1 or self.event_batch_size < 1:
            raise ValueError("graph batch sizes must be >= 1")
        if self.event_batch_size > _MAX_EVENT_BATCH_SIZE:
            raise ValueError(f"event_batch_size must be <= {_MAX_EVENT_BATCH_SIZE}")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if not 0 < self.lease_heartbeat_seconds < self.lease_seconds:
            raise ValueError("lease_heartbeat_seconds must be between zero and lease_seconds")
        if self.execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds must be > 0")
        if self.discovery_plan_budget < 0:
            raise ValueError("discovery_plan_budget must be >= 0")
        if self.attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be > 0")
        if self.progress_interval_seconds < 0:
            raise ValueError("progress_interval_seconds must be >= 0")


@dataclass(frozen=True)
class GraphSearchPipelineExecution:
    run_id: str
    execution_id: str
    enrichment_execution_id: str
    discovered_search_execution_id: str
    append_sequence: int
    paths: RunPaths
    final_items: tuple[JsonObject, ...]
    processed_payload: JsonObject
    receipt: JsonObject


@dataclass(frozen=True)
class PipelineDriverSpec:
    execution_id: str
    selection_request: SearchRequest | None
    discovery_request: SearchRequest | None
    requirement_scope: Literal["required", "optional"]
    optional_execution_id: str | None = None
    discovery_execution_id: str | None = None
