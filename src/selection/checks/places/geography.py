"""Geography of a vacancy place against a brief place, by identifiers of the one reference.

Vacancy places carry `scope` (set by a source normalizer): the comparison uses only the GeoNames ID,
the GeoNames region, the ISO country and the CLDR group — never a label (§11 table).

| Vacancy place         | Brief: country | Brief: city, remote | Brief: city, office/hybrid      |
|-----------------------|----------------|---------------------|---------------------------------|
| world                 | yes            | yes                 | yes (the format decides)        |
| CLDR group            | member         | member              | member → unknown; else no       |
| group without members | unknown        | unknown             | unknown                         |
| country               | same ISO       | same ISO            | other country no; same unknown  |
| region                | by country     | by country          | same region yes; other unknown  |
| city with GeoNames ID | by country     | by country          | same ID yes; other no           |
| city without an ID    | by country     | by country          | other country no; same unknown  |
| unknown country       | unknown        | unknown             | unknown                         |

Explicit exclusions only narrow a matching place and apply to their own work format (office or hybrid
exactly; any remote to any remote); an exclusion of unknown country leaves the place unknown.
A place of unknown work format may still allow worldwide remote work: another country is unknown,
and so is another city. A region of another name stays unknown: a source may define its region
differently from GeoNames (Getmatch puts Moscow inside «Московская область»).
"""

from src.core.errors import InvalidSearchError
from src.geography.catalog import GeographyCatalog
from src.models.filters import REMOTE_FORMATS, SearchPlace, VacancyPlace
from src.models.vacancy import Vacancy
from src.selection.checks.places.model import NO_EXCLUSION, Match, label, whole_country


def has_facts(vacancy: Vacancy) -> bool:
    return any(p.scope is not None for p in vacancy.locations)


def _applies(excluded: str | None, mode: str | None) -> bool:
    """An exclusion of one work format does not forbid another: office ≠ remote; any remote is remote."""
    if excluded is None or mode is None or excluded == mode:
        return True
    return excluded in REMOTE_FORMATS and mode in REMOTE_FORMATS


class FactGeography:
    """Two questions, each answered once and shared by inclusion and exclusion:

    `_country` — is the brief's country within the place (world, CLDR group, ISO country)?
    `_locality` — inside that country, is the brief city the place (GeoNames ID) or in it (region)?
    """

    def __init__(self, catalog: GeographyCatalog) -> None:
        self.catalog = catalog

    def alternatives(self, vacancy: Vacancy) -> list[tuple[str | None, VacancyPlace]]:
        # Each place is one option with its own format; nothing is borrowed from other places.
        places = [p for p in vacancy.locations if not p.excluded]
        return [(p.format, p) for p in places] or [(None, VacancyPlace())]

    def decide(
        self, wanted: SearchPlace, place: VacancyPlace, mode: str | None, exclusions: list[VacancyPlace]
    ) -> Match:
        # An exclusion only narrows: a place that does not match stays "no" whatever is excluded.
        match = self._matches(wanted, place, mode)
        if match.value is False:
            return match
        excluded = self._excluded(wanted, exclusions, mode)
        if excluded.value is None:
            return Match(None, excluded.basis)
        return Match(False, excluded.basis) if excluded.value else match

    def _matches(self, wanted: SearchPlace, place: VacancyPlace, mode: str | None) -> Match:
        if mode == "remote_worldwide":
            return Match(True, "удалёнка по всему миру")
        country = self._country(place, wanted)
        if country.value is not True:
            # A place of unknown work format may still allow worldwide remote work.
            return (
                country
                if mode or country.value is None
                else Match(None, country.basis + ", формат неизвестен")
            )
        if place.scope == "world" or whole_country(wanted) or mode in REMOTE_FORMATS:
            return country
        if place.scope == "group":
            return Match(None, f"город внутри группы CLDR {place.group} не указан")
        locality = self._locality(place, wanted)
        if locality.value is False and not mode:
            return Match(None, locality.basis + ", формат неизвестен")
        return locality

    def _excluded(self, wanted: SearchPlace, exclusions: list[VacancyPlace], mode: str | None) -> Match:
        possible: VacancyPlace | None = None
        for place in exclusions:
            if not _applies(place.format, mode):
                continue
            covered = self._covers(place, wanted)
            if covered is True:
                return Match(True, f"явно исключено место {label(place)}")
            if covered is None:
                possible = place
        if possible is not None:
            return Match(None, f"возможно исключено место {label(possible)}")
        return NO_EXCLUSION

    def _covers(self, place: VacancyPlace, wanted: SearchPlace) -> bool | None:
        """Is the whole brief place inside an excluded vacancy place?"""
        country = self._country(place, wanted).value
        if country is not True or place.scope not in {"city", "region"}:
            return country
        return None if whole_country(wanted) else self._locality(place, wanted).value

    def _country(self, place: VacancyPlace, wanted: SearchPlace) -> Match:
        if place.scope == "world":
            return Match(True, "весь мир")
        if place.scope == "group":
            members = self.catalog.group_countries(place.group) if place.group else frozenset()
            if not members:
                return Match(None, "группа стран без известного состава")
            inside = wanted.country in members
            return Match(inside, f"страна {'входит' if inside else 'не входит'} в группу CLDR {place.group}")
        if place.country is None:
            return Match(None, "страна места неизвестна")
        if place.country != wanted.country:
            return Match(False, f"другая страна ({place.country})")
        return Match(True, f"страна {place.country}")

    def _locality(self, place: VacancyPlace, wanted: SearchPlace) -> Match:
        if wanted.geoname_id is None:
            return Match(None, "город брифа без GeoNames ID")
        if place.scope == "city" and place.geoname_id is not None:
            same = place.geoname_id == wanted.geoname_id
            return Match(same, "совпадает GeoNames ID города" if same else "другой город (GeoNames ID)")
        if place.scope == "region" and place.region:
            if place.region == self._region(wanted.geoname_id):
                return Match(True, f"город брифа в регионе GeoNames {place.region}")
            return Match(None, "другой регион GeoNames; состав региона у источника может отличаться")
        return Match(None, "город места не определён")

    def _region(self, geoname_id: int) -> str | None:
        try:
            return self.catalog.city_region(geoname_id)
        except InvalidSearchError:
            return None
