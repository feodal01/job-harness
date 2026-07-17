"""Direct and managed parser execution boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import cast
from uuid import uuid4

from job_harness.v2.contracts import (
    ClassifiedSourceError,
    CompanyProfileResult,
    CompanySiteResult,
    LeasedParserInvocation,
    ParserExecutionResult,
    ParserFailure,
    ParserFailureKind,
    ParserInput,
    ParserManifest,
    ParserRef,
    ParserRegistry,
    ParserResult,
    SearchListingResult,
    SourceOutcome,
    StaleLeaseError,
    VacancyDetailResult,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.ports import (
    HttpAction,
    OperationContext,
    ParserAttemptMetrics,
    ParserRuntime,
    ParserRuntimeFactory,
    RetrySafety,
)
from job_harness.v2.runtime.errors import (
    HttpStatusError,
    RedirectLimitError,
    ResponseSizeLimitError,
    UnsafeTargetError,
)
from job_harness.v2.runtime.invocation_resources import invocation_resource_key
from job_harness.v2.runtime.request_retry import (
    InMemoryRequestRetrier,
    RequestAttemptError,
    RequestFailureKind,
    RequestRetryDisposition,
    RequestRetryPolicy,
)

_HTTP_REQUEST_TIMEOUT = 408
_HTTP_RATE_LIMITED = 429
_HTTP_SERVER_ERROR_MIN = 500
_HTTP_SERVER_ERROR_MAX = 600
_SOURCE_FAILURE_KINDS = {
    SourceOutcome.BLOCKED: ParserFailureKind.BLOCKED,
    SourceOutcome.RATE_LIMITED: ParserFailureKind.RATE_LIMITED,
    SourceOutcome.SOURCE_TIMEOUT: ParserFailureKind.SOURCE_TIMEOUT,
    SourceOutcome.HTTP_CLIENT_ERROR: ParserFailureKind.HTTP_CLIENT_ERROR,
    SourceOutcome.HTTP_SERVER_ERROR: ParserFailureKind.HTTP_SERVER_ERROR,
    SourceOutcome.NETWORK_ERROR: ParserFailureKind.NETWORK_ERROR,
    SourceOutcome.PARSE_ERROR: ParserFailureKind.PARSE_ERROR,
    SourceOutcome.INVALID_SOURCE_OUTPUT: ParserFailureKind.INVALID_SOURCE_OUTPUT,
    SourceOutcome.RESOURCE_FAILURE: ParserFailureKind.RESOURCE_FAILURE,
}
class DirectScraperExecutor:
    def __init__(
        self,
        *,
        registry: ParserRegistry,
        runtime_factory: ParserRuntimeFactory,
        request_retry_policy: RequestRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._registry = registry
        self._runtime_factory = runtime_factory
        self._request_retry_policy = request_retry_policy or RequestRetryPolicy()
        self._request_retrier = InMemoryRequestRetrier(
            policy=self._request_retry_policy,
            sleep=sleep,
            clock=clock,
        )

    async def execute(self, parser_ref: ParserRef, parser_input: ParserInput) -> ParserExecutionResult:
        bundle = self._registry.get(parser_ref)
        manifest = self._registry.manifest(parser_ref)
        input_type = getattr(bundle, "input_type", None)
        result_type = getattr(bundle, "result_type", None)
        execute = getattr(bundle, "execute", None)
        if not isinstance(input_type, type) or not isinstance(parser_input, input_type):
            return _failure(ParserFailureKind.INVALID_INPUT)
        if not isinstance(result_type, type) or not callable(execute):
            return _failure(ParserFailureKind.IMPLEMENTATION_UNAVAILABLE)

        async def execute_attempt() -> object:
            runtime = self._runtime_factory.create(
                OperationContext(
                    operation_id=f"direct-{uuid4().hex}",
                    execution_id=None,
                    invocation_id=None,
                ),
                reserved_collection_units=manifest.max_units_per_invocation,
            )
            try:
                async with asyncio.timeout(self._request_retry_policy.attempt_timeout_seconds):
                    return await execute(parser_input, runtime)
            except Exception as exc:
                request_error = _request_error_for_retry(exc)
                if request_error is exc or request_error is None:
                    raise
                raise request_error from exc

        try:
            result = await self._request_retrier.run(execute_attempt)
        except Exception as exc:
            return ParserExecutionResult(failure=_classify_failure(exc))
        if not isinstance(result, result_type):
            return _failure(ParserFailureKind.INVALID_SOURCE_OUTPUT)
        if (
            isinstance(result, SearchListingResult)
            and result.collection_units_consumed > manifest.max_units_per_invocation
        ):
            return _failure(ParserFailureKind.INVALID_SOURCE_OUTPUT)
        return ParserExecutionResult(result=cast(ParserResult, result))


def _failure(kind: ParserFailureKind) -> ParserExecutionResult:
    return ParserExecutionResult(
        failure=ParserFailure(kind=kind)
    )


class ManagedTaskRunner:
    def __init__(
        self,
        *,
        repository: SqliteGraphRepository,
        registry: ParserRegistry,
        runtime_factory: ParserRuntimeFactory,
        owner_id: str,
        lease_seconds: float,
        request_retry_policy: RequestRetryPolicy,
        attempt_timeout_seconds: float = 180.0,
        resource_key_resolver: Callable[[str], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be > 0")
        self._repository = repository
        self._registry = registry
        self._runtime_factory = runtime_factory
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._request_retry_policy = request_retry_policy
        self._attempt_timeout_seconds = attempt_timeout_seconds
        self._resource_key_resolver = resource_key_resolver or _identity_resource_key
        self._clock = clock

    @property
    def owner_id(self) -> str:
        return self._owner_id

    async def run_once(self, execution_id: str, *, limit: int, now: float) -> int:
        invocations = self.lease_ready(execution_id, limit=limit, now=now)
        await asyncio.gather(*(self.execute(invocation, now=now) for invocation in invocations))
        return len(invocations)

    def lease_ready(
        self,
        execution_id: str,
        *,
        limit: int,
        now: float,
        excluded_resource_keys: tuple[str, ...] = (),
    ) -> tuple[LeasedParserInvocation, ...]:
        unresolved = self._repository.unresolved_ready_invocations(
            execution_id,
            limit=limit,
            now=now,
        )
        self._repository.resolve_invocation_resource_keys(
            tuple(
                (
                    invocation_id,
                    invocation_resource_key(
                        self._registry,
                        spec.parser_ref,
                        spec.parser_input,
                        self._resource_key_resolver,
                    ),
                )
                for invocation_id, spec in unresolved
            )
        )
        return self._repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id=self._owner_id,
            limit=limit,
            lease_seconds=self._lease_seconds,
            now=now,
            excluded_resource_keys=excluded_resource_keys,
            require_resolved_resource_keys=True,
        )

    async def execute(self, invocation: LeasedParserInvocation, *, now: float) -> None:
        attempt_id: str | None = None
        runtime: ParserRuntime | None = None
        try:
            bundle = self._registry.get(invocation.spec.parser_ref)
            manifest = self._registry.manifest(invocation.spec.parser_ref)
            input_type = bundle.input_type
            result_type = bundle.result_type
            if not isinstance(invocation.parser_input, input_type):
                raise ValueError("stored parser input does not match bundle")
            runtime = self._runtime_factory.create(
                OperationContext(
                    operation_id=f"managed-{invocation.invocation_id}-{invocation.lease_token}",
                    execution_id=invocation.spec.execution_id,
                    invocation_id=invocation.invocation_id,
                ),
                reserved_collection_units=invocation.spec.reserved_collection_units or 1,
            )
            build_action = getattr(bundle, "build_action", None)
            prepare_http = getattr(runtime, "prepare_http", None)
            if callable(build_action) and callable(prepare_http):
                action = build_action(invocation.parser_input)
                if not isinstance(action, HttpAction):
                    raise TypeError("bundle build_action must return HttpAction")
                retry_after_seconds = await prepare_http(action)
                if retry_after_seconds is not None:
                    commit_now = self._completion_time(now)
                    self._repository.defer_unstarted_invocation(
                        invocation,
                        waiting_reason="resource_pacing",
                        available_at=commit_now + max(retry_after_seconds, 0.001),
                        now=commit_now,
                    )
                    return
            attempt_id, _attempt_number = self._repository.begin_parser_attempt(
                invocation,
                now=now,
            )
            async with asyncio.timeout(self._attempt_timeout_seconds):
                result = await bundle.execute(invocation.parser_input, runtime)
            commit_now = self._completion_time(now)
            if not isinstance(result, result_type):
                raise ValueError("parser returned the wrong result type")
            self._commit_result(
                invocation,
                result,
                manifest,
                attempt_id=attempt_id,
                attempt_metrics=runtime.attempt_metrics,
                now=commit_now,
            )
        except StaleLeaseError:
            return
        except Exception as exc:
            try:
                self._commit_failure(
                    invocation,
                    exc,
                    attempt_id=attempt_id,
                    runtime=runtime,
                    started_at=now,
                )
            except StaleLeaseError:
                return
        finally:
            if runtime is not None:
                release_prepared_http = getattr(runtime, "release_prepared_http", None)
                if callable(release_prepared_http):
                    await release_prepared_http()

    def _commit_result(
        self,
        invocation: LeasedParserInvocation,
        result: ParserResult,
        manifest: ParserManifest,
        *,
        attempt_id: str,
        attempt_metrics: ParserAttemptMetrics,
        now: float,
    ) -> None:
        if isinstance(result, SearchListingResult):
            self._repository.commit_search_result(
                invocation,
                result,
                manifest,
                attempt_id=attempt_id,
                attempt_metrics=attempt_metrics,
                now=now,
            )
        elif isinstance(result, VacancyDetailResult):
            self._repository.commit_detail_result(
                invocation,
                result,
                manifest,
                attempt_id=attempt_id,
                attempt_metrics=attempt_metrics,
                now=now,
            )
        elif isinstance(result, CompanyProfileResult):
            self._repository.commit_profile_result(
                invocation,
                result,
                manifest,
                attempt_id=attempt_id,
                attempt_metrics=attempt_metrics,
                now=now,
            )
        elif isinstance(result, CompanySiteResult):
            self._repository.commit_site_result(
                invocation,
                result,
                manifest,
                attempt_id=attempt_id,
                attempt_metrics=attempt_metrics,
                now=now,
            )
        else:
            raise ValueError("managed result type is not implemented")

    def _commit_failure(
        self,
        invocation: LeasedParserInvocation,
        exc: Exception,
        *,
        attempt_id: str | None,
        runtime: ParserRuntime | None,
        started_at: float,
    ) -> None:
        commit_now = self._completion_time(started_at)
        failure = _classify_failure(exc)
        request_error = _request_error_for_retry(exc)
        if attempt_id is None:
            try:
                attempt_id, _attempt_number = self._repository.begin_parser_attempt(
                    invocation,
                    now=started_at,
                )
            except StaleLeaseError:
                return
        attempt_metrics = runtime.attempt_metrics if runtime is not None else ParserAttemptMetrics()
        retry_decision = RequestRetryDisposition.TERMINAL.value
        if request_error is not None:
            decision = self._request_retry_policy.decide(
                retry_safety=request_error.retry_safety,
                failure_kind=request_error.failure_kind,
                attempt_number=self._repository.request_attempt_number(invocation.invocation_id),
                elapsed_seconds=self._repository.request_retry_elapsed_seconds(
                    invocation.invocation_id,
                    current_network_elapsed_ms=attempt_metrics.network_elapsed_ms,
                ),
                status_code=request_error.status_code,
                retry_after_seconds=request_error.retry_after_seconds,
            )
            if decision.disposition == RequestRetryDisposition.SCHEDULE:
                self._repository.defer_invocation(
                    invocation,
                    attempt_id=attempt_id,
                    failure_kind=failure.kind.value,
                    attempt_metrics=attempt_metrics,
                    waiting_reason="retry_backoff",
                    retry_delay_ms=round(decision.delay_seconds * 1000),
                    available_at=commit_now + max(decision.delay_seconds, 0.001),
                    now=commit_now,
                )
                return
            retry_decision = decision.disposition.value
        self._repository.commit_failure(
            invocation,
            attempt_id=attempt_id,
            failure_kind=failure.kind.value,
            retry_decision=retry_decision,
            public_notice=failure.public_notice,
            attempt_metrics=attempt_metrics,
            now=commit_now,
        )

    def _completion_time(self, started_at: float) -> float:
        return started_at if self._clock is None else self._clock()


def _request_error_for_retry(exc: Exception) -> RequestAttemptError | None:
    if isinstance(exc, RequestAttemptError):
        return exc
    if isinstance(exc, TimeoutError):
        return RequestAttemptError(
            failure_kind=RequestFailureKind.TIMEOUT,
            retry_safety=RetrySafety.SAFE,
            message=str(exc) or "parser invocation timed out",
        )
    if isinstance(exc, OSError):
        return RequestAttemptError(
            failure_kind=RequestFailureKind.NETWORK,
            retry_safety=RetrySafety.SAFE,
            message=str(exc) or "parser invocation network failure",
        )
    return None


def _classify_failure(exc: Exception) -> ParserFailure:
    if isinstance(exc, RequestAttemptError):
        kind = {
            RequestFailureKind.TIMEOUT: ParserFailureKind.SOURCE_TIMEOUT,
            RequestFailureKind.NETWORK: ParserFailureKind.NETWORK_ERROR,
            RequestFailureKind.HTTP_STATUS: _http_status_failure_kind(exc.status_code),
        }[exc.failure_kind]
        return ParserFailure(kind, public_notice=str(exc) or None)
    if isinstance(exc, ClassifiedSourceError):
        classified_kind = _SOURCE_FAILURE_KINDS.get(exc.outcome)
        if classified_kind is None:
            classified_kind = ParserFailureKind.INVALID_SOURCE_OUTPUT
        return ParserFailure(
            classified_kind,
            public_notice=str(exc) or None,
        )
    if isinstance(exc, HttpStatusError):
        return _http_status_failure(exc)
    if isinstance(exc, UnsafeTargetError):
        return ParserFailure(
            ParserFailureKind.UNSUPPORTED_TARGET,
            public_notice=str(exc) or None,
        )
    if isinstance(exc, ResponseSizeLimitError | RedirectLimitError):
        return ParserFailure(
            ParserFailureKind.RESOURCE_FAILURE,
            public_notice=str(exc) or None,
        )
    if isinstance(exc, KeyError):
        return ParserFailure(
            ParserFailureKind.IMPLEMENTATION_UNAVAILABLE,
            public_notice=str(exc) or None,
        )
    if isinstance(exc, TimeoutError):
        return ParserFailure(
            ParserFailureKind.SOURCE_TIMEOUT,
            public_notice=str(exc) or None,
        )
    if isinstance(exc, OSError):
        return ParserFailure(
            ParserFailureKind.NETWORK_ERROR,
            public_notice=str(exc) or None,
        )
    if isinstance(exc, ValueError):
        return ParserFailure(
            ParserFailureKind.PARSE_ERROR,
            public_notice=str(exc) or None,
        )
    return ParserFailure(
        ParserFailureKind.RESOURCE_FAILURE,
        public_notice=str(exc) or None,
    )


def _http_status_failure_kind(status_code: int | None) -> ParserFailureKind:
    if status_code is None:
        return ParserFailureKind.HTTP_SERVER_ERROR
    if status_code == _HTTP_RATE_LIMITED:
        return ParserFailureKind.RATE_LIMITED
    if status_code == _HTTP_REQUEST_TIMEOUT:
        return ParserFailureKind.SOURCE_TIMEOUT
    if _HTTP_SERVER_ERROR_MIN <= status_code < _HTTP_SERVER_ERROR_MAX:
        return ParserFailureKind.HTTP_SERVER_ERROR
    return ParserFailureKind.HTTP_CLIENT_ERROR


def _http_status_failure(exc: HttpStatusError) -> ParserFailure:
    if exc.status_code in {401, 403}:
        kind = ParserFailureKind.BLOCKED
    elif exc.status_code == _HTTP_REQUEST_TIMEOUT:
        kind = ParserFailureKind.SOURCE_TIMEOUT
    elif exc.status_code == _HTTP_RATE_LIMITED:
        kind = ParserFailureKind.RATE_LIMITED
    elif _HTTP_SERVER_ERROR_MIN <= exc.status_code < _HTTP_SERVER_ERROR_MAX:
        kind = ParserFailureKind.HTTP_SERVER_ERROR
    else:
        kind = ParserFailureKind.HTTP_CLIENT_ERROR
    return ParserFailure(kind, public_notice=str(exc))


def _identity_resource_key(host: str) -> str:
    return host
