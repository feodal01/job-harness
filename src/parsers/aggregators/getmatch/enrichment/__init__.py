"""Getmatch vacancy facts from its structured `location_requirements` (docs/vacancy-facts-plan.md §9).

Each record becomes one place; geography, format and relocation are separate steps
(geography.py, formats.py, relocation.py) joined only in `_combine`. Getmatch stores one pair
"place + one option" per record, so a relocation record of a place applies to the format records
of the same `location_id` in the same vacancy (`_join`, §11).
"""

from collections import defaultdict

from src.enrichment.contract import FactUpdate
from src.enrichment.staged import PlaceGeography, RawPlace, StagedEnricher
from src.geography.catalog import GeographyCatalog
from src.models.filters import VacancyPlace, WorkFormat
from src.models.vacancy import Vacancy
from src.parsers.aggregators.getmatch.enrichment.formats import FORMATS, place_format
from src.parsers.aggregators.getmatch.enrichment.geography import place_geography
from src.parsers.aggregators.getmatch.enrichment.relocation import RELOCATION, place_relocation
from src.parsers.aggregators.getmatch.enrichment.salary import salary_records

KNOWN_CODES = frozenset(FORMATS) | frozenset(RELOCATION)


class GetmatchEnricher(StagedEnricher):
    id = "getmatch"
    version = "4"
    facts = frozenset({"locations", "work_formats", "salary"})
    _places_field = "location_requirements"

    def __init__(self, catalog: GeographyCatalog) -> None:
        self.catalog = catalog

    def enrich(self, vacancy: Vacancy) -> list[FactUpdate]:
        updates = super().enrich(vacancy)
        records = salary_records(vacancy.source_data, self.id, self.version)
        if records is not None:
            updates.append(FactUpdate("salary", records, field="salary_*"))
        return updates

    def _raw_places(self, vacancy: Vacancy) -> list[RawPlace]:
        records = vacancy.source_data.get("location_requirements")
        if not isinstance(records, list):
            return []
        return [
            RawPlace(
                record,
                f"location_requirements[{i}]",
                raw=record.get("location_id"),
                excluded=bool(record.get("exclude")),
            )
            for i, record in enumerate(records)
            # A code outside Getmatch's known vocabulary is not understood: skip, never guess. So is an
            # excluded relocation record: "no relocation there" is not "no work there".
            if isinstance(record, dict)
            and (
                record.get("format") in FORMATS
                or (record.get("format") in RELOCATION and not record.get("exclude"))
            )
        ]

    def _place_geography(self, raw: RawPlace) -> PlaceGeography:
        return place_geography(raw.data, self.catalog)

    def _place_format(self, raw: RawPlace) -> WorkFormat | None:
        return place_format(raw.data)

    def _place_relocation(self, raw: RawPlace) -> bool | None:
        return place_relocation(raw.data)

    def _combine(
        self,
        raw: RawPlace,
        geography: PlaceGeography,
        work_format: WorkFormat | None,
        relocation: bool | None,
    ) -> VacancyPlace:
        # Getmatch's only coupling: remote work for the whole world is remote_worldwide.
        if work_format == "remote_country" and geography.scope == "world":
            work_format = "remote_worldwide"
        return super()._combine(raw, geography, work_format, relocation)

    def _join(self, records: list[tuple[RawPlace, VacancyPlace]]) -> list[VacancyPlace]:
        groups: defaultdict[tuple[object, ...], list[tuple[RawPlace, VacancyPlace]]] = defaultdict(list)
        for raw, place in records:
            groups[_place_key(raw)].append((raw, place))
        joined: list[VacancyPlace] = []
        for key, members in groups.items():
            relocation = [(raw, p) for raw, p in members if _is_relocation(p)]
            formats = [(raw, p) for raw, p in members if not _is_relocation(p)]
            if key[0] is None or not relocation or not formats:
                joined.extend(p for _, p in members)
                continue
            values = {p.relocation_support for _, p in relocation}
            # Contradicting relocation records of one place stay unknown, never guessed.
            value = values.pop() if len(values) == 1 else None
            for raw, place in formats:
                place.relocation_support = value
                if place.evidence is not None:
                    fields = ", ".join([raw.field, *(r.field for r, _ in relocation)])
                    place.evidence = place.evidence.model_copy(update={"field": fields})
                joined.append(place)
        # Keep the source's order of places.
        order = {id(place): i for i, (_, place) in enumerate(records)}
        return sorted(joined, key=lambda place: order[id(place)])

    def _without_places(self, vacancy: Vacancy) -> list[VacancyPlace]:
        # Stored vacancies without source data keep their places; worldwide remote stays worldwide.
        places = [p.model_copy() for p in vacancy.locations]
        for place in places:
            if place.worldwide and place.format == "remote_country":
                place.format = "remote_worldwide"
        return places


def _is_relocation(place: VacancyPlace) -> bool:
    """A relocation record: the format step found no work format, the relocation step found a value."""
    return place.format is None and place.relocation_support is not None


def _place_key(raw: RawPlace) -> tuple[object, ...]:
    """Records of one place: the same location_id and labels (Getmatch marks unidentified places with a
    shared id like `unknown`), and the same inclusion (an exclusion is another statement)."""
    data = raw.data
    return (raw.raw or None, data.get("city") or None, data.get("country") or None, raw.excluded)
