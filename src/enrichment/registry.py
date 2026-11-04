"""Fact types and the enrichers of each source.

The enricher registry does not depend on enabled sources: stored vacancies of a disabled
source are enriched the same way. ATS ids `ats:<platform>:<board>` resolve by platform.
"""

from functools import cache
from typing import Any

from src.enrichment.contract import Enricher
from src.enrichment.formats import CanonicalFormats
from src.geography.catalog import GeographyCatalog, default_catalog
from src.models.vacancy import Vacancy, fact_fields

# Vacancy fields marked FACT in the model; the model is the only list.
VACANCY_FACTS = fact_fields()
# Additional facts declared by enrichers (e.g. a text tool's english_level) are registered here
# with their value type; they are stored in Vacancy.facts and checked by this type.
EXTRA_FACTS: dict[str, Any] = {}
FACT_TYPES: dict[str, Any] = {
    **{name: Vacancy.model_fields[name].annotation for name in VACANCY_FACTS},
    **EXTRA_FACTS,
}
# Run for every source before its own enricher.
SHARED: tuple[Enricher, ...] = (CanonicalFormats(),)


def source_key(source: str) -> str:
    """`ats:<platform>:<board>` → `ats:<platform>`; other ids are used as is."""
    parts = source.split(":")
    return ":".join(parts[:2]) if parts[0] == "ats" else source


class Enrichment:
    """The enrichers of every source, built once with the application's geography catalog."""

    def __init__(self, catalog: GeographyCatalog) -> None:
        # Imported here: source modules import the enrichment contract.
        from src.parsers.aggregators.getmatch.enrichment import GetmatchEnricher
        from src.parsers.aggregators.hh.enrichment import HHEnricher

        self.catalog = catalog
        self.sources: dict[str, Enricher] = {
            e.id: e for e in (GetmatchEnricher(catalog), HHEnricher(catalog))
        }

    def enrichers_for(self, source: str) -> tuple[Enricher, ...]:
        own = self.sources.get(source_key(source))
        return (*SHARED, *((own,) if own else ()))

    def enrich(self, vacancy: Vacancy, source: str) -> Vacancy:
        """An enriched copy; facts and evidence are recomputed from scratch each time."""
        from src.enrichment.chain import run

        return run(vacancy, self.enrichers_for(source))


@cache
def default_enrichment() -> Enrichment:
    """Enrichment over the process-wide catalog; the application passes it explicitly."""
    return Enrichment(default_catalog())
