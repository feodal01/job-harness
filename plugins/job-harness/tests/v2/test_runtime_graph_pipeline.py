from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from job_harness.v2.contracts import (
    CompanyRef,
    InvocationScope,
    ParserManifest,
    ParserRegistry,
    ParserType,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchRequest,
    SearchResultOutcome,
    SourceLocation,
    SourceType,
    TransportKind,
    VacancyDetailInput,
    VacancyDetailResult,
)
from job_harness.v2.persistence.graph_repository import read_graph_processed_payload
from job_harness.v2.ports import HttpAction, HttpResponse, OperationContext, ParserRuntime
from job_harness.v2.runtime.graph_pipeline import GraphSearchPipeline, GraphSearchPipelineConfig


class _Runtime(ParserRuntime):
    @property
    def reserved_collection_units(self) -> int:
        return 1

    async def http(self, _action: HttpAction) -> HttpResponse:
        raise AssertionError("fake bundle does not use network")


class _RuntimeFactory:
    def create(self, context: OperationContext, *, reserved_collection_units: int) -> ParserRuntime:
        del context, reserved_collection_units
        return _Runtime()


class _SearchBundle:
    manifest = ParserManifest(
        parser_id="test.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id="test.search.input.v1",
        output_schema_id="search-listing-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("test",),
        supported_url_patterns=(),
        output_facts=("title", "vacancyUrl"),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=("aggregator",),
        query_mode="per_query",
        collection_unit="page",
        native_criteria=("query",),
        default_unit_budget=2,
        default_item_budget=20,
        default_invocation_budget=3,
    )
    input_type = SearchListingInput
    result_type = SearchListingResult

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            SearchListingInput(
                source_id="test",
                target_provider_id="test",
                queries=intent.query_variants,
                target=target,
                cursor={"page": 0},
                native_filters={},
                resolved_state=None,
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        del parser_input, runtime
        return SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=(
                SearchListingOutput(
                    source_id="test",
                    target_provider_id="test",
                    source_listing_id="1",
                    title="QA Engineer",
                    company=CompanyRef(name="Example"),
                    location=SourceLocation("Remote"),
                    salary=None,
                    work_formats=("remote",),
                    remote_scopes=(),
                    native_grade=None,
                    posted_at=None,
                    vacancy_url="https://example.com/jobs/1",
                    apply_url=None,
                    summary=None,
                ),
            ),
            continuations=(),
            collection_units_consumed=1,
        )


class _FailingDetailBundle:
    manifest = ParserManifest(
        parser_id="test.detail",
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id="test.detail.input.v1",
        output_schema_id="vacancy-detail-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("test",),
        supported_url_patterns=(r"https://example\.com/jobs/.*",),
        output_facts=("description",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )
    input_type = VacancyDetailInput
    result_type = VacancyDetailResult

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        del parser_input, runtime
        raise ValueError("detail unavailable")


class _CompanySearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="company.search",
        provider_ids=("company",),
        source_kinds=("company_career",),
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            replace(
                super().plan_initial(intent, target)[0],
                source_id="company",
                target_provider_id="company",
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            items=(
                replace(
                    result.items[0],
                    source_id="company",
                    target_provider_id="company",
                    vacancy_url="https://company.example/jobs/1",
                ),
            ),
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _DeadlineSearchBundle(_SearchBundle):
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        self._clock.now += 2.0
        return await super().execute(parser_input, runtime)


class GraphSearchPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_runs_managed_graph_to_final_snapshot_without_legacy_raw_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = GraphSearchPipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _FailingDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            self.assertEqual(execution.append_sequence, 0)
            self.assertEqual(len(execution.final_items), 1)
            self.assertEqual(execution.final_items[0]["title"], "QA Engineer")
            self.assertEqual(execution.final_items[0]["sourceId"], "test")
            self.assertNotIn("source_id", execution.final_items[0])
            self.assertNotIn("target_provider_id", execution.final_items[0])
            self.assertTrue(execution.paths.report_html_path.exists())
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                status = connection.execute("SELECT status FROM search_executions").fetchone()[0]
                enrichment_status = connection.execute(
                    "SELECT status FROM listing_enrichment_requests"
                ).fetchone()[0]
            self.assertNotIn("raw_listings", tables)
            self.assertIn("listing_observations", tables)
            self.assertEqual(status, "completed")
            self.assertEqual(enrichment_status, "terminal")
            stored_payload = read_graph_processed_payload(execution.paths.database_path)
            self.assertEqual(stored_payload["execution_id"], execution.execution_id)
            self.assertEqual(stored_payload["result_count"], 1)

    async def test_source_selector_honors_business_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = GraphSearchPipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _CompanySearchBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    source_types=(SourceType.COMPANY_CAREER,),
                ),
                run_id="r-company",
            )

            self.assertEqual(len(execution.final_items), 1)
            self.assertEqual(execution.final_items[0]["sourceId"], "company")
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                source_ids = tuple(
                    row[0]
                    for row in connection.execute("SELECT source_id FROM source_plans ORDER BY source_id")
                )
            self.assertEqual(source_ids, ("company",))

    async def test_preliminary_reject_does_not_schedule_detail_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = GraphSearchPipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _FailingDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    exclude_companies=("Example",),
                ),
                run_id="r-rejected",
            )

            self.assertEqual(execution.final_items, ())
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                detail_tasks = connection.execute(
                    "SELECT COUNT(*) FROM parser_invocations WHERE parser_type = 'vacancy_detail'"
                ).fetchone()[0]
                final_outcome = connection.execute(
                    "SELECT outcome FROM selection_evaluations WHERE stage = 'final'"
                ).fetchone()[0]
            self.assertEqual(detail_tasks, 0)
            self.assertEqual(final_outcome, "reject")

    async def test_deadline_fences_late_result_and_completes_with_deadline_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            pipeline = GraphSearchPipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    execution_timeout_seconds=1.0,
                ),
                registry=ParserRegistry((_DeadlineSearchBundle(clock),)),
                runtime_factory=_RuntimeFactory(),
                clock=clock,
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-deadline",
            )

            self.assertEqual(execution.final_items, ())
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                invocation_status = connection.execute(
                    "SELECT status FROM parser_invocations"
                ).fetchone()[0]
                source_status = connection.execute(
                    "SELECT status FROM source_plans"
                ).fetchone()[0]
                execution_row = connection.execute(
                    "SELECT status, completion_reason FROM search_executions"
                ).fetchone()
            self.assertEqual(invocation_status, "cancelled")
            self.assertEqual(source_status, "cancelled")
            self.assertEqual(execution_row, ("completed", "deadline"))


if __name__ == "__main__":
    unittest.main()
