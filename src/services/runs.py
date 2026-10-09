from src.db.protocols import EventStore, RunStore, VacancyStore
from src.models.run import RunInput
from src.services.interfaces import SearchExecutor


class RunService:
    def __init__(
        self, runs: RunStore, vacancies: VacancyStore, events: EventStore, engine: SearchExecutor
    ) -> None:
        self.runs, self.vacancies, self.event_store = runs, vacancies, events
        self.engine = engine

    def list_runs(self, brief_id: str | None = None) -> list[dict]:
        return self.runs.runs(brief_id)

    def start(self, request: RunInput) -> dict:
        return self.engine.start(request)

    def get(self, run_id: str) -> dict:
        return self.runs.run(run_id)

    def cancel(self, run_id: str) -> dict:
        self.engine.cancel(run_id)
        return self.runs.run(run_id)

    def results(self, run_id: str, page: int = 1, page_size: int = 50, selection: str = "all") -> dict:
        return self.vacancies.results_page(run_id, page, page_size, selection)

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return self.event_store.events(run_id, after)
