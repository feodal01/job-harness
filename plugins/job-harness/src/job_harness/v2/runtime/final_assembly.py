"""Global final barrier and immutable result assembly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.runtime.public_projection import public_vacancy_projection
from job_harness.v2.serialization import JsonObject


class ExecutionNotDrainedError(RuntimeError):
    """Raised when final assembly is attempted before the graph drains."""


@dataclass(frozen=True)
class FinalAssembly:
    execution_id: str
    items: tuple[JsonObject, ...]


class FinalAssembler:
    def __init__(
        self,
        repository: SqliteGraphRepository,
        *,
        scorer: Callable[[JsonObject], float] | None = None,
    ) -> None:
        self._repository = repository
        self._scorer = scorer or (lambda _facts: 0.0)

    def assemble(self, execution_id: str, *, now: float) -> FinalAssembly:
        try:
            items = self._repository.assemble_final(
                execution_id,
                projector=public_vacancy_projection,
                scorer=self._scorer,
                now=now,
            )
        except RuntimeError as exc:
            if str(exc).startswith("execution is not drained:"):
                raise ExecutionNotDrainedError(str(exc)) from exc
            raise
        return FinalAssembly(execution_id=execution_id, items=items)
