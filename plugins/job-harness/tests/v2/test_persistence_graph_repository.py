from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast

from job_harness.v2.contracts import (
    CompanyRef,
    ExecutionArtifact,
    InvocationScope,
    ParserInvocationSpec,
    ParserManifest,
    ParserType,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchResultOutcome,
    SourceLocation,
    StaleLeaseError,
    TaskClass,
    TransportKind,
    VacancyDetailInput,
)
from job_harness.v2.persistence.graph_repository import (
    SqliteGraphRepository,
    _comparison_matches,
)
from job_harness.v2.ports import ParserAttemptMetrics

_DEFAULT_COMPANY = CompanyRef(name="Example")


def _manifest() -> ParserManifest:
    return ParserManifest(
        parser_id="hh.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id="hh.search.input.v1",
        output_schema_id="hh.search.output.v1",
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
        max_units_per_invocation=1,
    )


def _input(page: int) -> SearchListingInput:
    return SearchListingInput(
        source_id="hh_ru",
        target_provider_id="hh",
        queries=("QA",),
        target={"kind": "catalog"},
        cursor={"page": page},
        native_filters={"area": "113"},
        resolved_state=None,
    )


def _detail_manifest() -> ParserManifest:
    return ParserManifest(
        parser_id="hh.detail",
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id="hh.detail.input.v1",
        output_schema_id="hh.detail.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("hh",),
        supported_url_patterns=(r"https://hh\.ru/vacancy/.*",),
        output_facts=("description",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )


def _listing(
    source_listing_id: str = "123",
    *,
    company: CompanyRef | None = _DEFAULT_COMPANY,
) -> SearchListingOutput:
    return SearchListingOutput(
        source_id="hh_ru",
        target_provider_id="hh",
        source_listing_id=source_listing_id,
        title="QA Engineer",
        company=company,
        location=SourceLocation("Moscow"),
        salary=None,
        work_formats=("hybrid",),
        remote_scopes=(),
        native_grade=None,
        posted_at=None,
        vacancy_url=f"https://hh.ru/vacancy/{source_listing_id}",
        apply_url=None,
        summary=None,
    )


class SqliteGraphRepositoryTest(unittest.TestCase):
    def test_numeric_gte_fact_comparison(self) -> None:
        self.assertTrue(_comparison_matches(250_000, {"operator": "gte", "value": 200_000}))
        self.assertFalse(_comparison_matches(150_000, {"operator": "gte", "value": 200_000}))

    def test_execution_completes_only_after_exact_artifact_verification(self) -> None:
        execution_id = self.repository.create_execution(
            run_id="r-artifacts",
            intent={"queries": ["QA"]},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            active_runtime_budget_ms=1_000_000,
        )

        self.repository.assemble_final(
            execution_id,
            projector=lambda facts: facts,
            scorer=lambda _facts: 0.0,
            now=10.0,
        )

        self.assertEqual(
            self._scalar(
                "SELECT status FROM search_executions WHERE execution_id = ?",
                (execution_id,),
            ),
            "assembling",
        )
        artifact = ExecutionArtifact(
            name="search_results",
            path="/tmp/search-results.json",
            schema_version=2,
            sha256="a" * 64,
            byte_count=42,
        )
        self.repository.prepare_execution_artifacts(
            execution_id,
            artifacts=(artifact,),
            now=11.0,
        )
        self.assertEqual(
            self._scalar(
                "SELECT status FROM search_executions WHERE execution_id = ?",
                (execution_id,),
            ),
            "artifacts_pending",
        )

        with self.assertRaisesRegex(ValueError, "digest"):
            self.repository.complete_execution_artifacts(
                execution_id,
                verified_artifacts=(replace(artifact, sha256="b" * 64),),
                now=12.0,
            )
        self.repository.complete_execution_artifacts(
            execution_id,
            verified_artifacts=(artifact,),
            now=13.0,
        )

        self.assertEqual(
            self._scalar(
                "SELECT status FROM search_executions WHERE execution_id = ?",
                (execution_id,),
            ),
            "completed",
        )

    def test_active_runtime_heartbeat_excludes_process_downtime(self) -> None:
        execution_id = self.repository.create_execution(
            run_id="r-active-runtime",
            intent={"queries": ["QA"]},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v2",
            active_runtime_budget_ms=5_000,
        )
        self.repository.begin_execution_sessions((execution_id,), now=100.0)
        self.repository.heartbeat_execution_sessions((execution_id,), now=102.0)
        self.repository.end_execution_sessions((execution_id,), now=103.0)

        self.assertEqual(
            tuple(
                self._row(
                    """
                    SELECT active_runtime_ms, active_session_started_at
                    FROM search_executions WHERE execution_id = ?
                    """,
                    (execution_id,),
                )
            ),
            (3_000, None),
        )

        self.repository.begin_execution_sessions((execution_id,), now=10_000.0)
        self.assertFalse(self.repository.settle_deadline(execution_id, now=10_001.9))
        self.assertTrue(self.repository.settle_deadline(execution_id, now=10_002.1))

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.database_path = Path(self._temporary_directory.name) / "run.sqlite"
        self.repository = SqliteGraphRepository(self.database_path)
        self.addCleanup(self.repository.close)
        self.execution_id = self.repository.create_execution(
            run_id="r-test",
            intent={"queries": ["QA"]},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            active_runtime_budget_ms=1_000_000,
        )
        self.source_plan_id = self.repository.create_source_plan(
            execution_id=self.execution_id,
            source_id="hh_ru",
            manifest=_manifest(),
            queries=("QA",),
            unit_budget=3,
            item_budget=100,
            invocation_budget=4,
        )

    def _enqueue(self, page: int = 0, *, resource_key: str | None = None) -> str:
        return self.repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=self.execution_id,
                source_plan_id=self.source_plan_id,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=_manifest().ref,
                parser_type=ParserType.SEARCH_LISTING,
                input_schema_id=_manifest().input_schema_id,
                parser_input=_input(page),
                task_class=TaskClass.LISTING,
                task_key=f"hh-page-{page}",
                available_at=0.0,
                reserved_collection_units=1,
                resource_key=resource_key,
            )
        )

    def test_leasing_can_exclude_ready_resource_keys(self) -> None:
        self._enqueue(0, resource_key="hh")
        self._enqueue(1, resource_key="other")

        leased = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker",
            limit=2,
            lease_seconds=30.0,
            now=100.0,
            excluded_resource_keys=("hh",),
        )

        self.assertEqual(len(leased), 1)
        self.assertEqual(leased[0].spec.resource_key, "other")
        parser_input = cast(SearchListingInput, leased[0].parser_input)
        self.assertEqual(parser_input.cursor, {"page": 1})

    def _enqueue_detail(self, vacancy_id: str) -> str:
        manifest = _detail_manifest()
        return self.repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=self.execution_id,
                source_plan_id=None,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=manifest.ref,
                parser_type=ParserType.VACANCY_DETAIL,
                input_schema_id=manifest.input_schema_id,
                parser_input=VacancyDetailInput(
                    target_provider_id="hh",
                    vacancy_url=f"https://hh.ru/vacancy/{vacancy_id}",
                    source_listing_id=vacancy_id,
                ),
                task_class=TaskClass.DETAIL,
                task_key=f"hh-detail-{vacancy_id}",
                available_at=0.0,
                reserved_collection_units=None,
            )
        )

    def test_leasing_persists_two_to_one_listing_weight_across_calls(self) -> None:
        for page in (0, 1, 2):
            self._enqueue(page)
        for vacancy_id in ("100", "101", "102"):
            self._enqueue_detail(vacancy_id)

        first = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )
        self.repository.close()
        self.repository = SqliteGraphRepository(self.database_path)
        self.addCleanup(self.repository.close)
        second = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-2",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )
        third = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-3",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )

        self.assertEqual(
            tuple(item.spec.task_class for item in first),
            (TaskClass.LISTING,),
        )
        self.assertEqual(second[0].spec.task_class, TaskClass.LISTING)
        self.assertEqual(third[0].spec.task_class, TaskClass.DETAIL)

    def test_single_worker_dispatches_required_detail_after_two_listing_tasks(self) -> None:
        self._enqueue(0)
        self._enqueue_detail("100")
        first = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        self.repository.commit_search_result(
            first,
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(_listing(),),
                continuations=(_input(1),),
                collection_units_consumed=1,
            ),
            _manifest(),
            now=101.0,
        )

        second = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-2",
            limit=1,
            lease_seconds=30.0,
            now=102.0,
        )[0]
        self.repository.commit_search_result(
            second,
            SearchListingResult(
                outcome=SearchResultOutcome.NO_RESULTS,
                items=(),
                continuations=(),
                collection_units_consumed=1,
            ),
            _manifest(),
            now=103.0,
        )
        third = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-3",
            limit=1,
            lease_seconds=30.0,
            now=104.0,
        )[0]

        self.assertEqual(first.spec.task_class, TaskClass.LISTING)
        self.assertEqual(second.spec.task_class, TaskClass.LISTING)
        self.assertEqual(third.spec.task_class, TaskClass.DETAIL)

    def test_task_key_is_idempotent_within_execution(self) -> None:
        first = self._enqueue()
        second = self._enqueue()

        self.assertEqual(first, second)
        self.assertEqual(self._count("parser_invocations"), 1)

    def test_search_commit_is_atomic_and_page_two_is_immediately_leasable(self) -> None:
        first_id = self._enqueue()
        first = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        result = SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=(_listing(),),
            continuations=(_input(1),),
            collection_units_consumed=1,
        )

        self.repository.commit_search_result(first, result, _manifest(), now=101.0)

        self.assertEqual(first.invocation_id, first_id)
        self.assertEqual(self._count("listing_observations"), 1)
        self.assertEqual(self._count("domain_events"), 1)
        self.assertEqual(self._count("parser_invocations"), 2)
        first_status = self._scalar(
            "SELECT status FROM parser_invocations WHERE invocation_id = ?",
            (first.invocation_id,),
        )
        self.assertEqual(first_status, "succeeded")
        counters = self._row(
            "SELECT units_used, items_used, invocations_used FROM source_plans WHERE source_plan_id = ?",
            (self.source_plan_id,),
        )
        self.assertEqual(tuple(counters), (1, 1, 1))

        second = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-2",
            limit=1,
            lease_seconds=30.0,
            now=102.0,
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].parser_input, _input(1))
        self.assertEqual(
            self._scalar("SELECT processed_at FROM domain_events", ()),
            None,
        )

    def test_expired_lease_cannot_commit_after_reassignment(self) -> None:
        self._enqueue()
        stale = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=1.0,
            now=100.0,
        )[0]
        current = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-2",
            limit=1,
            lease_seconds=10.0,
            now=102.0,
        )[0]

        with self.assertRaises(StaleLeaseError):
            self.repository.commit_search_result(
                stale,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing(),),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                _manifest(),
                now=103.0,
            )

        self.assertNotEqual(stale.lease_token, current.lease_token)
        self.assertEqual(self._count("listing_observations"), 0)
        self.assertEqual(self._count("domain_events"), 0)

    def test_expired_active_attempt_is_recorded_as_worker_lost(self) -> None:
        self._enqueue()
        stale = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=1.0,
            now=100.0,
        )[0]
        self.repository.begin_parser_attempt(stale, now=100.0)

        reassigned = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-2",
            limit=1,
            lease_seconds=10.0,
            now=102.0,
        )[0]

        self.assertEqual(reassigned.invocation_id, stale.invocation_id)
        self.assertEqual(
            tuple(
                self._row(
                    "SELECT outcome, failure_kind, retry_decision "
                    "FROM parser_attempts WHERE invocation_id = ?",
                    (stale.invocation_id,),
                )
            ),
            ("worker_lost", "worker_lost", "scheduled"),
        )
        self.assertEqual(self.repository.request_attempt_number(stale.invocation_id), 0)

    def test_batch_heartbeat_renews_only_current_owned_leases(self) -> None:
        self._enqueue()
        leased = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]

        renewed = self.repository.renew_invocation_leases(
            owner_id="worker-1",
            leases=((leased.invocation_id, leased.lease_token),),
            lease_seconds=30.0,
            now=120.0,
        )

        self.assertEqual(renewed, 1)
        self.assertEqual(
            self._scalar(
                "SELECT lease_until FROM parser_invocations WHERE invocation_id = ?",
                (leased.invocation_id,),
            ),
            150.0,
        )
        self.assertEqual(
            self.repository.lease_ready_invocations(
                execution_id=self.execution_id,
                owner_id="worker-2",
                limit=1,
                lease_seconds=30.0,
                now=131.0,
            ),
            (),
        )
        reassigned = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-2",
            limit=1,
            lease_seconds=30.0,
            now=151.0,
        )
        self.assertEqual(len(reassigned), 1)
        self.assertEqual(reassigned[0].invocation_id, leased.invocation_id)

    def test_request_retry_waits_without_a_lease_and_reuses_the_same_page_task(self) -> None:
        invocation_id = self._enqueue()
        leased = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        attempt_id, _ = self.repository.begin_parser_attempt(leased, now=100.0)

        self.repository.defer_invocation(
            leased,
            attempt_id=attempt_id,
            failure_kind="source_timeout",
            attempt_metrics=ParserAttemptMetrics(
                network_action_count=1,
                network_elapsed_ms=15_000,
                last_error_class="TimeoutError",
            ),
            waiting_reason="retry_backoff",
            retry_delay_ms=5_000,
            available_at=105.0,
            now=100.0,
        )

        waiting = self._row(
            (
                "SELECT status, waiting_reason, available_at, lease_owner, lease_token, lease_until "
                "FROM parser_invocations WHERE invocation_id = ?"
            ),
            (invocation_id,),
        )
        self.assertEqual(tuple(waiting), ("waiting", "retry_backoff", 105.0, None, None, None))
        attempt = self._row(
            "SELECT retry_decision, retry_delay_ms FROM parser_attempts WHERE parser_attempt_id = ?",
            (attempt_id,),
        )
        self.assertEqual(tuple(attempt), ("scheduled", 5_000))
        self.assertEqual(
            self.repository.lease_ready_invocations(
                execution_id=self.execution_id,
                owner_id="worker-2",
                limit=1,
                lease_seconds=30.0,
                now=104.9,
            ),
            (),
        )

        retried = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-2",
            limit=1,
            lease_seconds=30.0,
            now=105.0,
        )[0]

        self.assertEqual(retried.invocation_id, invocation_id)
        self.assertEqual(retried.spec.task_key, "hh-page-0")

    def test_next_wakeup_reports_future_waiting_page(self) -> None:
        invocation_id = self._enqueue()
        leased = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker-1",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        attempt_id, _ = self.repository.begin_parser_attempt(leased, now=100.0)
        self.repository.defer_invocation(
            leased,
            attempt_id=attempt_id,
            failure_kind="network_error",
            attempt_metrics=ParserAttemptMetrics(network_action_count=1),
            waiting_reason="retry_backoff",
            retry_delay_ms=7_000,
            available_at=107.0,
            now=100.0,
        )

        self.assertEqual(
            self.repository.next_scheduler_wakeup_at(self.execution_id, now=100.0),
            107.0,
        )
        self.assertEqual(
            self._scalar(
                "SELECT invocation_id FROM parser_invocations WHERE status = 'waiting'",
                (),
            ),
            invocation_id,
        )

    def test_next_wakeup_reports_expiry_of_orphaned_lease(self) -> None:
        self._enqueue()
        self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="dead-worker",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )

        self.assertEqual(
            self.repository.next_scheduler_wakeup_at(self.execution_id, now=100.0),
            130.0,
        )

    def test_next_wakeup_reports_coordinator_expiry_for_unprocessed_event(self) -> None:
        self._enqueue()
        invocation = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        self.repository.commit_search_result(
            invocation,
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(_listing(),),
                continuations=(),
                collection_units_consumed=1,
            ),
            _manifest(),
            now=101.0,
        )
        self.repository.acquire_coordinator(
            execution_id=self.execution_id,
            owner_id="dead-coordinator",
            lease_seconds=30.0,
            now=102.0,
        )

        self.assertEqual(
            self.repository.next_scheduler_wakeup_at(self.execution_id, now=102.0),
            132.0,
        )
        self.repository.release_coordinator_leases(
            ((self.execution_id, "dead-coordinator"),)
        )
        self.assertEqual(
            self.repository.next_scheduler_wakeup_at(self.execution_id, now=102.0),
            1102.0,
        )

    def test_later_success_cannot_erase_sibling_page_failure(self) -> None:
        self._enqueue(0)
        self._enqueue(1)
        first, second = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker",
            limit=2,
            lease_seconds=30.0,
            now=100.0,
        )
        self.repository.commit_failure(
            first,
            failure_kind="timeout",
            retry_decision="exhausted",
            public_notice=None,
            now=101.0,
        )
        self.repository.commit_search_result(
            second,
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(_listing("page-2"),),
                continuations=(),
                collection_units_consumed=1,
            ),
            _manifest(),
            now=102.0,
        )

        self.assertEqual(
            ("partial", "timeout"),
            tuple(
                self._row(
                    "SELECT status, terminal_reason FROM source_plans WHERE source_plan_id = ?",
                    (self.source_plan_id,),
                )
            ),
        )

    def test_events_use_one_execution_coordinator_lease_without_event_claim_rows(self) -> None:
        self._enqueue()
        invocation = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        self.repository.commit_search_result(
            invocation,
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(_listing(),),
                continuations=(),
                collection_units_consumed=1,
            ),
            _manifest(),
            now=101.0,
        )
        coordinator = self.repository.acquire_coordinator(
            execution_id=self.execution_id,
            owner_id="coordinator-a",
            lease_seconds=30.0,
            now=102.0,
        )
        if coordinator is None:
            self.fail("coordinator lease was not acquired")

        events = self.repository.read_unprocessed_events(self.execution_id, limit=10)
        self.repository.mark_events_processed(coordinator, tuple(event.event_id for event in events), now=103.0)

        self.assertEqual(len(events), 1)
        self.assertIsNotNone(self._scalar("SELECT processed_at FROM domain_events", ()))
        event_columns = self._column_names("domain_events")
        self.assertNotIn("claim_owner", event_columns)
        self.assertNotIn("claim_token", event_columns)

    def test_schema_omits_write_amplifying_member_and_request_history_tables(self) -> None:
        table_names = {
            str(row[0])
            for row in self._query("SELECT name FROM sqlite_master WHERE type = 'table'", ())
        }

        self.assertNotIn("fact_set_listing_members", table_names)
        self.assertNotIn("fact_set_detail_members", table_names)
        self.assertNotIn("resource_requests", table_names)
        self.assertNotIn("request_attempts", table_names)
        self.assertIn("fact_sets", table_names)
        self.assertIn("parser_attempts", table_names)
        fact_set_indexes = {
            str(row[1])
            for row in self._query("PRAGMA index_list('fact_sets')", ())
        }
        self.assertIn("fact_sets_latest_idx", fact_set_indexes)

    def test_schema_enforces_all_event_and_discovery_edges_shown_in_diagram(self) -> None:
        source_plan_foreign_keys = self._foreign_keys("source_plans")
        invocation_foreign_keys = self._foreign_keys("parser_invocations")

        self.assertEqual(source_plan_foreign_keys["origin_event_id"], ("domain_events", "event_id"))
        self.assertEqual(source_plan_foreign_keys["origin_company_id"], ("companies", "company_id"))
        self.assertEqual(source_plan_foreign_keys["origin_endpoint_id"], ("discovered_endpoints", "endpoint_id"))
        self.assertEqual(invocation_foreign_keys["cause_event_id"], ("domain_events", "event_id"))

    def test_listing_creates_company_only_from_strong_identity_claims(self) -> None:
        invocation_id = self._enqueue()
        invocation = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        self.repository.commit_search_result(
            invocation,
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(
                    _listing(
                        company=CompanyRef(
                            name="Example",
                            target_provider_id="hh",
                            source_company_id="10",
                            profile_url="https://hh.ru/employer/10",
                            official_site_url="https://example.com",
                        )
                    ),
                ),
                continuations=(),
                collection_units_consumed=1,
            ),
            _manifest(),
            now=101.0,
        )

        self.assertEqual(self._count("companies"), 1)
        self.assertEqual(self._count("company_identity_claims"), 3)
        company_id = self._scalar("SELECT company_id FROM vacancy_listings", ())
        self.assertIsNotNone(company_id)
        self.assertEqual(
            self._scalar(
                "SELECT listing_observation_id FROM company_identity_claims WHERE claim_type = 'provider_id'",
                (),
            ),
            self._scalar(
                "SELECT listing_observation_id FROM listing_observations WHERE invocation_id = ?",
                (invocation_id,),
            ),
        )

    def test_name_only_listing_does_not_create_company_identity(self) -> None:
        self._enqueue()
        invocation = self.repository.lease_ready_invocations(
            execution_id=self.execution_id,
            owner_id="worker",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]
        self.repository.commit_search_result(
            invocation,
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(_listing(company=CompanyRef(name="Example")),),
                continuations=(),
                collection_units_consumed=1,
            ),
            _manifest(),
            now=101.0,
        )

        self.assertEqual(self._count("companies"), 0)
        self.assertIsNone(self._scalar("SELECT company_id FROM vacancy_listings", ()))

    def test_listing_leases_atomically_reserve_remaining_collection_budget(self) -> None:
        manifest = replace(
            _manifest(),
            invocation_scope=InvocationScope.SESSION_BATCH,
            max_units_per_invocation=3,
            default_unit_budget=4,
            default_invocation_budget=3,
        )
        execution_id = self.repository.create_execution(
            run_id="r-session",
            intent={"queries": ["QA"]},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            active_runtime_budget_ms=1_000_000,
        )
        source_plan_id = self.repository.create_source_plan(
            execution_id=execution_id,
            source_id="session-source",
            manifest=manifest,
            queries=("QA",),
            unit_budget=4,
            item_budget=100,
            invocation_budget=3,
        )
        invocation_ids = []
        for page in (0, 1, 2):
            invocation_ids.append(self.repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=manifest.ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=manifest.input_schema_id,
                    parser_input=_input(page),
                    task_class=TaskClass.LISTING,
                    task_key=f"session-page-{page}",
                    available_at=0.0,
                    reserved_collection_units=3,
                )
            ))

        leased = self.repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id="worker",
            limit=2,
            lease_seconds=30.0,
            now=100.0,
        )

        reservations = tuple(item.spec.reserved_collection_units for item in leased)
        self.assertEqual(reservations, (3, 1))
        self.assertEqual(sum(value or 0 for value in reservations), 4)
        pending_id = next(
            invocation_id
            for invocation_id in invocation_ids
            if invocation_id not in {item.invocation_id for item in leased}
        )
        for index, invocation in enumerate(leased):
            self.repository.commit_search_result(
                invocation,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing(str(200 + index)),),
                    continuations=(),
                    collection_units_consumed=invocation.spec.reserved_collection_units or 1,
                ),
                manifest,
                now=101.0 + index,
            )

        exhausted = self.repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id="worker",
            limit=2,
            lease_seconds=30.0,
            now=104.0,
        )

        self.assertEqual(exhausted, ())
        self.assertEqual(
            self._scalar(
                "SELECT status FROM parser_invocations WHERE invocation_id = ?",
                (pending_id,),
            ),
            "cancelled",
        )
        self.assertEqual(
            self._scalar("SELECT status FROM source_plans WHERE source_plan_id = ?", (source_plan_id,)),
            "limit_reached",
        )

    def test_oversized_last_page_is_truncated_without_parse_failure(self) -> None:
        execution_id = self.repository.create_execution(
            run_id="r-item-limit",
            intent={"queries": ["QA"]},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            active_runtime_budget_ms=1_000_000,
        )
        manifest = _manifest()
        source_plan_id = self.repository.create_source_plan(
            execution_id=execution_id,
            source_id="hh_ru",
            manifest=manifest,
            queries=("QA",),
            unit_budget=2,
            item_budget=2,
            invocation_budget=2,
        )
        self.repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=execution_id,
                source_plan_id=source_plan_id,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=manifest.ref,
                parser_type=ParserType.SEARCH_LISTING,
                input_schema_id=manifest.input_schema_id,
                parser_input=_input(0),
                task_class=TaskClass.LISTING,
                task_key="item-limit-page-0",
                available_at=100.0,
                reserved_collection_units=1,
            )
        )
        invocation = self.repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id="worker",
            limit=1,
            lease_seconds=30.0,
            now=100.0,
        )[0]

        self.repository.commit_search_result(
            invocation,
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(_listing("1"), _listing("2"), _listing("3")),
                continuations=(_input(1),),
                collection_units_consumed=1,
            ),
            manifest,
            now=101.0,
        )

        with closing(sqlite3.connect(self.database_path)) as connection:
            plan = connection.execute(
                "SELECT status, terminal_reason, items_used, invocations_used FROM source_plans "
                "WHERE source_plan_id = ?",
                (source_plan_id,),
            ).fetchone()
            observations = connection.execute(
                "SELECT COUNT(*) FROM listing_observations WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()[0]
            invocations = connection.execute(
                "SELECT COUNT(*) FROM parser_invocations WHERE source_plan_id = ?",
                (source_plan_id,),
            ).fetchone()[0]
        self.assertEqual(plan, ("limit_reached", "item_limit", 2, 1))
        self.assertEqual(observations, 2)
        self.assertEqual(invocations, 1)

    def test_already_leased_page_finishes_cleanly_after_item_budget_is_full(self) -> None:
        execution_id = self.repository.create_execution(
            run_id="r-concurrent-item-limit",
            intent={"queries": ["QA"]},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            active_runtime_budget_ms=1_000_000,
        )
        manifest = _manifest()
        source_plan_id = self.repository.create_source_plan(
            execution_id=execution_id,
            source_id="hh_ru",
            manifest=manifest,
            queries=("QA",),
            unit_budget=2,
            item_budget=1,
            invocation_budget=2,
        )
        for page in range(2):
            self.repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=manifest.ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=manifest.input_schema_id,
                    parser_input=_input(page),
                    task_class=TaskClass.LISTING,
                    task_key=f"concurrent-item-limit-page-{page}",
                    available_at=100.0,
                    reserved_collection_units=1,
                )
            )
        first, second = self.repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id="worker",
            limit=2,
            lease_seconds=30.0,
            now=100.0,
        )

        for index, invocation in enumerate((first, second), start=1):
            self.repository.commit_search_result(
                invocation,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing(str(index)),),
                    continuations=(_input(index + 1),),
                    collection_units_consumed=1,
                ),
                manifest,
                now=100.0 + index,
            )

        with closing(sqlite3.connect(self.database_path)) as connection:
            plan = connection.execute(
                "SELECT status, terminal_reason, items_used, invocations_used "
                "FROM source_plans WHERE source_plan_id = ?",
                (source_plan_id,),
            ).fetchone()
            invocation_rows = connection.execute(
                "SELECT status, outcome FROM parser_invocations "
                "WHERE source_plan_id = ? ORDER BY task_key",
                (source_plan_id,),
            ).fetchall()
        self.assertEqual(plan, ("limit_reached", "item_limit", 1, 2))
        self.assertEqual(invocation_rows, [("succeeded", "success"), ("succeeded", "success")])

    def test_deadline_settlement_fences_late_parser_commit(self) -> None:
        execution_id = self.repository.create_execution(
            run_id="r-deadline",
            intent={"queries": ["QA"]},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            active_runtime_budget_ms=1_000,
        )
        source_plan_id = self.repository.create_source_plan(
            execution_id=execution_id,
            source_id="hh_ru",
            manifest=_manifest(),
            queries=("QA",),
            unit_budget=1,
            item_budget=10,
            invocation_budget=1,
        )
        self.repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=execution_id,
                source_plan_id=source_plan_id,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=_manifest().ref,
                parser_type=ParserType.SEARCH_LISTING,
                input_schema_id=_manifest().input_schema_id,
                parser_input=_input(0),
                task_class=TaskClass.LISTING,
                task_key="deadline-page",
                available_at=0.0,
                reserved_collection_units=1,
            )
        )
        self.repository.begin_execution_sessions((execution_id,), now=90.0)
        leased = self.repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id="worker",
            limit=1,
            lease_seconds=30.0,
            now=90.0,
        )[0]

        settled = self.repository.settle_deadline(execution_id, now=101.0)

        self.assertTrue(settled)
        with self.assertRaises(StaleLeaseError):
            self.repository.commit_search_result(
                leased,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing(),),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                _manifest(),
                now=101.0,
            )
        self.assertEqual(
            self._scalar("SELECT status FROM parser_invocations WHERE execution_id = ?", (execution_id,)),
            "cancelled",
        )
        self.assertEqual(
            self._scalar("SELECT status FROM source_plans WHERE source_plan_id = ?", (source_plan_id,)),
            "cancelled",
        )

    def _count(self, table: str) -> int:
        return cast(int, self._scalar(f"SELECT COUNT(*) FROM {table}", ()))

    def _scalar(self, query: str, parameters: tuple[object, ...]) -> object:
        return self._row(query, parameters)[0]

    def _row(self, query: str, parameters: tuple[object, ...]) -> sqlite3.Row:
        rows = self._query(query, parameters)
        if len(rows) != 1:
            self.fail(f"expected one row, got {len(rows)}")
        return rows[0]

    def _query(self, query: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(query, parameters).fetchall()

    def _column_names(self, table: str) -> set[str]:
        return {str(row[1]) for row in self._query(f"PRAGMA table_info({table})", ())}

    def _foreign_keys(self, table: str) -> dict[str, tuple[str, str]]:
        return {
            str(row[3]): (str(row[2]), str(row[4]))
            for row in self._query(f"PRAGMA foreign_key_list({table})", ())
        }


if __name__ == "__main__":
    unittest.main()
