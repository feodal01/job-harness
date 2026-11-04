"""Shared step for every source: work formats in the common vocabulary.

Moved unchanged from normalize_stored_vacancy (stage 2); FORMATS goes away with the old fields.
"""

from src.enrichment.contract import FactUpdate
from src.models.facts import EnrichMethod
from src.models.vacancy import Vacancy
from src.parsers.normalization import FORMATS


class CanonicalFormats:
    id = "formats.canonical"
    version = "1"
    method: EnrichMethod = "structured"
    facts = frozenset({"work_formats"})

    def enrich(self, vacancy: Vacancy) -> list[FactUpdate]:
        formats = list(dict.fromkeys(FORMATS[f] for f in vacancy.work_formats if f in FORMATS))
        return [FactUpdate("work_formats", formats, field="work_formats")]
