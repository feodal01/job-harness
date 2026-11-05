"""Shared step for every source: keep only work formats of the brief's vocabulary.

Source codes (HH `ON_SITE`, ATS `onsite`…) are translated by each source's own step; this one drops
anything else. "remote" is the one stored legacy value: the old name of remote_country, as in
VacancyPlace.legacy_remote.
"""

from src.enrichment.contract import FactUpdate
from src.models.facts import EnrichMethod
from src.models.filters import WORK_FORMATS
from src.models.vacancy import Vacancy


class CanonicalFormats:
    id = "formats.canonical"
    version = "2"
    method: EnrichMethod = "structured"
    facts = frozenset({"work_formats"})

    def enrich(self, vacancy: Vacancy) -> list[FactUpdate]:
        named = ("remote_country" if f == "remote" else f for f in vacancy.work_formats)
        formats = list(dict.fromkeys(f for f in named if f in WORK_FORMATS))
        return [FactUpdate("work_formats", formats, field="work_formats")]
