"""HH vacancy facts: one place per HH work format at the vacancy's `area` (docs/vacancy-facts-plan.md §12).

Steps (StagedEnricher): geography.py (HH area directory → country, region, GeoNames city), formats.py
(HH work format codes), relocation — joined in `_combine`. text.py adds what HH has no structure for:
worldwide remote work, excluded countries and relocation from the card text; places it shapes are
marked `method=text` in their evidence.
"""

from dataclasses import dataclass, replace
from typing import Any

from src.enrichment.contract import FactUpdate
from src.enrichment.staged import PlaceGeography, RawPlace, StagedEnricher
from src.geography.catalog import GeographyCatalog
from src.models.filters import REMOTE_FORMATS, VacancyPlace, WorkFormat
from src.models.vacancy import Vacancy
from src.parsers.aggregators.hh.enrichment import text
from src.parsers.aggregators.hh.enrichment.areas import HHAreas, default_areas
from src.parsers.aggregators.hh.enrichment.formats import place_format
from src.parsers.aggregators.hh.enrichment.geography import area_id, place_geography


@dataclass(frozen=True)
class Record:
    """One raw HH place: the area with one work format, or a country excluded by the text."""

    area: dict[str, Any]
    code: str | None = None
    worldwide: bool = False  # Remote work worldwide, from the text.
    relocation: bool | None = None  # From the card text.
    excluded_country: str | None = None


class HHEnricher(StagedEnricher):
    id = "hh_ru"
    version = "4"
    _places_field = "area"

    def __init__(self, catalog: GeographyCatalog, areas: HHAreas | None = None) -> None:
        self.catalog = catalog
        self.areas = areas or default_areas()

    def enrich(self, vacancy: Vacancy) -> list[FactUpdate]:
        updates = super().enrich(vacancy)
        if not any(u.name == "work_formats" for u in updates):
            # No area (some cards): the formats are known even without a place.
            formats = [f for f in (place_format(c) for c in _codes(vacancy)) if f]
            if formats:
                updates.append(FactUpdate("work_formats", list(dict.fromkeys(formats)), field="workFormats"))
        return updates

    def _raw_places(self, vacancy: Vacancy) -> list[RawPlace]:
        raw_area = vacancy.source_data.get("area")
        area = raw_area if isinstance(raw_area, dict) else None
        codes = _codes(vacancy)
        wording = "\n".join(t for t in (vacancy.summary, vacancy.description) if t)
        # A worldwide phrase alone may describe the company's business: remote work must be stated too.
        remote = any(place_format(c) in REMOTE_FORMATS for c in codes)
        worldwide = text.worldwide(wording) and (remote or text.REMOTE_WORDING.search(wording) is not None)
        if worldwide and not remote:
            codes.append("REMOTE")
        # Relocation is promised by the full card text only; a summary proves nothing.
        relocation = text.relocation(vacancy.description) if vacancy.description else None
        records = []
        if area is not None:
            # A code with no brief counterpart (FIELD_WORK) adds no option next to understood ones: its
            # unknown format would read as «maybe remote anywhere» at a place HH puts in one city.
            understood = [c for c in codes if place_format(c) is not None]
            options: list[str | None] = [*understood] or [None]
            records = [
                RawPlace(
                    Record(area, code, worldwide and place_format(code) in REMOTE_FORMATS, relocation),
                    "area",
                    raw=area_id(area),
                )
                for code in options
            ]
        records += [
            RawPlace(Record({}, excluded_country=code), "description", raw=code, excluded=True)
            for code in sorted(text.excluded_countries(wording, self.catalog))
        ]
        return records

    def _place_geography(self, raw: RawPlace) -> PlaceGeography:
        record: Record = raw.data
        if record.excluded_country:
            return PlaceGeography("country", country=record.excluded_country)
        return place_geography(record.area, self.areas, self.catalog)

    def _place_format(self, raw: RawPlace) -> WorkFormat | None:
        record: Record = raw.data
        if record.excluded_country or record.worldwide:
            return "remote_worldwide"
        return place_format(record.code)

    def _place_relocation(self, raw: RawPlace) -> bool | None:
        record: Record = raw.data
        return record.relocation

    def _combine(
        self,
        raw: RawPlace,
        geography: PlaceGeography,
        work_format: WorkFormat | None,
        relocation: bool | None,
    ) -> VacancyPlace:
        record: Record = raw.data
        if work_format == "remote_worldwide" and not raw.excluded:
            geography = replace(geography, worldwide=True)
        place = super()._combine(raw, geography, work_format, relocation)
        from_text = record.excluded_country or record.worldwide or record.relocation is not None
        if from_text and place.evidence is not None:
            field = "description" if record.excluded_country else "area, description"
            place.evidence = place.evidence.model_copy(update={"method": "text", "field": field})
        return place


def _codes(vacancy: Vacancy) -> list[str]:
    """HH work format codes; stored vacancies parsed before stage 5 keep them only in `work_formats`."""
    stored = vacancy.source_data.get("workFormats")
    codes = stored if isinstance(stored, list) else vacancy.work_formats
    return list(dict.fromkeys(c for c in codes if isinstance(c, str)))
