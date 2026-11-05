"""Disagreements between facts of different methods (decision 05.11.2026).

The source's structure wins the decision; a text fact that contradicts it is not dropped but kept
as a conflict on the vacancy, shown on the filter check it concerns.

One rule today, for places: a place given by the source's structure lies in a country (or CLDR
group, or the world) that a place from another method excludes. A source stating both in its own
structure (Getmatch: «remote anywhere except Russia» and «office in Moscow») is one coherent statement.
"""

from src.geography.catalog import GeographyCatalog
from src.models.facts import Conflict
from src.models.filters import VacancyPlace
from src.models.vacancy import Vacancy


def find_conflicts(vacancy: Vacancy, catalog: GeographyCatalog) -> list[Conflict]:
    names = catalog.country_names("ru")
    conflicts = []
    places = list(enumerate(vacancy.locations))
    for i, place in places:
        if place.excluded or place.evidence is None or place.country is None:
            continue
        for j, excluded in places:
            if not excluded.excluded or excluded.evidence is None:
                continue
            if excluded.evidence.method == place.evidence.method:
                continue
            if not _inside(place.country, excluded, catalog):
                continue
            where = excluded.label or names.get(
                excluded.country or "", excluded.country or excluded.group or ""
            )
            conflicts.append(
                Conflict(
                    fact="locations",
                    places=[i, j],
                    message=(
                        f"{_method(place)}: «{place.label or place.city or names.get(place.country)}», "
                        f"формат {place.format or 'не указан'}; {_method(excluded)}: исключено «{where}»"
                    ),
                    evidence=[place.evidence, excluded.evidence],
                )
            )
    return conflicts


def _inside(country: str, excluded: VacancyPlace, catalog: GeographyCatalog) -> bool:
    if excluded.scope == "world":
        return True
    if excluded.scope == "group":
        return bool(excluded.group) and country in catalog.group_countries(excluded.group or "")
    return excluded.country == country


def _method(place: VacancyPlace) -> str:
    method = place.evidence.method if place.evidence else None
    return {"structured": "структура источника", "text": "текст вакансии"}.get(method or "", "вывод")
