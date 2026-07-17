"""Invocation-scoped network runtime exposed to scraper bundles."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from time import monotonic
from urllib.parse import urljoin, urlsplit

from job_harness.v2.ports import (
    HttpAction,
    HttpResponse,
    HttpTransport,
    OperationContext,
    ParserAttemptMetrics,
    RetrySafety,
)
from job_harness.v2.runtime.errors import (
    HttpStatusError,
    RedirectLimitError,
    ResponseSizeLimitError,
    UnsafeTargetError,
)
from job_harness.v2.runtime.request_retry import (
    RequestAttemptError,
    RequestFailureKind,
    is_retryable_http_status,
)
from job_harness.v2.runtime.resource_gate import (
    ResourceGate,
    ResourcePolicy,
    ResourceSlotPermit,
)

type HostResolver = Callable[[str], tuple[str, ...]]
type ResourcePolicyResolver = Callable[[str], ResourcePolicy]
type ResourceKeyResolver = Callable[[str], str]
type MonotonicClock = Callable[[], float]

_HTTP_SEE_OTHER = 303
_HTTP_SUCCESS_MIN = 200
_HTTP_REDIRECT_MIN = 300
_MAX_REDIRECTS = 10


@dataclass(frozen=True)
class _ResolvedTarget:
    host: str
    addresses: tuple[str, ...]


class DefaultParserRuntime:
    def __init__(
        self,
        *,
        context: OperationContext,
        reserved_collection_units: int,
        transport: HttpTransport,
        resource_gate: ResourceGate,
        policy_for_resource: ResourcePolicyResolver,
        timeout_seconds: float,
        max_response_bytes: int,
        host_resolver: HostResolver | None = None,
        resource_key_resolver: ResourceKeyResolver | None = None,
        clock: MonotonicClock = monotonic,
    ) -> None:
        if reserved_collection_units < 1:
            raise ValueError("reserved_collection_units must be >= 1")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be >= 1")
        self._context = context
        self._reserved_collection_units = reserved_collection_units
        self._transport = transport
        self._resource_gate = resource_gate
        self._policy_for_resource = policy_for_resource
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._host_resolver = host_resolver or _resolve_host
        self._resource_key_resolver = resource_key_resolver or (lambda host: host)
        self._clock = clock
        self._network_action_count = 0
        self._network_elapsed_ms = 0
        self._last_status_code: int | None = None
        self._last_error_class: str | None = None
        self._prepared_action: HttpAction | None = None
        self._prepared_permit: ResourceSlotPermit | None = None
        self._prepared_target: _ResolvedTarget | None = None

    @property
    def reserved_collection_units(self) -> int:
        return self._reserved_collection_units

    @property
    def attempt_metrics(self) -> ParserAttemptMetrics:
        return ParserAttemptMetrics(
            network_action_count=self._network_action_count,
            network_elapsed_ms=self._network_elapsed_ms,
            last_status_code=self._last_status_code,
            last_error_class=self._last_error_class,
        )

    async def prepare_http(self, action: HttpAction) -> float | None:
        if self._prepared_permit is not None:
            raise RuntimeError("parser runtime already has a prepared HTTP action")
        resource_key, policy, target = await asyncio.to_thread(self._resource_policy, action)
        decision = await self._resource_gate.try_admit_async(
            resource_key,
            policy,
            self._context,
        )
        if decision.permit is None:
            return max(decision.retry_after_seconds, 0.001)
        self._prepared_action = action
        self._prepared_permit = decision.permit
        self._prepared_target = target
        return None

    async def release_prepared_http(self) -> None:
        permit = self._prepared_permit
        self._prepared_action = None
        self._prepared_permit = None
        self._prepared_target = None
        if permit is not None:
            await self._resource_gate.release_async(permit)

    async def http(self, action: HttpAction) -> HttpResponse:
        try:
            current = action
            for redirect_count in range(_MAX_REDIRECTS + 1):
                response = await self._send_once(current)
                location = response.headers.get("location")
                if response.status_code not in {301, 302, 303, 307, 308}:
                    _raise_for_status(response)
                    return response
                if not location:
                    raise HttpStatusError(
                        status_code=response.status_code,
                        final_url=response.final_url,
                    )
                if redirect_count == _MAX_REDIRECTS:
                    raise RedirectLimitError("HTTP redirect limit exceeded")
                redirected_url = urljoin(response.final_url, location)
                method = "GET" if response.status_code == _HTTP_SEE_OTHER else current.method
                body = None if response.status_code == _HTTP_SEE_OTHER else current.body
                current = replace(
                    current,
                    method=method,
                    url=redirected_url,
                    headers=_redirect_headers(current, redirected_url),
                    body=body,
                    resource_key=None,
                    connection_addresses=(),
                )
        except Exception as exc:
            self._last_error_class = type(exc).__name__
            request_error = _request_attempt_error(exc, current.retry_safety)
            if request_error is not None:
                raise request_error from exc
            raise
        raise RuntimeError("unreachable redirect loop")

    async def _send_once(self, action: HttpAction) -> HttpResponse:
        prepared = await self._take_prepared_permit(action)
        if prepared is None:
            resource_key, policy, target = await asyncio.to_thread(self._resource_policy, action)
            permit = await self._resource_gate.admit(resource_key, policy, self._context)
        else:
            permit, target = prepared
        transport_action = replace(action, connection_addresses=target.addresses)
        started_at = self._clock()
        self._network_action_count += 1
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._transport.send(
                    transport_action,
                    timeout_seconds=self._timeout_seconds,
                )
            self._last_status_code = response.status_code
            _validate_response_target(response.final_url, expected_host=target.host)
            if len(response.body) > self._max_response_bytes:
                raise ResponseSizeLimitError("response body exceeds configured maximum")
            return response
        finally:
            elapsed_ms = max(0, round((self._clock() - started_at) * 1000))
            self._network_elapsed_ms += elapsed_ms
            await self._resource_gate.release_async(permit)

    def _resource_policy(
        self,
        action: HttpAction,
    ) -> tuple[str, ResourcePolicy, _ResolvedTarget]:
        target = self._validate_target(action.url)
        resource_key = self._resource_key_resolver(target.host)
        if action.resource_key is not None:
            if not action.resource_key.strip():
                raise ValueError("resource_key must be non-empty")
            resource_key = action.resource_key
        return resource_key, self._policy_for_resource(resource_key), target

    async def _take_prepared_permit(
        self,
        action: HttpAction,
    ) -> tuple[ResourceSlotPermit, _ResolvedTarget] | None:
        if self._prepared_permit is None:
            return None
        if self._prepared_action != action:
            await self.release_prepared_http()
            raise RuntimeError("executed HTTP action does not match prepared action")
        permit = self._prepared_permit
        target = self._prepared_target
        self._prepared_action = None
        self._prepared_permit = None
        self._prepared_target = None
        if target is None:
            await self._resource_gate.release_async(permit)
            raise RuntimeError("prepared HTTP action is missing its resolved target")
        return permit, target

    def _validate_target(self, url: str) -> _ResolvedTarget:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeTargetError("target must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeTargetError("target URL must not contain credentials")
        host = parsed.hostname.casefold()
        addresses = tuple(
            dict.fromkeys((host,) if _is_ip_literal(host) else self._host_resolver(host))
        )
        if not addresses:
            raise UnsafeTargetError("target host did not resolve")
        for address in addresses:
            if not _is_public_address(address):
                raise UnsafeTargetError(f"target resolves to a disallowed address: {address}")
        return _ResolvedTarget(host=host, addresses=addresses)


class DefaultParserRuntimeFactory:
    def __init__(
        self,
        *,
        transport: HttpTransport,
        resource_gate: ResourceGate,
        policy_for_resource: ResourcePolicyResolver,
        timeout_seconds: float,
        max_response_bytes: int,
        host_resolver: HostResolver | None = None,
        resource_key_resolver: ResourceKeyResolver | None = None,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self._transport = transport
        self._resource_gate = resource_gate
        self._policy_for_resource = policy_for_resource
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._host_resolver = host_resolver
        self._resource_key_resolver = resource_key_resolver
        self._clock = clock

    def create(
        self,
        context: OperationContext,
        *,
        reserved_collection_units: int,
    ) -> DefaultParserRuntime:
        return DefaultParserRuntime(
            context=context,
            reserved_collection_units=reserved_collection_units,
            transport=self._transport,
            resource_gate=self._resource_gate,
            policy_for_resource=self._policy_for_resource,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            host_resolver=self._host_resolver,
            resource_key_resolver=self._resource_key_resolver,
            clock=self._clock,
        )

def _resolve_host(host: str) -> tuple[str, ...]:
    try:
        return tuple(sorted({str(item[4][0]) for item in socket.getaddrinfo(host, None)}))
    except socket.gaierror as exc:
        raise UnsafeTargetError(f"target host resolution failed: {host}") from exc


def _validate_response_target(url: str, *, expected_host: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeTargetError("response URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeTargetError("response URL must not contain credentials")
    if parsed.hostname.casefold() != expected_host:
        raise UnsafeTargetError("transport returned a response for an unexpected host")


def _redirect_headers(action: HttpAction, redirected_url: str) -> Mapping[str, str]:
    current = urlsplit(action.url)
    redirected = urlsplit(redirected_url)
    if (current.scheme.casefold(), current.hostname, current.port) == (
        redirected.scheme.casefold(),
        redirected.hostname,
        redirected.port,
    ):
        return action.headers
    sensitive = {"authorization", "cookie", "proxy-authorization"}
    return {key: value for key, value in action.headers.items() if key.casefold() not in sensitive}


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise UnsafeTargetError(f"resolver returned an invalid address: {value}") from exc
    return address.is_global


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _raise_for_status(response: HttpResponse) -> None:
    if _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_REDIRECT_MIN:
        return
    raise HttpStatusError(
        status_code=response.status_code,
        final_url=response.final_url,
        retry_after_seconds=_retry_after_seconds(response.headers),
    )


def _request_attempt_error(
    exc: Exception,
    retry_safety: RetrySafety,
) -> RequestAttemptError | None:
    if retry_safety != RetrySafety.SAFE:
        return None
    if isinstance(exc, TimeoutError):
        return RequestAttemptError(
            failure_kind=RequestFailureKind.TIMEOUT,
            retry_safety=retry_safety,
            message=str(exc) or "request timed out",
        )
    if isinstance(exc, OSError):
        return RequestAttemptError(
            failure_kind=RequestFailureKind.NETWORK,
            retry_safety=retry_safety,
            message=str(exc) or "network request failed",
        )
    if isinstance(exc, HttpStatusError) and is_retryable_http_status(exc.status_code):
        return RequestAttemptError(
            failure_kind=RequestFailureKind.HTTP_STATUS,
            retry_safety=retry_safety,
            message=str(exc),
            status_code=exc.status_code,
            retry_after_seconds=exc.retry_after_seconds,
        )
    return None


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    for key, value in headers.items():
        if str(key).casefold() != "retry-after":
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None
