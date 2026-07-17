from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from job_harness.v2.contracts import (
    CompanyRef,
    InvocationScope,
    ParserInvocationSpec,
    ParserManifest,
    ParserRegistry,
    ParserType,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchResultOutcome,
    SourceLocation,
    TaskClass,
    TransportKind,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.runtime.final_assembly import ExecutionNotDrainedError, FinalAssembler
from job_harness.v2.runtime.graph_coordinator import GraphCoordinator


def _manifest(source_id: str) -> ParserManifest:
    return ParserManifest(
        parser_id=f"{source_id}.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id=f"{source_id}.search.input.v1",
        output_schema_id="search-listing-output.v2",
        transport=TransportKind.HTTP,
        provider_ids=("hh",),
        supported_url_patterns=(),
        output_facts=("title", "vacancyUrl"),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=("aggregator",),
        query_mode="per_query",
        collection_unit="page",
        native_criteria=("query",),
        default_unit_budget=3,
        default_item_budget=100,
        default_invocation_budget=4,
    )


def _input(source_id: str, page: int, *, target_provider_id: str = "hh") -> SearchListingInput:
    return SearchListingInput(
        source_id=source_id,
        target_provider_id=target_provider_id,
        queries=("QA",),
        target={"kind": "catalog"},
        cursor={"page": page},
        native_filters={},
        resolved_state=None,
    )


def _listing(
    source_id: str,
    *,
    target_provider_id: str = "hh",
    source_listing_id: str = "123",
    company: CompanyRef | None = None,
) -> SearchListingOutput:
    vacancy_url = (
        f"https://hh.ru/vacancy/{source_listing_id}"
        if source_id == "hh_ru"
        else f"https://{source_id}.example/vacancy/{source_listing_id}"
    )
    return SearchListingOutput(
        source_id=source_id,
        target_provider_id=target_provider_id,
        source_listing_id=source_listing_id,
        title="QA Engineer",
        company=company or CompanyRef(name="Example"),
        location=SourceLocation("Moscow"),
        salary=None,
        work_formats=("hybrid",),
        remote_scopes=(),
        native_grade=None,
        posted_at=None,
        vacancy_url=vacancy_url,
        apply_url=None,
        summary=None,
    )


class FinalAssemblerTest(unittest.TestCase):
    def test_same_canonical_url_converges_across_provider_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-url-identity",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            canonical_url = "https://jobs.example.com/vacancies/42"
            for index, (source_id, provider_id, source_listing_id) in enumerate(
                (("board-a", "a", "a-42"), ("board-b", "b", "b-900"))
            ):
                source_plan = self._source_plan(repository, execution_id, source_id)
                invocation_id = repository.enqueue_invocation(
                    ParserInvocationSpec(
                        execution_id=execution_id,
                        source_plan_id=source_plan,
                        parent_invocation_id=None,
                        cause_event_id=None,
                        parser_ref=_manifest(source_id).ref,
                        parser_type=ParserType.SEARCH_LISTING,
                        input_schema_id=_manifest(source_id).input_schema_id,
                        parser_input=_input(source_id, 0, target_provider_id=provider_id),
                        task_class=TaskClass.LISTING,
                        task_key=f"{source_id}-page-0",
                        available_at=0.0,
                        reserved_collection_units=1,
                    )
                )
                leased = repository.lease_ready_invocations(
                    execution_id=execution_id,
                    owner_id=f"worker-{index}",
                    limit=1,
                    lease_seconds=30.0,
                    now=100.0 + index,
                )[0]
                self.assertEqual(invocation_id, leased.invocation_id)
                listing = _listing(
                    source_id,
                    target_provider_id=provider_id,
                    source_listing_id=source_listing_id,
                )
                repository.commit_search_result(
                    leased,
                    SearchListingResult(
                        outcome=SearchResultOutcome.SUCCESS,
                        items=(replace(listing, vacancy_url=canonical_url),),
                        continuations=(),
                        collection_units_consumed=1,
                    ),
                    _manifest(source_id),
                    now=102.0 + index,
                )
            GraphCoordinator(
                repository=repository,
                registry=ParserRegistry(()),
                owner_id="coordinator",
            ).process_once(execution_id, limit=20, lease_seconds=30.0, now=105.0)

            assembly = FinalAssembler(repository).assemble(execution_id, now=106.0)

            self.assertEqual(1, len(assembly.items))
            self.assertEqual(("board-a", "board-b"), tuple(assembly.items[0]["sourceVariants"]))
            self.assertEqual(
                1,
                self._scalar(database_path, "SELECT COUNT(*) FROM vacancy_resources"),
            )
            self.assertEqual(
                2,
                self._scalar(database_path, "SELECT COUNT(*) FROM vacancy_provider_aliases"),
            )

    def test_latest_final_reject_wins_when_fact_sets_share_a_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-latest-evaluation",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            source_plan = self._source_plan(repository, execution_id, "hh_ru")
            invocation_id = self._enqueue(
                repository,
                execution_id,
                source_plan,
                "hh_ru",
                page=0,
            )
            self._commit(
                repository,
                execution_id,
                invocation_id,
                "hh_ru",
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing("hh_ru"),),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                now=101.0,
            )
            GraphCoordinator(
                repository=repository,
                registry=ParserRegistry(()),
                owner_id="coordinator",
            ).process_once(execution_id, limit=20, lease_seconds=30.0, now=102.0)

            with closing(sqlite3.connect(database_path)) as connection:
                listing_id, created_at, facts_json = connection.execute(
                    "SELECT listing_id, created_at, materialized_facts_json FROM fact_sets"
                ).fetchone()
                facts = json.loads(facts_json)
                facts["description"] = "later evidence"
                connection.execute(
                    """
                    INSERT INTO fact_sets (
                        fact_set_id, execution_id, listing_id, evidence_refs_json,
                        materialized_facts_json, fingerprint, created_at
                    ) VALUES ('later-facts', ?, ?, '{}', ?, 'later-fingerprint', ?)
                    """,
                    (execution_id, listing_id, json.dumps(facts), created_at),
                )
                connection.execute(
                    """
                    INSERT INTO selection_evaluations (
                        evaluation_id, execution_id, listing_id, fact_set_id,
                        stage, outcome, reason_codes_json
                    ) VALUES ('later-reject', ?, ?, 'later-facts', 'final', 'reject', '["later"]')
                    """,
                    (execution_id, listing_id),
                )
                connection.commit()

            assembly = FinalAssembler(repository).assemble(execution_id, now=103.0)

            self.assertEqual((), assembly.items)

    def test_only_global_assembly_waits_for_all_branches_and_collapses_exact_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-test",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            hh_plan = self._source_plan(repository, execution_id, "hh_ru")
            mirror_plan = self._source_plan(repository, execution_id, "mirror")
            hh_first = self._enqueue(repository, execution_id, hh_plan, "hh_ru", page=0)
            self._commit(
                repository,
                execution_id,
                hh_first,
                "hh_ru",
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing("hh_ru"),),
                    continuations=(_input("hh_ru", 1),),
                    collection_units_consumed=1,
                ),
                now=101.0,
            )
            mirror_first = self._enqueue(repository, execution_id, mirror_plan, "mirror", page=0)
            self._commit(
                repository,
                execution_id,
                mirror_first,
                "mirror",
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing("mirror"),),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                now=102.0,
            )
            coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry(()),
                owner_id="coordinator",
            )
            self.assertEqual(
                coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=103.0),
                2,
            )

            with self.assertRaises(ExecutionNotDrainedError):
                FinalAssembler(repository).assemble(execution_id, now=104.0)

            continuation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="worker",
                limit=1,
                lease_seconds=30.0,
                now=105.0,
            )[0]
            repository.commit_search_result(
                continuation,
                SearchListingResult(
                    outcome=SearchResultOutcome.NO_RESULTS,
                    items=(),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                _manifest("hh_ru"),
                now=106.0,
            )

            assembly = FinalAssembler(repository).assemble(execution_id, now=107.0)

            self.assertEqual(len(assembly.items), 1)
            self.assertEqual(tuple(assembly.items[0]["sourceVariants"]), ("hh_ru", "mirror"))
            self.assertEqual(assembly.items[0]["sourceId"], "hh_ru")
            self.assertEqual(assembly.items[0]["sourceListingId"], "123")
            self.assertEqual(assembly.items[0]["vacancyUrl"], "https://hh.ru/vacancy/123")
            self.assertNotIn("source_id", assembly.items[0])
            self.assertNotIn("target_provider_id", assembly.items[0])
            self.assertNotIn("native_filters", assembly.items[0])
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM vacancy_duplicate_groups"), 1)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM vacancy_duplicate_members"), 2)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM final_vacancies"), 1)
            self.assertEqual(
                self._scalar(database_path, "SELECT status FROM search_executions"),
                "assembling",
            )

    def test_probable_duplicates_are_grouped_but_not_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-probable",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            company = CompanyRef(name="Example", official_site_url="https://example.com")
            for index, (source_id, provider_id) in enumerate((("board-a", "a"), ("board-b", "b"))):
                plan = self._source_plan(repository, execution_id, source_id)
                invocation_id = repository.enqueue_invocation(
                    ParserInvocationSpec(
                        execution_id=execution_id,
                        source_plan_id=plan,
                        parent_invocation_id=None,
                        cause_event_id=None,
                        parser_ref=_manifest(source_id).ref,
                        parser_type=ParserType.SEARCH_LISTING,
                        input_schema_id=_manifest(source_id).input_schema_id,
                        parser_input=_input(source_id, 0, target_provider_id=provider_id),
                        task_class=TaskClass.LISTING,
                        task_key=f"{source_id}-page-0",
                        available_at=0.0,
                        reserved_collection_units=1,
                    )
                )
                leased = repository.lease_ready_invocations(
                    execution_id=execution_id,
                    owner_id=f"worker-{index}",
                    limit=1,
                    lease_seconds=30.0,
                    now=100.0 + index,
                )[0]
                self.assertEqual(leased.invocation_id, invocation_id)
                repository.commit_search_result(
                    leased,
                    SearchListingResult(
                        outcome=SearchResultOutcome.SUCCESS,
                        items=(
                            _listing(
                                source_id,
                                target_provider_id=provider_id,
                                source_listing_id=str(index + 1),
                                company=company,
                            ),
                        ),
                        continuations=(),
                        collection_units_consumed=1,
                    ),
                    _manifest(source_id),
                    now=102.0 + index,
                )
            coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry(()),
                owner_id="coordinator",
            )
            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=105.0)

            assembly = FinalAssembler(repository).assemble(execution_id, now=106.0)

            self.assertEqual(len(assembly.items), 2)
            self.assertEqual(
                tuple(item["duplicateConfidence"] for item in assembly.items),
                ("probable", "probable"),
            )
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM vacancy_duplicate_groups WHERE confidence = 'probable'",
                ),
                1,
            )
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM final_vacancies"), 2)

    @staticmethod
    def _source_plan(repository: SqliteGraphRepository, execution_id: str, source_id: str) -> str:
        return repository.create_source_plan(
            execution_id=execution_id,
            source_id=source_id,
            manifest=_manifest(source_id),
            queries=("QA",),
            unit_budget=3,
            item_budget=100,
            invocation_budget=4,
        )

    @staticmethod
    def _enqueue(
        repository: SqliteGraphRepository,
        execution_id: str,
        source_plan_id: str,
        source_id: str,
        *,
        page: int,
    ) -> str:
        return repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=execution_id,
                source_plan_id=source_plan_id,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=_manifest(source_id).ref,
                parser_type=ParserType.SEARCH_LISTING,
                input_schema_id=_manifest(source_id).input_schema_id,
                parser_input=_input(source_id, page),
                task_class=TaskClass.LISTING,
                task_key=f"{source_id}-page-{page}",
                available_at=0.0,
                reserved_collection_units=1,
            )
        )

    @staticmethod
    def _commit(
        repository: SqliteGraphRepository,
        execution_id: str,
        invocation_id: str,
        source_id: str,
        result: SearchListingResult,
        *,
        now: float,
    ) -> None:
        leased = repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id=f"worker-{source_id}",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )
        invocation = next(item for item in leased if item.invocation_id == invocation_id)
        repository.commit_search_result(invocation, result, _manifest(source_id), now=now)

    @staticmethod
    def _scalar(database_path: Path, query: str) -> object:
        with closing(sqlite3.connect(database_path)) as connection:
            row = connection.execute(query).fetchone()
        if row is None:
            raise AssertionError("expected one row")
        return row[0]


if __name__ == "__main__":
    unittest.main()
