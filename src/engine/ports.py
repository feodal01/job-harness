"""Storage contracts needed by search orchestration."""

from typing import Protocol

from src.db.protocols import BriefReader, CompanyStore, EventStore, ResourceStore, RunStore, VacancyStore


class RecordingStorage(Protocol):
    @property
    def briefs(self) -> BriefReader: ...

    @property
    def runs(self) -> RunStore: ...

    @property
    def vacancies(self) -> VacancyStore: ...

    @property
    def events(self) -> EventStore: ...


class SearchStorage(RecordingStorage, Protocol):
    @property
    def companies(self) -> CompanyStore: ...

    @property
    def resources(self) -> ResourceStore: ...
