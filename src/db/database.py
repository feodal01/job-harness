"""Application database: resource lifetime and repository composition."""

from contextlib import AbstractContextManager
from pathlib import Path

from .briefs import BriefRepository
from .companies import CompanyRepository
from .connection import Connection
from .events import EventRepository
from .resources import ResourceRepository
from .runs import RunRepository
from .vacancies import VacancyRepository


class Database:
    def __init__(self, path: Path) -> None:
        self._connection = Connection(path)
        try:
            self.briefs = BriefRepository(self._connection)
            self.runs = RunRepository(self._connection, self.briefs)
            self.vacancies = VacancyRepository(self._connection, self.briefs, self.runs)
            self.companies = CompanyRepository(self._connection)
            self.events = EventRepository(self._connection, self.runs)
            self.resources = ResourceRepository(self._connection)
        except BaseException:
            self._connection.close()
            raise

    def transaction(self) -> AbstractContextManager[None]:
        return self._connection.transaction()

    def close(self) -> None:
        self._connection.close()

    def stats(self) -> dict[str, int]:
        return {
            name: self._connection.db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("briefs", "runs", "vacancies", "events")
        }
