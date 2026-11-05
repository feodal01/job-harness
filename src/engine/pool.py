"""Brief pool decisions are recomputed locally, without requests to sources (spec 9)."""

from src.db.protocols import VacancyStore
from src.engine.filter_stage import FilterStage
from src.enrichment.registry import Enrichment
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.parsers.interfaces import SourceAdapter
from src.selection.salary.settings import SalaryRange


def reevaluate_pool(
    store: VacancyStore,
    brief_id: str,
    filters: SearchFilters,
    queries: tuple[str, ...],
    sources: dict[str, SourceAdapter],
    enrichment: Enrichment,
    salary_range: SalaryRange,
) -> int:
    stages = {
        key: FilterStage(
            filters,
            queries,
            frozenset(k for k, mode in source.search.parameters.items() if mode == "none"),
            catalog=enrichment.catalog,
            salary_range=salary_range,
        )
        for key, source in sources.items()
    }
    rows = store.pool(brief_id)
    for row in rows:
        vacancy = Vacancy.from_stored(row)
        vacancy = enrichment.enrich(vacancy, row["source"])
        # Historical sources may be disabled; their stored facts remain usable locally.
        stage = stages.get(row["source"]) or FilterStage(
            filters, queries, catalog=enrichment.catalog, salary_range=salary_range
        )
        store.set_pool_selection(brief_id, row["key"], stage.evaluate(vacancy).model_dump(mode="json"))
    return len(rows)
