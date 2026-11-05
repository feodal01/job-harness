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


def test_relocation_is_a_fact_of_the_area_place_even_without_formats():
    [place] = hh(description="Помощь с релокацией").locations
    assert (place.scope, place.country, place.geoname_id, place.format) == ("city", "RU", 524901, None)
    assert place.relocation_support is True
    assert place.evidence is not None
    assert (place.evidence.method, place.evidence.field) == ("text", "area, description")


def test_without_an_area_relocation_is_not_recorded():
    # Relocation belongs to a place; with no area there is none, and nothing is invented.
    vacancy = default_enrichment().enrich(
        Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1", description="Помощь с релокацией"), "hh_ru"
    )
    assert vacancy.locations == []
    result = evaluate(vacancy, SearchFilters(relocation_support=True), complete=True)
    assert next(c for c in result.checks if c.field == "relocation_support").status == "unknown"


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


# --- Live HH wording, 04.11.2026 (docs/experiments/hh-enrichment-2026-11-04, 260 cards) -----------

from src.geography.catalog import default_catalog  # noqa: E402
from src.parsers.aggregators.hh.enrichment import text as hh_text  # noqa: E402


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        # Employer assistance the old expressions missed.
        ("• Программа релокации для иногородних сотрудников", True),
        ("* Оплата релокации", True),
        ("• Компенсация аренды жилья при релокации", True),
        ("• Предоставляется служебное жильё при релокации;", True),
        ("Поддержку при релокации в Душанбе", True),
        ("• Полностью оплачивается релокация для специалиста", True),
        ("Предоставляем пакет релокации", True),
        ("• Relocation to Cyprus, support in relocation", True),
        ("• Релокация и помощь в ней: компания покрывает расходы на переезд", True),
        ("• Помогаем с переездом в Москву", True),
        # Not an employer's promise.
        ("Хотите помогать людям воплощать мечту о переезде", None),
        (
            "• Работа в Москве по программе «Мостик» с перспективой дальнейшего переезда в Дубайский офис",
            None,
        ),
        ("• UAE employment visa and relocation support where applicable", None),
        ("• Готовность к релокации на Дальний Восток", None),
        ("• Релокация в Дубай: Финансовая подушка (от 150 000 ₽/мес)", None),
        ("Возможна релокация", None),
        ("ВАКАНСИЯ С ПЕРЕЕЗДОМ В РЕСПУБЛИКУ ТАТАРСТАН", None),
        ("- Coordinating employee mobility processes, including assignment planning, relocation", None),
        ("*Relocation support is not available for this job", False),
    ],
)
def test_live_relocation_wording(sentence, expected):
    assert hh_text.relocation(sentence) is expected


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("• ПОЛНОСТЬЮ удаленный формат работы из любой точки земного шара", True),
        ("• Свобода локации: работайте из дома или любой точки мира по комфортному для вас графику", True),
        ("• Удаленка из любой точки мира или офис", True),
        ("Сейчас MadOut2 играет более 200 миллионов человек по всему миру", False),
        ("Формат: полностью удалённая работа — работай из любой точки;", False),  # Not said: the world.
    ],
)
def test_live_worldwide_wording(sentence, expected):
    assert hh_text.worldwide(sentence) is expected


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("- Кроме того, вас будут ждать чай и печеньки;", set()),  # «того» is not Togo.
        ("Кроме того мы эксклюзивный дистрибьютор ТМ SKIL на территории России и Беларуси", set()),
        ("Вся команда работает удаленно, кроме производства", set()),
        ("График работы: пятидневка, кроме сб и вс", set()),
        ("Удалённая работа из любой точки мира, кроме России и Германии.", {"RU", "DE"}),
        ("Удалённо из любой страны, кроме РФ", {"RU"}),
        ("Work remotely worldwide except Germany.", {"DE"}),
        ("Remote work from anywhere excluding Russia, Belarus and Iran", {"RU", "BY", "IR"}),
    ],
)
def test_live_exclusion_wording(sentence, expected):
    assert hh_text.excluded_countries(sentence, default_catalog()) == expected
