"""Facts from the source's structure and from the text may disagree (decision 05.11.2026).

The structure wins the decision; the disagreement is kept on the vacancy and shown on the check
it concerns, so it can be highlighted to the user.
"""

from src.enrichment.registry import default_enrichment
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from tests.filter_support import evaluate

EXCEPT_RUSSIA = "Удалённая работа из любой точки мира, кроме России и Беларуси."
MOSCOW_OFFICE = {"country": "RU", "geoname_id": 524901, "city": "Moscow", "minimum_work_format": "office"}


def hh(formats, description=EXCEPT_RUSSIA):
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://hh.ru/vacancy/1",
        description=description,
        source_data={"area": {"@id": 1, "name": "Москва"}, "workFormats": formats},
    )
    return default_enrichment().enrich(vacancy, "hh_ru")


def locations_check(vacancy, place):
    result = evaluate(vacancy, SearchFilters(locations=[place]), complete=True)
    return result, next(c for c in result.checks if c.field == "locations")


def test_structured_office_against_text_exclusion_is_recorded():
    vacancy = hh(["REMOTE", "ON_SITE"])
    [conflict] = vacancy.conflicts
    assert conflict.fact == "locations"
    office = next(i for i, p in enumerate(vacancy.locations) if p.format == "office")
    russia = next(i for i, p in enumerate(vacancy.locations) if p.excluded and p.country == "RU")
    assert conflict.places == [office, russia]
    assert {e.method for e in conflict.evidence} == {"structured", "text"}
    assert "Москва" in conflict.message and "Россия" in conflict.message


def test_structure_wins_and_the_check_shows_the_conflict():
    result, check = locations_check(hh(["REMOTE", "ON_SITE"]), MOSCOW_OFFICE)
    assert result.status == "accepted" and check.status == "accepted"
    assert len(check.conflicts) == 1 and "Россия" in check.conflicts[0]


def test_a_check_decided_by_another_place_carries_no_conflict():
    germany = {"country": "DE", "scope": "country", "minimum_work_format": "remote_country"}
    _, check = locations_check(hh(["REMOTE", "ON_SITE"]), germany)
    assert check.status == "accepted" and check.conflicts == []


def test_one_coherent_statement_is_not_a_conflict():
    # Only remote work, from the text alone: «anywhere except Russia» is consistent.
    vacancy = hh(["REMOTE"])
    assert vacancy.conflicts == []
    result, check = locations_check(vacancy, MOSCOW_OFFICE)
    assert result.status == "rejected" and check.conflicts == []


def test_a_source_stating_both_in_its_structure_is_not_a_conflict():
    # Getmatch: remote worldwide except Russia, and an office in Moscow — all structured.
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://getmatch.ru/1",
        source_data={
            "location_requirements": [
                {"location_id": "_cu-world", "city": "Весь мир", "format": "remote"},
                {"location_id": "russia", "country": "Россия", "format": "remote", "exclude": True},
                {
                    "location_id": "moscow__mo__russia",
                    "city": "Москва",
                    "country": "Россия",
                    "format": "office",
                },
            ]
        },
    )
    assert default_enrichment().enrich(vacancy, "getmatch").conflicts == []


def test_conflicts_are_recomputed_on_every_enrichment():
    once = hh(["REMOTE", "ON_SITE"])
    assert default_enrichment().enrich(once, "hh_ru") == once
    without_text = once.model_copy(update={"description": "QA", "summary": None})
    assert default_enrichment().enrich(without_text, "hh_ru").conflicts == []
