"""Application operations required by the API-facing services."""

from typing import Any, Protocol

from src.models.run import RunInput


class SearchExecutor(Protocol):
    def start(self, request: RunInput) -> dict[str, Any]: ...

    def cancel(self, run_id: str) -> None: ...
