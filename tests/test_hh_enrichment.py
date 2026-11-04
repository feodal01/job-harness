"""HH relocation heuristic: moved from the local filter into the HH normalizer (§11, decision 04.11.2026).

The expressions are an HH-only heuristic for Russian/English wording, kept as is until the HH stage.
"""

import pytest

from src.enrichment.registry import default_enrichment
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from tests.filter_support import evaluate

AREA = {"countryIsoCode": "RU", "name": "Москва"}


def hh(**fields):
    vacancy = Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1", source_data={"area": AREA}, **fields)
    return default_enrichment().enrich(vacancy, "hh_ru")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We provide relocation assistance and visa support.", True),
        ("Помогаем с переездом и оформлением визы.", True),
        ("Помощь с релокацией", True),
        ("Релокационный пакет", True),
        ("Предоставляем релокационный пакет.", True),
        ("Готовность к релокации обязательна.", None),
        ("Наш офис в Алматы.", None),
        ("Relocation support is not provided.", False),
        ("We do not provide relocation assistance.", False),
        ("Не предоставляем помощь с переездом.", False),
        ("Без помощи с переездом", False),
        ("Помощь с релокацией не предоставляется", False),
        ("Опыт работы в агентстве релокации.", None),
        ("We provide relocation assistance. Relocation support is not provided.", None),
        ("Relocation support is not required.", None),
        ("Relocation assistance may be available.", None),
        ("Relocation assistance is available for senior candidates only.", None),
        ("We do not charge candidates for relocation assistance.", None),
        ("Помощь с переездом возможна после испытательного срока", None),
        ("Experience administering relocation assistance is required.", None),
        ("Relocation assistance is required.", None),
        ("Опыт оформления помощи с релокацией обязателен.", None),
    ],
)
def test_relocation_wording_sets_the_place_fact(text, expected):
    vacancy = hh(description=text, work_formats=["office", "hybrid"])
    assert [p.relocation_support for p in vacancy.locations] == [expected, expected]
    status = {True: "accepted", False: "rejected", None: "unknown"}[expected]
    result = evaluate(vacancy, SearchFilters(relocation_support=True), complete=True)
    assert next(c for c in result.checks if c.field == "relocation_support").status == status
    assert result.status == ("maybe" if expected is False else "accepted")


def test_relocation_needs_the_full_description_not_a_summary():
    vacancy = hh(summary="We provide relocation assistance.", work_formats=["office"])
    assert [p.relocation_support for p in vacancy.locations] == [None]


def test_relocation_without_a_place_is_not_recorded_and_geography_is_unchanged():
    # No fictitious place: relocation belongs to a place, and HH gave none (no formats, no places).
    vacancy = hh(description="Помощь с релокацией")
    assert vacancy.locations == []
    result = evaluate(
        vacancy, SearchFilters(locations=[{"country": "RU", "scope": "country"}]), complete=True
    )
    assert next(c for c in result.checks if c.field == "locations").status == "unknown"


def test_relocation_is_set_on_the_place_the_parser_built_from_area():
    stored = Vacancy(
        id="1",
        title="QA",
        url="https://hh.ru/vacancy/1",
        description="Помощь с релокацией",
        locations=[{"country": "RU", "city": "Москва"}],
    )
    [place] = default_enrichment().enrich(stored, "hh_ru").locations
    assert (place.country, place.city, place.relocation_support) == ("RU", "Москва", True)


def test_excluded_places_get_no_relocation():
    vacancy = hh(
        description="Помощь с релокацией. Удалённо из любой точки мира, кроме России.",
        work_formats=["remote_country"],
    )
    assert {p.excluded: p.relocation_support for p in vacancy.locations} == {False: True, True: None}


def test_hh_relocation_is_recomputed_idempotently():
    once = hh(description="Помощь с релокацией", work_formats=["office"])
    assert default_enrichment().enrich(once, "hh_ru") == once
    stored = once.model_copy(update={"description": None})
    assert [p.relocation_support for p in default_enrichment().enrich(stored, "hh_ru").locations] == [None]
