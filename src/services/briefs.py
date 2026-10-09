from collections.abc import Callable
from contextlib import AbstractContextManager

from src.db.protocols import BriefStore, VacancyStore
from src.engine.pool import reevaluate_pool
from src.models.brief import BriefInput
from src.parsers.interfaces import SourceAdapter


class BriefService:
    def __init__(
        self,
        briefs: BriefStore,
        vacancies: VacancyStore,
        transaction: Callable[[], AbstractContextManager[None]],
        sources: dict[str, SourceAdapter],
    ) -> None:
        self.briefs, self.vacancies, self.transaction = briefs, vacancies, transaction
        self.sources = sources

    def list_briefs(self) -> list[dict]:
        return self.briefs.brief_overviews()

    def create(self, brief: BriefInput) -> dict:
        return self.briefs.save_brief(brief)

    def update(self, brief_id: str, brief: BriefInput) -> dict:
        with self.transaction():
            saved = self.briefs.save_brief(brief, brief_id)
            reevaluate_pool(self.vacancies, brief_id, brief.filters, tuple(brief.queries), self.sources)
        return saved
