"""What both ways of reading vacancy places share: the outcome type, the reading protocol, labels."""

from dataclasses import dataclass
from typing import Protocol

from src.models.filters import SearchPlace, VacancyPlace
from src.models.vacancy import Vacancy


@dataclass(frozen=True)
class Match:
    """True: the place fits the brief place; False: it does not; None: unknown. `basis` explains why."""

    value: bool | None
    basis: str


NO_EXCLUSION = Match(False, "")


class Geography(Protocol):
    """How a vacancy's places are read: by facts (geography.py) or by labels (legacy.py)."""

    def alternatives(self, vacancy: Vacancy) -> list[tuple[str | None, VacancyPlace]]:
        """Options of the vacancy: (work format or None if unknown, place)."""
        ...

    def decide(
        self, wanted: SearchPlace, place: VacancyPlace, mode: str | None, exclusions: list[VacancyPlace]
    ) -> Match:
        """The place against the brief place, with the vacancy's explicit exclusions."""
        ...


def whole_country(wanted: SearchPlace) -> bool:
    return wanted.scope == "country" or (wanted.geoname_id is None and not wanted.city)


def label(place: VacancyPlace) -> str:
    """How a place is named in reasons: the source's label, otherwise its identifiers."""
    if text := place.label or place.city:
        return f"«{text}»"
    if place.scope == "world" or place.worldwide:
        return "весь мир"
    parts = [
        f"GeoNames {place.geoname_id}" if place.geoname_id is not None else None,
        place.region,
        f"группа CLDR {place.group}" if place.group else None,
        place.country,
    ]
    return ", ".join(p for p in parts if p) or "место не указано"
