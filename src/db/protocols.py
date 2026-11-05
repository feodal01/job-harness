"""Narrow repository interfaces used across storage and application services."""

from typing import Any, Protocol

from src.models.brief import BriefInput
from src.models.company import Company
from src.models.vacancy import Vacancy


class BriefReader(Protocol):
    def brief(self, brief_id: str) -> dict[str, Any] | None: ...


class RunReader(Protocol):
    def run(self, run_id: str) -> dict[str, Any]: ...


class BriefStore(BriefReader, Protocol):
    def save_brief(self, brief: BriefInput, brief_id: str | None = None) -> dict[str, Any]: ...

    def delete_brief(self, brief_id: str) -> int: ...

    def briefs(self) -> list[dict[str, Any]]: ...

    def brief_overviews(self) -> list[dict[str, Any]]: ...


class RunStore(RunReader, Protocol):
    def create_run(
        self, brief_id: str, request: dict[str, Any], sources: dict[str, dict[str, Any]]
    ) -> dict[str, Any]: ...

    def runs(self, brief_id: str | None = None) -> list[dict[str, Any]]: ...

    def progress(
        self,
        run_id: str,
        metrics: dict[str, Any],
        *,
        status: str = "running",
        error: str | None = None,
        limit_reached: bool = False,
    ) -> None: ...

    def update_source(self, run_id: str, source: str, state: dict[str, Any]) -> None: ...


class VacancyStore(Protocol):
    def save_vacancy(
        self,
        run_id: str,
        brief_id: str,
        vacancy: Vacancy,
        status: str = "pending",
        error: str | None = None,
        *,
        source: str,
        selection: dict[str, Any] | None = None,
        detail_fetched: bool = False,
    ) -> None: ...

    def add_origin(self, run_id: str, vacancy_key: str, query: str, request_url: str, page: int) -> None: ...

    def known_card(self, vacancy_key: str) -> dict[str, Any] | None: ...

    def results(self, run_id: str) -> list[dict[str, Any]]: ...

    def results_page(
        self, run_id: str, page: int = 1, page_size: int = 50, selection: str = "all"
    ) -> dict[str, Any]: ...

    def pool(self, brief_id: str) -> list[dict[str, Any]]: ...

    def pool_page(
        self,
        brief_id: str,
        page: int = 1,
        page_size: int = 50,
        selection: str = "candidates",
        status: str = "all",
        q: str = "",
        sort: str = "first_seen",
        source: str = "",
    ) -> dict[str, Any]: ...

    def pool_adjacent(
        self,
        brief_id: str,
        vacancy_key: str,
        direction: str = "next",
        page_size: int = 50,
        selection: str = "candidates",
        status: str = "all",
        q: str = "",
        sort: str = "first_seen",
        anchor_time: float | None = None,
        source: str = "",
    ) -> dict[str, Any]: ...

    def pool_item(self, brief_id: str, vacancy_key: str) -> dict[str, Any]: ...

    def set_pool_status(self, brief_id: str, vacancy_key: str, status: str, note: str) -> None: ...

    def set_pool_selection(self, brief_id: str, vacancy_key: str, selection: dict[str, Any]) -> None: ...


class CompanyStore(Protocol):
    def known_company(self, key: str) -> Company | None: ...

    def save_company(self, key: str, company: Company) -> None: ...


class EventStore(Protocol):
    def event(self, run_id: str | None, event: str, **data: Any) -> None: ...

    def events(self, run_id: str | None, after: int = 0) -> list[dict[str, Any]]: ...


class ResourceStore(Protocol):
    def resource(self, resource: str, update: dict[str, Any] | None = None) -> dict[str, Any]: ...
