"""Base class for source normalizers: one independent step per kind of data, joined in `_combine`.

A vacancy place is one fact holding geography, work format and relocation together (the mirror
of SearchPlace), so these are steps of one enricher rather than separate chain links. Each step
reads one raw place record of the source and knows nothing about the other steps; `_combine`
is the only place where a source may couple them (docs/vacancy-facts-plan.md §9).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.enrichment.contract import FactUpdate
from src.models.facts import EnrichMethod, Evidence
from src.models.filters import PlaceScope, VacancyPlace, WorkFormat
from src.models.vacancy import Vacancy


@dataclass(frozen=True)
class PlaceGeography:
    """Geography of one raw place in the brief's vocabulary; unknown parts stay None."""

    scope: PlaceScope | None
    country: str | None = None
    geoname_id: int | None = None
    region: str | None = None
    group: str | None = None
    label: str | None = None
    # Transitional string facts read by the current local filter.
    city: str | None = None
    worldwide: bool = False


@dataclass(frozen=True)
class RawPlace:
    """One raw place record and where it is in the source data."""

    data: Any
    field: str
    raw: Any = None
    excluded: bool = False


class StagedEnricher(ABC):
    id: str
    version: str
    method: EnrichMethod = "structured"
    facts = frozenset({"locations", "work_formats"})

    def enrich(self, vacancy: Vacancy) -> list[FactUpdate]:
        records = [
            (
                raw,
                self._combine(
                    raw, self._place_geography(raw), self._place_format(raw), self._place_relocation(raw)
                ),
            )
            for raw in self._raw_places(vacancy)
        ]
        places = self._join(records) or self._without_places(vacancy)
        if not places:
            return []
        updates = [FactUpdate("locations", places, field=self._places_field)]
        declared = [p.format for p in places if p.format and not p.excluded]
        if declared:
            updates.append(
                FactUpdate("work_formats", list(dict.fromkeys(declared)), field=self._places_field)
            )
        return updates

    _places_field: str = ""

    @abstractmethod
    def _raw_places(self, vacancy: Vacancy) -> list[RawPlace]:
        """The source's place records of this vacancy."""

    @abstractmethod
    def _place_geography(self, raw: RawPlace) -> PlaceGeography: ...

    @abstractmethod
    def _place_format(self, raw: RawPlace) -> WorkFormat | None: ...

    @abstractmethod
    def _place_relocation(self, raw: RawPlace) -> bool | None: ...

    def _combine(
        self,
        raw: RawPlace,
        geography: PlaceGeography,
        work_format: WorkFormat | None,
        relocation: bool | None,
    ) -> VacancyPlace:
        """Default: put the independent results together without coupling them."""
        return VacancyPlace(
            scope=geography.scope,
            country=geography.country,
            geoname_id=geography.geoname_id,
            region=geography.region,
            group=geography.group,
            label=geography.label,
            city=geography.city,
            worldwide=geography.worldwide,
            format=work_format,
            relocation_support=relocation,
            excluded=raw.excluded,
            evidence=Evidence(
                enricher=self.id, version=self.version, method=self.method, field=raw.field, raw=raw.raw
            ),
        )

    def _join(self, records: list[tuple[RawPlace, VacancyPlace]]) -> list[VacancyPlace]:
        """Default: one place per raw record. A source whose records split one place overrides it."""
        return [place for _, place in records]

    def _without_places(self, vacancy: Vacancy) -> list[VacancyPlace]:
        """Places when the source has no place records (e.g. a stored vacancy without source data)."""
        return []
