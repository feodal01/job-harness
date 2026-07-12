"""Direct and managed parser execution boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast
from uuid import uuid4

from job_harness.v2.contracts import (
    CompanyProfileResult,
    CompanySiteResult,
    LeasedParserInvocation,
    ParserExecutionResult,
    ParserFailure,
    ParserFailureKind,
    ParserInput,
    ParserRef,
    ParserRegistry,
    ParserResult,
    SearchListingResult,
    StaleLeaseError,
    VacancyDetailResult,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.ports import OperationContext, ParserRuntimeFactory


class DirectScraperExecutor:
    def __init__(self, *, registry: ParserRegistry, runtime_factory: ParserRuntimeFactory) -> None:
        self._registry = registry
        self._runtime_factory = runtime_factory

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

        runtime = self._runtime_factory.create(
            OperationContext(
                operation_id=f"direct-{uuid4().hex}",
                execution_id=None,
                invocation_id=None,
            ),
            reserved_collection_units=manifest.max_units_per_invocation,
        )
        result = await execute(parser_input, runtime)
        if not isinstance(result, result_type):
            return _failure(ParserFailureKind.INVALID_OUTPUT)
        if (
            isinstance(result, SearchListingResult)
            and result.collection_units_consumed > manifest.max_units_per_invocation
        ):
            return _failure(ParserFailureKind.INVALID_OUTPUT)
        return ParserExecutionResult(result=cast(ParserResult, result))


def _failure(kind: ParserFailureKind) -> ParserExecutionResult:
    return ParserExecutionResult(
        failure=ParserFailure(
            kind=kind,
            retryable=False,
        )
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
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be > 0")
        self._repository = repository
        self._registry = registry
        self._runtime_factory = runtime_factory
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._clock = clock

    async def run_once(self, execution_id: str, *, limit: int, now: float) -> int:
        invocations = self._repository.lease_ready_invocations(
            execution_id=execution_id,
            owner_id=self._owner_id,
            limit=limit,
            lease_seconds=self._lease_seconds,
            now=now,
        )
        await asyncio.gather(*(self._execute(invocation, now=now) for invocation in invocations))
        return len(invocations)

    async def _execute(self, invocation: LeasedParserInvocation, *, now: float) -> None:
        attempt_id, attempt_number = self._repository.begin_parser_attempt(invocation, now=now)
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
            result = await bundle.execute(invocation.parser_input, runtime)
            commit_now = now if self._clock is None else self._clock()
            if not isinstance(result, result_type):
                raise ValueError("parser returned the wrong result type")
            if isinstance(result, SearchListingResult):
                self._repository.commit_search_result(
                    invocation,
                    result,
                    manifest,
                    attempt_id=attempt_id,
                    now=commit_now,
                )
            elif isinstance(result, VacancyDetailResult):
                self._repository.commit_detail_result(
                    invocation,
                    result,
                    manifest,
                    attempt_id=attempt_id,
                    now=commit_now,
                )
            elif isinstance(result, CompanyProfileResult):
                self._repository.commit_profile_result(
                    invocation,
                    result,
                    manifest,
                    attempt_id=attempt_id,
                    now=commit_now,
                )
            elif isinstance(result, CompanySiteResult):
                self._repository.commit_site_result(
                    invocation,
                    result,
                    manifest,
                    attempt_id=attempt_id,
                    now=commit_now,
                )
            else:
                raise ValueError("managed result type is not implemented")
        except StaleLeaseError:
            return
        except Exception as exc:
            failure = _classify_failure(exc)
            if failure.retryable and attempt_number < self._max_attempts:
                self._repository.commit_retry(
                    invocation,
                    attempt_id=attempt_id,
                    failure_kind=failure.kind.value,
                    available_at=now + self._retry_delay_seconds * (2 ** (attempt_number - 1)),
                    now=now,
                )
                return
            self._repository.commit_failure(
                invocation,
                attempt_id=attempt_id,
                failure_kind=failure.kind.value,
                retryable=failure.retryable,
                public_notice=failure.public_notice,
                now=now,
            )


def _classify_failure(exc: Exception) -> ParserFailure:
    if isinstance(exc, KeyError):
        return ParserFailure(
            ParserFailureKind.IMPLEMENTATION_UNAVAILABLE,
            retryable=False,
            public_notice=str(exc) or None,
        )
    if isinstance(exc, TimeoutError):
        return ParserFailure(ParserFailureKind.TIMEOUT, retryable=True, public_notice=str(exc) or None)
    if isinstance(exc, OSError):
        return ParserFailure(ParserFailureKind.NETWORK, retryable=True, public_notice=str(exc) or None)
    if isinstance(exc, ValueError):
        return ParserFailure(ParserFailureKind.PARSE, retryable=False, public_notice=str(exc) or None)
    return ParserFailure(ParserFailureKind.RESOURCE, retryable=False, public_notice=str(exc) or None)
