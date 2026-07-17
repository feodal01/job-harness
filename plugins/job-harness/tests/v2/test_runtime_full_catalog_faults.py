from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from job_harness.v2.contracts import SearchRequest
from job_harness.v2.ports import (
    HttpAction,
    HttpResponse,
    OperationContext,
    ParserAttemptMetrics,
    ParserRuntime,
    RetrySafety,
)
from job_harness.v2.runtime.config import SearchServiceConfig
from job_harness.v2.runtime.graph_pipeline import GraphSearchPipeline, GraphSearchPipelineConfig
from job_harness.v2.runtime.request_retry import (
    RequestAttemptError,
    RequestFailureKind,
    RequestRetryPolicy,
)
from job_harness.v2.runtime.source_registry import (
    build_independent_parser_registry,
    implemented_source_ids,
)


class _TimeoutRuntime(ParserRuntime):
    @property
    def reserved_collection_units(self) -> int:
        return 1

    @property
    def attempt_metrics(self) -> ParserAttemptMetrics:
        return ParserAttemptMetrics(
            network_action_count=1,
            network_elapsed_ms=2,
            last_error_class="TimeoutError",
        )

    async def http(self, _action: HttpAction) -> HttpResponse:
        await asyncio.sleep(0.002)
        raise RequestAttemptError(
            failure_kind=RequestFailureKind.TIMEOUT,
            retry_safety=RetrySafety.SAFE,
            message="deterministic timeout",
        )


class _TimeoutRuntimeFactory:
    def create(self, context: OperationContext, *, reserved_collection_units: int) -> ParserRuntime:
        del context, reserved_collection_units
        return _TimeoutRuntime()


class FullCatalogFaultTest(unittest.IsolatedAsyncioTestCase):
    async def test_widespread_page_timeouts_exhaust_independently_without_deadline_hang(self) -> None:
        registry = build_independent_parser_registry()
        selected_sources = len(implemented_source_ids())
        self.assertGreaterEqual(selected_sources, 100)
        with tempfile.TemporaryDirectory() as directory:
            pipeline = GraphSearchPipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    execution_timeout_seconds=10.0,
                    attempt_timeout_seconds=0.05,
                    company_enrichment_enabled=False,
                    request_retry_policy=RequestRetryPolicy(
                        max_attempts=3,
                        attempt_timeout_seconds=0.05,
                        base_delay_seconds=0.001,
                        max_delay_seconds=0.002,
                        request_budget_seconds=0.2,
                        random_fraction=lambda: 1.0,
                    ),
                ),
                registry=registry,
                runtime_factory=_TimeoutRuntimeFactory(),
            )

            started = time.perf_counter()
            execution = await asyncio.wait_for(
                pipeline.run(SearchRequest(query_variants=("QA",)), run_id="r-faults"),
                timeout=10.0,
            )
            elapsed = time.perf_counter() - started

            self.assertLess(elapsed, 10.0)
            self.assertEqual(execution.receipt["execution_quality"], "failed")
            coverage = execution.receipt["diagnostics"]["source_coverage"]
            self.assertEqual(coverage["planned"], selected_sources)
            self.assertEqual(coverage["failed"], selected_sources)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                timeout_attempt_bounds = connection.execute(
                    "SELECT MIN(attempt_count), MAX(attempt_count), COUNT(*) FROM ("
                    "SELECT COUNT(*) AS attempt_count FROM parser_attempts AS attempt "
                    "JOIN parser_invocations AS invocation "
                    "ON invocation.invocation_id = attempt.invocation_id "
                    "WHERE invocation.outcome = 'source_timeout' "
                    "GROUP BY attempt.invocation_id"
                    ")"
                ).fetchone()
                repeated_successes = connection.execute(
                    "SELECT COUNT(*) FROM parser_invocations "
                    "WHERE status = 'succeeded' AND invocation_id IN ("
                    "SELECT invocation_id FROM parser_attempts GROUP BY invocation_id HAVING COUNT(*) > 1"
                    ")"
                ).fetchone()[0]
            self.assertEqual(timeout_attempt_bounds[:2], (3, 3))
            self.assertGreaterEqual(timeout_attempt_bounds[2], 100)
            self.assertEqual(repeated_successes, 0)

    def test_production_retry_and_deadline_budget_is_bounded(self) -> None:
        service = SearchServiceConfig.from_package_resource()
        graph = GraphSearchPipelineConfig()

        self.assertEqual(graph.task_batch_size, 128)
        self.assertEqual(service.run_timeout_seconds, 180.0)
        self.assertEqual(service.fetch_timeout_seconds, 15.0)
        self.assertEqual(service.request_retry.max_attempts, 3)
        self.assertEqual(service.request_retry.request_budget_seconds, 55.0)


if __name__ == "__main__":
    unittest.main()
