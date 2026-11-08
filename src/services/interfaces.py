"""Application operations required by the API-facing services."""

from collections.abc import Mapping
from typing import Any, Protocol

from src.models.run import RunInput
from src.parsers.interfaces import SourceAdapter


class SearchExecutor(Protocol):
    def start(
        self,
        request: RunInput,
        sources: Mapping[str, SourceAdapter] | None = None,
        unavailable: Mapping[str, str] | None = None,
        snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def cancel(self, run_id: str) -> None: ...
