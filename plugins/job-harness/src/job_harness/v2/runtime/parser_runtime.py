"""Invocation-scoped network runtime exposed to scraper bundles."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import urljoin, urlsplit

from job_harness.v2.ports import HttpAction, HttpResponse, HttpTransport, OperationContext
from job_harness.v2.runtime.resource_gate import ResourceGate, ResourcePolicy

type HostResolver = Callable[[str], tuple[str, ...]]
type ResourcePolicyResolver = Callable[[str], ResourcePolicy]

_HTTP_SEE_OTHER = 303
_MAX_REDIRECTS = 10


class UnsafeTargetError(ValueError):
    """Raised before a scraper can access a disallowed network target."""


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

    @property
    def reserved_collection_units(self) -> int:
        return self._reserved_collection_units

    async def http(self, action: HttpAction) -> HttpResponse:
        current = action
        for redirect_count in range(_MAX_REDIRECTS + 1):
            response = await self._send_once(current)
            location = response.headers.get("location")
            if response.status_code not in {301, 302, 303, 307, 308} or not location:
                return response
            if redirect_count == _MAX_REDIRECTS:
                raise ValueError("HTTP redirect limit exceeded")
            redirected_url = urljoin(response.final_url, location)
            self._validate_target(redirected_url)
            method = "GET" if response.status_code == _HTTP_SEE_OTHER else current.method
            body = None if response.status_code == _HTTP_SEE_OTHER else current.body
            current = replace(current, method=method, url=redirected_url, body=body, resource_key=None)
        raise RuntimeError("unreachable redirect loop")

    async def _send_once(self, action: HttpAction) -> HttpResponse:
        resource_key = self._validate_target(action.url)
        if action.resource_key is not None:
            if not action.resource_key.strip():
                raise ValueError("resource_key must be non-empty")
            resource_key = action.resource_key
        policy = self._policy_for_resource(resource_key)
        permit = await self._resource_gate.admit(resource_key, policy, self._context)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._transport.send(action, timeout_seconds=self._timeout_seconds)
            self._validate_target(response.final_url)
            if len(response.body) > self._max_response_bytes:
                raise ValueError("response body exceeds configured maximum")
            return response
        finally:
            self._resource_gate.release(permit)

    def _validate_target(self, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeTargetError("target must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeTargetError("target URL must not contain credentials")
        host = parsed.hostname.casefold()
        addresses = (host,) if _is_ip_literal(host) else self._host_resolver(host)
        if not addresses:
            raise UnsafeTargetError("target host did not resolve")
        for address in addresses:
            if not _is_public_address(address):
                raise UnsafeTargetError(f"target resolves to a disallowed address: {address}")
        return host


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
    ) -> None:
        self._transport = transport
        self._resource_gate = resource_gate
        self._policy_for_resource = policy_for_resource
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._host_resolver = host_resolver

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
        )

def _resolve_host(host: str) -> tuple[str, ...]:
    try:
        return tuple(sorted({str(item[4][0]) for item in socket.getaddrinfo(host, None)}))
    except socket.gaierror as exc:
        raise UnsafeTargetError(f"target host resolution failed: {host}") from exc


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
