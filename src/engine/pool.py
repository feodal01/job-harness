"""Brief pool decisions are recomputed locally, without requests to sources (spec 9)."""

from collections.abc import Callable

from src.db.protocols import VacancyStore
from src.engine.filter_stage import FilterStage
from src.enrichment.registry import Enrichment
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.selection.salary.settings import SalaryRange


def reevaluate_pool(
    store: VacancyStore,
    brief_id: str,
    filters: SearchFilters,
    queries: tuple[str, ...],
    unsupported: Callable[[str], frozenset[str] | None],
    enrichment: Enrichment,
    salary_range: SalaryRange,
) -> int:
    stages: dict[str, FilterStage] = {}

    def stage_for(source: str) -> FilterStage:
        # Historical sources may be gone from the registry; their stored facts remain usable locally.
        if source not in stages:
            stages[source] = FilterStage(
                filters,
                queries,
                unsupported(source) or frozenset(),
                catalog=enrichment.catalog,
                salary_range=salary_range,
            )
        return stages[source]

    rows = store.pool(brief_id)
    for row in rows:
        vacancy = Vacancy.from_stored(row)
        vacancy = enrichment.enrich(vacancy, row["source"])
        stage = stage_for(row["source"])
        store.set_pool_selection(brief_id, row["key"], stage.evaluate(vacancy).model_dump(mode="json"))
    return len(rows)
