"""Ports between v2 layers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from job_harness.v2.contracts import (
    SourceFetchRequest,
    SourceResponseArtifact,
)

_MIN_HTTP_STATUS = 100
_MAX_HTTP_STATUS = 599


class ArtifactFetcher(Protocol):
    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        """Fetch one source response artifact for a source-native request."""


@dataclass(frozen=True)
class OperationContext:
    operation_id: str
    execution_id: str | None
    invocation_id: str | None

    def __post_init__(self) -> None:
        if not self.operation_id.strip():
            raise ValueError("operation_id must be non-empty")


@dataclass(frozen=True)
class HttpAction:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    resource_key: str | None = None

    def __post_init__(self) -> None:
        if self.method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("unsupported HTTP method")
        if not self.url.strip():
            raise ValueError("url must be non-empty")


@dataclass(frozen=True)
class HttpResponse:
    requested_url: str
    final_url: str
    status_code: int
    media_type: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _MIN_HTTP_STATUS <= self.status_code <= _MAX_HTTP_STATUS:
            raise ValueError("status_code must be a valid HTTP status")


class HttpTransport(Protocol):
    async def send(self, action: HttpAction, *, timeout_seconds: float) -> HttpResponse:
        """Execute one already-admitted HTTP action."""


class ParserRuntime(Protocol):
    @property
    def reserved_collection_units(self) -> int:
        """Collection units reserved for this invocation."""

    async def http(self, action: HttpAction) -> HttpResponse:
        """Execute one safe resource-gated HTTP action."""


class ParserRuntimeFactory(Protocol):
    def create(
        self,
        context: OperationContext,
        *,
        reserved_collection_units: int,
    ) -> ParserRuntime:
        """Create one invocation-scoped parser runtime."""
