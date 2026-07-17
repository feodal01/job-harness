from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from job_harness.v2.contracts import (
    CompanyRef,
    CompanySiteInput,
    CompanySiteOutput,
    CompanySiteResult,
    InvocationScope,
    ParserInvocationSpec,
    ParserManifest,
    ParserRef,
    ParserRegistry,
    ParserType,
    SingletonResultOutcome,
    SourceOutcome,
    StaleLeaseError,
    TaskClass,
    TransportKind,
    VacancyDetailInput,
    VacancyDetailOutput,
    VacancyDetailResult,
)
from job_harness.v2.contracts.errors import ClassifiedSourceError
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.ports import (
    HttpAction,
    HttpResponse,
    OperationContext,
    ParserAttemptMetrics,
    ParserRuntime,
    RetrySafety,
)
from job_harness.v2.runtime.errors import (
    HttpStatusError,
    ResponseSizeLimitError,
    UnsafeTargetError,
)
from job_harness.v2.runtime.executors import ManagedTaskRunner
from job_harness.v2.runtime.request_retry import (
    RequestAttemptError,
    RequestFailureKind,
    RequestRetryPolicy,
)


def _manifest(parser_id: str) -> ParserManifest:
    return ParserManifest(
        parser_id=parser_id,
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id=f"{parser_id}.input.v1",
        output_schema_id=f"{parser_id}.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("hh",),
        supported_url_patterns=(r"https://hh\.ru/vacancy/.*",),
        output_facts=("description",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )


def _input(vacancy_id: str) -> VacancyDetailInput:
    return VacancyDetailInput(
        target_provider_id="hh",
        vacancy_url=f"https://hh.ru/vacancy/{vacancy_id}",
        source_listing_id=vacancy_id,
    )


def _result(vacancy_id: str) -> VacancyDetailResult:
    return VacancyDetailResult(
        outcome=SingletonResultOutcome.SUCCESS,
        item=VacancyDetailOutput(
            target_provider_id="hh",
            source_listing_id=vacancy_id,
            canonical_vacancy_url=f"https://hh.ru/vacancy/{vacancy_id}",
            title="QA Engineer",
            company=CompanyRef(name="Example"),
            description="Full description",
            requirements=("Test APIs",),
            responsibilities=(),
            conditions=(),
            skills=("Python",),
            employment_types=("full_time",),
            salary=None,
            work_formats=("remote",),
            remote_scopes=(),
            application_channels=(),
        ),
    )


def _request_retry_policy() -> RequestRetryPolicy:
    return RequestRetryPolicy(
        max_attempts=3,
        attempt_timeout_seconds=15.0,
        base_delay_seconds=1.0,
        max_delay_seconds=8.0,
        request_budget_seconds=55.0,
        random_fraction=lambda: 0.0,
    )


class _Runtime(ParserRuntime):
    def __init__(
        self,
        context: OperationContext,
        reserved_collection_units: int,
        metrics: ParserAttemptMetrics,
    ) -> None:
        self.context = context
        self._reserved_collection_units = reserved_collection_units
        self._metrics = metrics

    @property
    def reserved_collection_units(self) -> int:
        return self._reserved_collection_units

    @property
    def attempt_metrics(self) -> ParserAttemptMetrics:
        return self._metrics

    async def http(self, _action: HttpAction) -> HttpResponse:
        raise AssertionError("network is not used by this fake bundle")


class _RuntimeFactory:
    def __init__(self, metrics: ParserAttemptMetrics | None = None) -> None:
        self.contexts: list[OperationContext] = []
        self.metrics = metrics or ParserAttemptMetrics()

    def create(self, context: OperationContext, *, reserved_collection_units: int) -> ParserRuntime:
        self.contexts.append(context)
        return _Runtime(context, reserved_collection_units, self.metrics)


class _DetailBundle:
    input_type = VacancyDetailInput
    result_type = VacancyDetailResult

    def __init__(self, parser_id: str, *, failure: Exception | None = None) -> None:
        self.manifest = _manifest(parser_id)
        self.failure = failure

    async def execute(self, parser_input: VacancyDetailInput, _runtime: ParserRuntime) -> VacancyDetailResult:
        if self.failure is not None:
            raise self.failure
        vacancy_id = parser_input.source_listing_id
        if vacancy_id is None:
            raise AssertionError("test input requires source listing id")
        return _result(vacancy_id)


class _FlakyDetailBundle(_DetailBundle):
    def __init__(self, parser_id: str, *, failures: int) -> None:
        super().__init__(parser_id)
        self._remaining_failures = failures

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        if self._remaining_failures:
            self._remaining_failures -= 1
            raise TimeoutError("temporary timeout")
        return await super().execute(parser_input, runtime)


class _FlakyRequestDetailBundle(_DetailBundle):
    def __init__(self, parser_id: str) -> None:
        super().__init__(parser_id)
        self._failed = False

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        if not self._failed:
            self._failed = True
            raise RequestAttemptError(
                failure_kind=RequestFailureKind.TIMEOUT,
                retry_safety=RetrySafety.SAFE,
                message="temporary page timeout",
            )
        return await super().execute(parser_input, runtime)


class _PacedDetailBundle(_DetailBundle):
    def build_action(self, parser_input: VacancyDetailInput) -> HttpAction:
        return HttpAction(
            method="GET",
            url=parser_input.vacancy_url,
            retry_safety=RetrySafety.SAFE,
        )

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        del parser_input, runtime
        raise AssertionError("paced invocation must not start parser execution")


class _PacedRuntime(_Runtime):
    async def prepare_http(self, _action: HttpAction) -> float | None:
        return 2.0


class _PacedRuntimeFactory:
    def create(self, context: OperationContext, *, reserved_collection_units: int) -> ParserRuntime:
        return _PacedRuntime(context, reserved_collection_units, ParserAttemptMetrics())


class _SlowDetailBundle(_DetailBundle):
    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        await asyncio.sleep(0.05)
        return await super().execute(parser_input, runtime)


class _SiteBundle:
    manifest = ParserManifest(
        parser_id="generic.site",
        parser_type=ParserType.COMPANY_SITE,
        implementation_version="1.0",
        input_schema_id="generic.site.input.v1",
        output_schema_id="generic.site.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("web",),
        supported_url_patterns=(r"https://.*",),
        output_facts=("careerEndpoints",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )
    input_type = CompanySiteInput
    result_type = CompanySiteResult

    async def execute(self, parser_input: CompanySiteInput, _runtime: ParserRuntime) -> CompanySiteResult:
        return CompanySiteResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=CompanySiteOutput(
                canonical_site_url=parser_input.site_url,
                company_name="Example",
                contacts=(),
                social_links=(),
                career_endpoints=(),
            ),
        )


class ManagedTaskRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.database_path = Path(self._temporary_directory.name) / "run.sqlite"
        self.repository = SqliteGraphRepository(self.database_path)
        self.execution_id = self.repository.create_execution(
            run_id="r-test",
            intent={"kind": "standalone"},
            append_sequence=0,
            policy_version="policy-v1",
            runtime_config_version="runtime-v1",
            active_runtime_budget_ms=1_000_000,
        )

    async def _cleanup(self) -> None:
        self.repository.close()
        self._temporary_directory.cleanup()

    async def test_standalone_detail_persists_without_listing_or_source_plan(self) -> None:
        bundle = _DetailBundle("hh.detail")
        runtime_factory = _RuntimeFactory()
        invocation_id = self._enqueue(bundle.manifest.ref, "123", task_key="detail-123")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=runtime_factory,
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
        )

        completed = await runner.run_once(self.execution_id, limit=1, now=100.0)

        self.assertEqual(completed, 1)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM vacancy_detail_observations"), 1)
        self.assertEqual(
            self._scalar("SELECT event_type FROM domain_events"),
            "vacancy_detail_observation_stored",
        )
        self.assertEqual(
            self._scalar("SELECT status FROM parser_invocations WHERE invocation_id = ?", (invocation_id,)),
            "succeeded",
        )
        self.assertEqual(runtime_factory.contexts[0].execution_id, self.execution_id)
        self.assertEqual(runtime_factory.contexts[0].invocation_id, invocation_id)
        attempt = self._query(
            "SELECT attempt_number, outcome, retry_decision FROM parser_attempts WHERE invocation_id = ?",
            (invocation_id,),
        )
        self.assertEqual(tuple(attempt[0]), (1, "success", None))

    async def test_one_failed_task_does_not_prevent_unrelated_task(self) -> None:
        failing = _DetailBundle("hh.detail.fail", failure=ValueError("broken parser"))
        successful = _DetailBundle("hh.detail.ok")
        self._enqueue(failing.manifest.ref, "1", task_key="detail-1")
        self._enqueue(successful.manifest.ref, "2", task_key="detail-2")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((failing, successful)),
            runtime_factory=_RuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
        )

        completed = await runner.run_once(self.execution_id, limit=2, now=100.0)

        statuses = tuple(
            row[0]
            for row in self._query("SELECT status FROM parser_invocations ORDER BY task_key", ())
        )
        self.assertEqual(completed, 2)
        self.assertEqual(statuses, ("failed", "succeeded"))
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM vacancy_detail_observations"), 1)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM domain_events"), 2)

    async def test_stale_lease_while_committing_failure_does_not_escape_worker(self) -> None:
        bundle = _DetailBundle("hh.detail.stale-failure", failure=ValueError("broken parser"))
        self._enqueue(bundle.manifest.ref, "1", task_key="detail-stale-failure")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
        )

        with patch.object(
            self.repository,
            "commit_failure",
            side_effect=StaleLeaseError("lease reassigned"),
        ):
            completed = await runner.run_once(self.execution_id, limit=1, now=100.0)

        self.assertEqual(completed, 1)

    async def test_company_site_uses_the_same_managed_runner(self) -> None:
        bundle = _SiteBundle()
        invocation_id = self.repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=self.execution_id,
                source_plan_id=None,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=bundle.manifest.ref,
                parser_type=ParserType.COMPANY_SITE,
                input_schema_id=bundle.manifest.input_schema_id,
                parser_input=CompanySiteInput(site_url="https://example.com"),
                task_class=TaskClass.SITE,
                task_key="site-example",
                available_at=0.0,
                reserved_collection_units=None,
            )
        )
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
        )

        completed = await runner.run_once(self.execution_id, limit=1, now=100.0)

        self.assertEqual(completed, 1)
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM company_site_observations"), 1)
        self.assertEqual(
            self._scalar("SELECT status FROM parser_invocations WHERE invocation_id = ?", (invocation_id,)),
            "succeeded",
        )

    async def test_retryable_failure_waits_then_succeeds_with_two_attempt_rows(self) -> None:
        bundle = _FlakyRequestDetailBundle("hh.detail.flaky")
        invocation_id = self._enqueue(bundle.manifest.ref, "123", task_key="detail-flaky")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=RequestRetryPolicy(
                max_attempts=3,
                attempt_timeout_seconds=15.0,
                base_delay_seconds=2.0,
                max_delay_seconds=8.0,
                request_budget_seconds=55.0,
                random_fraction=lambda: 1.0,
            ),
        )

        first = await runner.run_once(self.execution_id, limit=1, now=100.0)
        too_early = await runner.run_once(self.execution_id, limit=1, now=101.0)
        second = await runner.run_once(self.execution_id, limit=1, now=102.0)

        self.assertEqual((first, too_early, second), (1, 0, 1))
        self.assertEqual(
            self._scalar("SELECT status FROM parser_invocations WHERE invocation_id = ?", (invocation_id,)),
            "succeeded",
        )
        attempts = self._query(
            (
                "SELECT attempt_number, outcome, retry_decision, retry_delay_ms "
                "FROM parser_attempts ORDER BY attempt_number"
            ),
            (),
        )
        self.assertEqual(
            tuple(tuple(row) for row in attempts),
            ((1, "source_timeout", "scheduled", 2_000), (2, "success", None, 0)),
        )
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM domain_events"), 1)

    async def test_resource_pacing_releases_lease_without_consuming_request_retry(self) -> None:
        bundle = _PacedDetailBundle("hh.detail.paced")
        invocation_id = self._enqueue(bundle.manifest.ref, "123", task_key="detail-paced")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=_PacedRuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
        )

        completed = await runner.run_once(self.execution_id, limit=1, now=100.0)

        self.assertEqual(completed, 1)
        self.assertEqual(
            tuple(
                self._query(
                    "SELECT status, waiting_reason, available_at, lease_owner "
                    "FROM parser_invocations WHERE invocation_id = ?",
                    (invocation_id,),
                )[0]
            ),
            ("waiting", "resource_pacing", 102.0, None),
        )
        self.assertEqual(self._scalar("SELECT COUNT(*) FROM parser_attempts"), 0)
        self.assertEqual(self.repository.request_attempt_number(invocation_id), 0)

    async def test_attempt_timeout_retries_only_the_timed_out_invocation(self) -> None:
        bundle = _SlowDetailBundle("hh.detail.slow")
        invocation_id = self._enqueue(bundle.manifest.ref, "123", task_key="detail-slow")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
            attempt_timeout_seconds=0.01,
        )

        first = await runner.run_once(self.execution_id, limit=1, now=100.0)
        second = await runner.run_once(self.execution_id, limit=1, now=101.0)
        third = await runner.run_once(self.execution_id, limit=1, now=102.0)

        self.assertEqual((first, second, third), (1, 1, 1))
        self.assertEqual(
            self._scalar("SELECT outcome FROM parser_invocations WHERE invocation_id = ?", (invocation_id,)),
            "source_timeout",
        )
        self.assertEqual(
            tuple(
                tuple(row)
                for row in self._query(
                    "SELECT attempt_number, outcome, retry_decision "
                    "FROM parser_attempts ORDER BY attempt_number",
                    (),
                )
            ),
            (
                (1, "source_timeout", "scheduled"),
                (2, "source_timeout", "scheduled"),
                (3, "source_timeout", "exhausted"),
            ),
        )

    async def test_rate_limited_response_is_classified_without_a_second_retry_policy(self) -> None:
        bundle = _DetailBundle(
            "hh.detail.rate-limited",
            failure=HttpStatusError(
                status_code=429,
                final_url="https://hh.ru/vacancy/123",
            ),
        )
        invocation_id = self._enqueue(bundle.manifest.ref, "123", task_key="detail-rate-limited")
        runtime_factory = _RuntimeFactory(
            ParserAttemptMetrics(
                network_action_count=1,
                network_elapsed_ms=37,
                last_status_code=429,
                last_error_class="HttpStatusError",
            )
        )
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=runtime_factory,
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
        )

        completed = await runner.run_once(self.execution_id, limit=1, now=100.0)

        self.assertEqual(completed, 1)
        attempt = self._query(
            (
                "SELECT outcome, failure_kind, retry_decision, network_action_count, "
                "network_elapsed_ms, last_status_code, last_error_class "
                "FROM parser_attempts WHERE invocation_id = ?"
            ),
            (invocation_id,),
        )
        self.assertEqual(
            tuple(attempt[0]),
            ("rate_limited", "rate_limited", "terminal", 1, 37, 429, "HttpStatusError"),
        )

    async def test_failure_classifier_uses_canonical_runtime_outcomes(self) -> None:
        cases = (
            (HttpStatusError(status_code=404, final_url="https://hh.ru/missing"), "http_client_error"),
            (HttpStatusError(status_code=503, final_url="https://hh.ru/down"), "http_server_error"),
            (OSError("connection reset"), "network_error"),
            (ValueError("malformed payload"), "parse_error"),
            (TimeoutError("source deadline"), "source_timeout"),
            (ResponseSizeLimitError("too large"), "resource_failure"),
            (UnsafeTargetError("private target"), "unsupported_target"),
            (
                ClassifiedSourceError(SourceOutcome.BLOCKED, "Jobvite unavailable redirect"),
                "blocked",
            ),
        )
        for index, (failure, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                bundle = _DetailBundle(f"hh.detail.failure-{index}", failure=failure)
                invocation_id = self._enqueue(
                    bundle.manifest.ref,
                    str(index),
                    task_key=f"detail-failure-{index}",
                )
                runner = ManagedTaskRunner(
                    repository=self.repository,
                    registry=ParserRegistry((bundle,)),
                    runtime_factory=_RuntimeFactory(),
                    owner_id="worker",
                    lease_seconds=30.0,
                    request_retry_policy=_request_retry_policy(),
                )

                await runner.run_once(self.execution_id, limit=1, now=100.0 + index)

                self.assertEqual(
                    self._scalar(
                        "SELECT failure_kind FROM parser_attempts WHERE invocation_id = ?",
                        (invocation_id,),
                    ),
                    expected,
                )

    async def test_failure_uses_completion_clock_for_attempt_timestamps(self) -> None:
        bundle = _DetailBundle("hh.detail.fail-clock", failure=ValueError("broken parser"))
        invocation_id = self._enqueue(bundle.manifest.ref, "123", task_key="detail-fail-clock")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
            clock=lambda: 115.0,
        )

        await runner.run_once(self.execution_id, limit=1, now=100.0)

        attempt = self._query(
            "SELECT started_at, finished_at FROM parser_attempts WHERE invocation_id = ?",
            (invocation_id,),
        )
        self.assertEqual(tuple(attempt[0]), (100.0, 115.0))

    async def test_missing_pinned_implementation_fails_explicitly(self) -> None:
        parser_ref = ParserRef("removed.detail", "9.9")
        invocation_id = self._enqueue(parser_ref, "123", task_key="detail-removed")
        runner = ManagedTaskRunner(
            repository=self.repository,
            registry=ParserRegistry(()),
            runtime_factory=_RuntimeFactory(),
            owner_id="worker",
            lease_seconds=30.0,
            request_retry_policy=_request_retry_policy(),
        )

        completed = await runner.run_once(self.execution_id, limit=1, now=100.0)

        self.assertEqual(completed, 1)
        self.assertEqual(
            self._scalar("SELECT outcome FROM parser_invocations WHERE invocation_id = ?", (invocation_id,)),
            "implementation_unavailable",
        )
        self.assertEqual(
            self._scalar("SELECT failure_kind FROM parser_attempts WHERE invocation_id = ?", (invocation_id,)),
            "implementation_unavailable",
        )

    def _enqueue(self, parser_ref: ParserRef, vacancy_id: str, *, task_key: str) -> str:
        return self.repository.enqueue_invocation(
            ParserInvocationSpec(
                execution_id=self.execution_id,
                source_plan_id=None,
                parent_invocation_id=None,
                cause_event_id=None,
                parser_ref=parser_ref,
                parser_type=ParserType.VACANCY_DETAIL,
                input_schema_id=f"{parser_ref.parser_id}.input.v1",
                parser_input=_input(vacancy_id),
                task_class=TaskClass.DETAIL,
                task_key=task_key,
                available_at=0.0,
                reserved_collection_units=None,
            )
        )

    def _scalar(self, query: str, parameters: tuple[object, ...] = ()) -> object:
        rows = self._query(query, parameters)
        if len(rows) != 1:
            self.fail(f"expected one row, got {len(rows)}")
        return rows[0][0]

    def _query(self, query: str, parameters: tuple[object, ...]) -> list[sqlite3.Row]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(query, parameters).fetchall()


if __name__ == "__main__":
    unittest.main()
