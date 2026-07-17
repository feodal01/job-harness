"""Application service for the durable v2 scraper graph."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.contracts import ParserRegistry, SearchRequest
from job_harness.v2.ports import ParserRuntimeFactory
from job_harness.v2.runtime.config import SearchServiceConfig
from job_harness.v2.runtime.graph_pipeline import (
    GraphSearchPipeline,
    GraphSearchPipelineConfig,
    GraphSearchPipelineExecution,
)
from job_harness.v2.runtime.graph_scheduler import GraphSearchProgress
from job_harness.v2.runtime.http import HttpxTransport
from job_harness.v2.runtime.parser_runtime import DefaultParserRuntimeFactory, HostResolver
from job_harness.v2.runtime.resource_gate import (
    ResourceGate,
    ResourcePolicy,
    SqliteResourceGateBackend,
)
from job_harness.v2.runtime.run_layout import RunPaths
from job_harness.v2.runtime.source_registry import build_independent_parser_registry
from job_harness.v2.serialization import JsonObject

__all__ = ["V2SearchApplication", "V2SearchConfig", "V2SearchExecution"]


@dataclass(frozen=True)
class V2SearchConfig:
    runs_dir: Path = Path(".job-harness/v2/runs")
    source_ids: tuple[str, ...] = ()
    service_config: SearchServiceConfig | None = None
    host_resolver: HostResolver | None = None
    progress_callback: Callable[[GraphSearchProgress], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "runs_dir", Path(self.runs_dir))


@dataclass(frozen=True)
class V2SearchExecution:
    run_id: str
    execution_id: str
    enrichment_execution_id: str
    discovered_search_execution_id: str
    append_sequence: int
    paths: RunPaths
    final_items: tuple[JsonObject, ...]
    processed_payload: JsonObject
    receipt: JsonObject


class V2SearchApplication:
    def __init__(
        self,
        *,
        config: V2SearchConfig | None = None,
        registry: ParserRegistry | None = None,
        runtime_factory: ParserRuntimeFactory | None = None,
    ) -> None:
        self._config = config or V2SearchConfig()
        self._registry = registry
        self._runtime_factory = runtime_factory

    async def search(self, request: SearchRequest, *, run_id: str | None = None) -> V2SearchExecution:
        registry = self._registry or build_independent_parser_registry(
            self._config.source_ids or request.sources
        )
        service_config = self._config.service_config or SearchServiceConfig.from_package_resource()
        runtime_factory, owned_transport = self._runtime_factory_for(service_config)
        try:
            execution = await self._pipeline(
                registry,
                runtime_factory,
                service_config,
            ).run(request, run_id=run_id)
        finally:
            if owned_transport is not None:
                await owned_transport.aclose()
        return _application_execution(execution)

    async def resume_execution(self, execution_id: str) -> V2SearchExecution:
        registry = self._registry or build_independent_parser_registry(
            self._config.source_ids
        )
        service_config = self._config.service_config or SearchServiceConfig.from_package_resource()
        runtime_factory, owned_transport = self._runtime_factory_for(service_config)
        try:
            execution = await self._pipeline(
                registry,
                runtime_factory,
                service_config,
            ).resume_execution(execution_id)
        finally:
            if owned_transport is not None:
                await owned_transport.aclose()
        return _application_execution(execution)

    def _runtime_factory_for(
        self,
        service_config: SearchServiceConfig,
    ) -> tuple[ParserRuntimeFactory, HttpxTransport | None]:
        if self._runtime_factory is not None:
            return self._runtime_factory, None
        owned_transport = HttpxTransport()
        gate_backend = SqliteResourceGateBackend(
            self._config.runs_dir / "_runtime" / "resource-gate.sqlite"
        )
        return (
            DefaultParserRuntimeFactory(
                transport=owned_transport,
                resource_gate=ResourceGate(
                    backend=gate_backend,
                    owner_id=f"process-{secrets.token_hex(6)}",
                ),
                policy_for_resource=_resource_policy(service_config),
                timeout_seconds=service_config.fetch_timeout_seconds,
                max_response_bytes=20 * 1024 * 1024,
                host_resolver=self._config.host_resolver,
                resource_key_resolver=service_config.resources.resource_key_for_host,
            ),
            owned_transport,
        )

    def _pipeline(
        self,
        registry: ParserRegistry,
        runtime_factory: ParserRuntimeFactory,
        service_config: SearchServiceConfig,
    ) -> GraphSearchPipeline:
        return GraphSearchPipeline(
            config=GraphSearchPipelineConfig(
                runs_dir=self._config.runs_dir,
                source_ids=self._config.source_ids,
                execution_timeout_seconds=service_config.run_timeout_seconds,
                attempt_timeout_seconds=service_config.source_attempt_timeout_seconds,
                request_retry_policy=service_config.request_retry.to_policy(
                    attempt_timeout_seconds=service_config.fetch_timeout_seconds,
                ),
                lease_seconds=30.0,
                lease_heartbeat_seconds=10.0,
                company_enrichment_enabled=service_config.application_channels.enabled,
                progress_callback=self._config.progress_callback,
            ),
            registry=registry,
            runtime_factory=runtime_factory,
            resource_key_resolver=service_config.resources.resource_key_for_host,
        )


def _application_execution(execution: GraphSearchPipelineExecution) -> V2SearchExecution:
    return V2SearchExecution(
        run_id=execution.run_id,
        execution_id=execution.execution_id,
        enrichment_execution_id=execution.enrichment_execution_id,
        discovered_search_execution_id=execution.discovered_search_execution_id,
        append_sequence=execution.append_sequence,
        paths=execution.paths,
        final_items=execution.final_items,
        processed_payload=execution.processed_payload,
        receipt=execution.receipt,
    )


def _resource_policy(config: SearchServiceConfig) -> Callable[[str], ResourcePolicy]:
    def resolve(resource_key: str) -> ResourcePolicy:
        return ResourcePolicy(
            max_concurrency=config.resources.concurrency_for_resource(resource_key),
            min_interval_seconds=config.resources.min_interval_for_resource(resource_key),
            lease_seconds=max(config.fetch_timeout_seconds * 2, 1.0),
        )

    return resolve
