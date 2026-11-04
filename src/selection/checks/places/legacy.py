"""TRANSITIONAL: places without facts (no `scope`) of sources not yet translated — HH and ATS.

Moved unchanged from the pre-stage-4 filter: the city is compared by its label, a place without
a format takes the vacancy's `work_formats`. Removed at stage N+1 (docs/vacancy-facts-plan.md §5).
"""

from src.geography.catalog import GeographyCatalog
from src.models.filters import OFFICE_FORMATS, SearchPlace, VacancyPlace
from src.models.vacancy import Vacancy
from src.selection.checks.places.format import FORMAT_RANK
from src.selection.checks.places.model import NO_EXCLUSION, Match, label


class LegacyGeography:
    def __init__(self, catalog: GeographyCatalog) -> None:
        self.catalog = catalog

    def _city(self, wanted: SearchPlace, actual: str) -> bool | None:
        # Only cities confirmed in the brief editor (GeoNames ID) can be compared.
        if wanted.geoname_id is None:
            return None
        return self.catalog.matches(wanted.geoname_id, actual)

    def alternatives(self, vacancy: Vacancy) -> list[tuple[str | None, VacancyPlace]]:
        places = [p for p in vacancy.locations if not p.excluded]
        modes = {"remote_country" if m == "remote" else m for m in vacancy.work_formats}
        modes &= FORMAT_RANK.keys()
        alternatives: list[tuple[str | None, VacancyPlace]] = []
        for place in places:
            if place.worldwide or place.format == "remote_worldwide":
                alternatives.append(("remote_worldwide", place))
            elif place.format:
                alternatives.append((place.format, place))
            else:
                alternatives.extend((m, place) for m in sorted(modes))
                if not modes:
                    alternatives.append((None, place))
        represented = {mode for mode, _ in alternatives}
        alternatives.extend((m, VacancyPlace()) for m in sorted(modes - represented))
        return alternatives or [(None, VacancyPlace())]

    def decide(
        self, wanted: SearchPlace, place: VacancyPlace, mode: str | None, exclusions: list[VacancyPlace]
    ) -> Match:
        # As before stage 4: a possible exclusion is checked first and wins over the place.
        excluded = self.excluded(wanted, exclusions, mode)
        if excluded.value is not False:
            return Match(False if excluded.value else None, excluded.basis)
        return self.matches(wanted, place, mode)

    def matches(self, wanted: SearchPlace, place: VacancyPlace, mode: str | None) -> Match:
        basis = "подпись города и страна (переходное сравнение)"
        if mode == "remote_worldwide":
            return Match(True, "удалёнка по всему миру")
        if place.country is None:
            return Match(None, "страна места неизвестна")
        if mode is None:
            # An unknown format might allow worldwide or country-wide remote work.
            if place.country != wanted.country:
                return Match(None, basis)
            if not wanted.city or (place.city and self._city(wanted, place.city)):
                return Match(True, basis)
            return Match(None, basis)
        if place.country != wanted.country:
            return Match(False, basis)
        if mode == "remote_country" or not wanted.city:
            return Match(True, basis)
        return Match(self._city(wanted, place.city) if place.city else None, basis)

    def excluded(self, wanted: SearchPlace, exclusions: list[VacancyPlace], mode: str | None) -> Match:
        possible: VacancyPlace | None = None
        for place in exclusions:
            if mode in OFFICE_FORMATS and place.format not in {None, mode}:
                continue
            if place.country != wanted.country:
                continue
            if not place.city:
                return Match(True, f"явно исключено место {label(place)}")
            if not wanted.city:
                possible = place
            else:
                match = self._city(wanted, place.city)
                if match is True:
                    return Match(True, f"явно исключено место {label(place)}")
                if match is None:
                    possible = place
        if possible is not None:
            return Match(None, f"возможно исключено место {label(possible)}")
        return NO_EXCLUSION
