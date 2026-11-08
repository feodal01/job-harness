from src.db.protocols import EventStore, RunStore, VacancyStore
from src.models.run import RunInput
from src.models.sources import SourceRules
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
        """Start a run over the sources its rules give now: the request's, else the brief's.

        Company boards are read from the catalog with the user's session; without one they are
        left out with the reason and the other sources still run.
        """
        brief = self.briefs.validate_saved(request.brief_id)
        if self.sources is None:
            return self.engine.start(request)
        rules = request.source_rules or (brief.search.source_rules if brief.search else None) or SourceRules()
        resolved = await self.sources.resolve_rules(rules, (p.country for p in brief.filters.locations))
        snapshot = {
            "source_rules": rules.model_dump(mode="json"),
            "resolution": {
                "countries": resolved.countries,
                "catalog": resolved.catalog.model_dump(exclude={"login_url"}, exclude_none=True),
                "matched": len(resolved.sources) + len(resolved.excluded),
                "excluded": resolved.excluded,
            },
        }
        return self.engine.start(request, resolved.sources, resolved.skipped, snapshot)

    def get(self, run_id: str) -> dict:
        return self.runs.run(run_id)

    def cancel(self, run_id: str) -> dict:
        self.engine.cancel(run_id)
        return self.runs.run(run_id)

    def results(self, run_id: str, page: int = 1, page_size: int = 50, selection: str = "all") -> dict:
        return self.vacancies.results_page(run_id, page, page_size, selection)

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return self.event_store.events(run_id, after)
