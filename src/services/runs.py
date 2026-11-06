from src.db.protocols import EventStore, RunStore, VacancyStore
from src.models.run import RunInput
from src.services.briefs import BriefService
from src.services.interfaces import SearchExecutor
from src.services.sources import SourceService


class RunService:
    def __init__(
        self,
        runs: RunStore,
        vacancies: VacancyStore,
        events: EventStore,
        engine: SearchExecutor,
        briefs: BriefService,
        sources: SourceService | None = None,
    ) -> None:
        self.runs, self.vacancies, self.event_store = runs, vacancies, events
        self.engine = engine
        self.briefs = briefs
        self.sources = sources

    def list_runs(self, brief_id: str | None = None) -> list[dict]:
        return self.runs.runs(brief_id)

    async def start(self, request: RunInput) -> dict:
        """Start a run; company boards are read from the catalog now, with the user's session.

        Without a session the boards are left out with the reason and the other sources still run.
        """
        self.briefs.validate_saved(request.brief_id)
        if self.sources is None or not request.sources:
            return self.engine.start(request)
        resolved = await self.sources.resolve_ids(request.sources)
        boards = {
            key: source for key, source in resolved.sources.items() if key not in self.sources.registry.fixed
        }
        return self.engine.start(request, boards, resolved.skipped)

    def get(self, run_id: str) -> dict:
        return self.runs.run(run_id)

    def cancel(self, run_id: str) -> dict:
        self.engine.cancel(run_id)
        return self.runs.run(run_id)

    def results(self, run_id: str, page: int = 1, page_size: int = 50, selection: str = "all") -> dict:
        return self.vacancies.results_page(run_id, page, page_size, selection)

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return self.event_store.events(run_id, after)
