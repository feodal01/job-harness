"""Brief pool decisions are recomputed locally, without requests to sources (spec 9)."""

from src.db.protocols import VacancyStore
from src.engine.filter_stage import FilterStage
from src.enrichment.registry import Enrichment
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.parsers.interfaces import SourceAdapter


def reevaluate_pool(
    store: VacancyStore,
    brief_id: str,
    filters: SearchFilters,
    queries: tuple[str, ...],
    sources: dict[str, SourceAdapter],
    enrichment: Enrichment,
) -> int:
    stages = {
        key: FilterStage(
            filters,
            queries,
            frozenset(k for k, mode in source.search.parameters.items() if mode == "none"),
            catalog=enrichment.catalog,
        )
        for key, source in sources.items()
    }
    rows = store.pool(brief_id)
    for row in rows:
        vacancy = Vacancy.model_validate({k: v for k, v in row.items() if k in Vacancy.model_fields})
        vacancy = enrichment.enrich(vacancy, row["source"])
        # Historical sources may be disabled; their stored facts remain usable locally.
        stage = stages.get(row["source"]) or FilterStage(filters, queries, catalog=enrichment.catalog)
        store.set_pool_selection(brief_id, row["key"], stage.evaluate(vacancy).model_dump(mode="json"))
    return len(rows)
