"""Stage 3 of docs/vacancy-facts-plan.md: Getmatch places in the brief's vocabulary."""

import pytest

from src.enrichment.registry import default_enrichment
from src.models.filters import VacancyPlace
from src.models.vacancy import Vacancy

enrich = default_enrichment().enrich
RU = ["russia", "_cu-europe", "_cu-emea", "_cu-world"]


def place(**record) -> VacancyPlace:
    record = {"exclude": False, "ancestors": [], "city": "", "country": "", **record}
    vacancy = Vacancy(
        id="1", title="QA", url="https://getmatch.ru/1", source_data={"location_requirements": [record]}
    )
    [result] = enrich(vacancy, "getmatch").locations
    return result


@pytest.mark.parametrize(
    "record,expected",
    [
        (
            {
                "location_id": "kazan__tatarstan__russia",
                "city": "Казань",
                "country": "Россия",
                "format": "hybrid",
            },
            {
                "scope": "city",
                "country": "RU",
                "geoname_id": 551487,
                "region": "Tatarstan Republic",
                "format": "hybrid",
            },
        ),
        (
            {"location_id": "russia", "country": "Россия", "format": "remote", "ancestors": RU},
            {"scope": "country", "country": "RU", "geoname_id": None, "format": "remote_country"},
        ),
        (
            {"location_id": "_cu-world", "city": "Весь мир", "format": "remote"},
            {"scope": "world", "country": None, "format": "remote_worldwide", "worldwide": True},
        ),
        (
            {"location_id": "_cu-europe", "city": "Европа", "format": "remote"},
            {"scope": "group", "group": "150", "format": "remote_country"},
        ),
        (
            {"location_id": "_cu-european-union", "city": "European Union", "format": "office"},
            {"scope": "group", "group": "EU", "format": "office"},
        ),
        (
            {"location_id": "_cu-emea", "city": "EMEA", "format": "remote"},
            {"scope": "group", "group": None},  # Not a CLDR group: members unknown.
        ),
        (
            {"location_id": "cyprus", "country": "Кипр", "format": "relocation_company"},
            {"scope": "country", "country": "CY", "format": None, "relocation_support": True},
        ),
        (
            {"location_id": "cyprus", "country": "Кипр", "format": "relocation_candidate"},
            {"scope": "country", "country": "CY", "format": None, "relocation_support": False},
        ),
        (
            {
                "location_id": "mo__russia",
                "city": "Московская область",
                "country": "Россия",
                "format": "office",
            },
            {"scope": "region", "country": "RU", "region": "Moscow Oblast", "geoname_id": None},
        ),
        (
            {
                "location_id": "slovakia",
                "country": "Словацкая Республика",
                "format": "office",
                "ancestors": ["slovakia"],
            },
            {"scope": "country", "country": "SK"},  # Label outside CLDR: country slug of the hierarchy.
        ),
        (
            {"location_id": "unknown", "city": "Екатеринбург", "country": "unknown", "format": "office"},
            {"scope": "city", "country": None, "geoname_id": None, "label": "Екатеринбург"},
        ),
        (
            {
                "location_id": "simferopol__crimea",
                "city": "Симферополь",
                "country": "Автономная Республика Крым",
                "format": "office",
                "ancestors": ["simferopol", "crimea"],
            },
            {"scope": "city", "country": None, "geoname_id": None},  # No ISO country: unknown, not guessed.
        ),
        (
            {"location_id": "germany", "country": "Германия", "format": "remote", "exclude": True},
            {"scope": "country", "country": "DE", "excluded": True},
        ),
    ],
)
def test_each_getmatch_record_kind_becomes_a_place_in_the_brief_vocabulary(record, expected):
    result = place(**record)
    assert {k: getattr(result, k) for k in expected} == expected
    assert result.evidence is not None
    assert (result.evidence.enricher, result.evidence.method) == ("getmatch", "structured")
    assert result.evidence.field == "location_requirements[0]"
    assert result.evidence.raw == record["location_id"]


def test_ambiguous_city_keeps_its_label_without_a_geoname_id():
    result = place(location_id="kirovsk__russia", city="Кировск", country="Россия", format="office")
    assert (result.scope, result.country, result.geoname_id, result.label) == ("city", "RU", None, "Кировск")


def test_unknown_format_codes_are_skipped_not_guessed():
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://getmatch.ru/1",
        source_data={
            "location_requirements": [{"location_id": "russia", "country": "Россия", "format": "flex"}]
        },
    )
    assert enrich(vacancy, "getmatch").locations == []


def test_relocation_places_do_not_declare_a_work_format():
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://getmatch.ru/1",
        source_data={
            "location_requirements": [
                {
                    "location_id": "limassol__limassol__cyprus",
                    "city": "Лимасол",
                    "country": "Кипр",
                    "format": "office",
                },
                {"location_id": "cyprus", "country": "Кипр", "format": "relocation_company"},
            ]
        },
    )
    result = enrich(vacancy, "getmatch")
    assert result.work_formats == ["office"]
    assert [p.relocation_support for p in result.locations] == [None, True]
    assert enrich(result, "getmatch") == result  # Idempotent re-enrichment of a stored vacancy.


def test_stored_vacancy_without_source_data_keeps_its_places():
    stored = Vacancy(
        id="1",
        title="QA",
        url="https://getmatch.ru/1",
        locations=[{"country": "RU", "format": "remote_country", "worldwide": True}],
    )
    [result] = enrich(stored, "getmatch").locations
    assert result.format == "remote_worldwide"


def places(*records):
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://getmatch.ru/1",
        source_data={"location_requirements": [{"exclude": False, "city": "", **r} for r in records]},
    )
    return enrich(vacancy, "getmatch")


def test_relocation_record_applies_to_the_formats_of_the_same_place():
    # Vacancy 36643: Getmatch stores one pair "place + one option" per record.
    result = places(
        {"location_id": "cyprus", "country": "Cyprus", "format": "relocation_company"},
        {"location_id": "cyprus", "country": "Cyprus", "format": "hybrid"},
    )
    [cyprus] = result.locations
    assert (cyprus.country, cyprus.format, cyprus.relocation_support) == ("CY", "hybrid", True)
    assert cyprus.evidence is not None
    # Provenance: the format record first, then the relocation record; raw stays the location_id.
    assert cyprus.evidence.field == "location_requirements[1], location_requirements[0]"
    assert cyprus.evidence.raw == "cyprus"
    assert enrich(result, "getmatch") == result  # Idempotent.


def test_each_place_gets_only_its_own_relocation():
    # Vacancy 36245: remote or relocation to each of several countries.
    result = places(
        {"location_id": "cyprus", "country": "Кипр", "format": "remote"},
        {"location_id": "cyprus", "country": "Кипр", "format": "relocation_company"},
        {"location_id": "armenia", "country": "Армения", "format": "remote"},
        {"location_id": "russia", "country": "Россия", "format": "office"},
    )
    assert [(p.country, p.format, p.relocation_support) for p in result.locations] == [
        ("CY", "remote_country", True),
        ("AM", "remote_country", None),
        ("RU", "office", None),
    ]


def test_conflicting_relocation_records_of_one_place_stay_unknown():
    result = places(
        {"location_id": "cyprus", "country": "Кипр", "format": "office"},
        {"location_id": "cyprus", "country": "Кипр", "format": "relocation_company"},
        {"location_id": "cyprus", "country": "Кипр", "format": "relocation_candidate"},
    )
    assert [(p.format, p.relocation_support) for p in result.locations] == [("office", None)]


def test_excluded_and_included_records_of_one_place_are_not_merged():
    result = places(
        {"location_id": "cyprus", "country": "Кипр", "format": "office", "exclude": True},
        {"location_id": "cyprus", "country": "Кипр", "format": "relocation_company"},
    )
    assert [(p.excluded, p.format, p.relocation_support) for p in result.locations] == [
        (True, "office", None),
        (False, None, True),
    ]


def test_records_of_different_unidentified_places_are_not_merged():
    result = places(
        {"location_id": "unknown", "city": "Екатеринбург", "country": "unknown", "format": "office"},
        {
            "location_id": "unknown",
            "city": "Воронеж, Казань",
            "country": "unknown",
            "format": "relocation_company",
        },
        {"country": "Кипр", "format": "office"},
        {"country": "Кипр", "format": "relocation_company"},
    )
    assert [(p.label, p.format, p.relocation_support) for p in result.locations] == [
        ("Екатеринбург", "office", None),
        ("Воронеж, Казань", None, True),
        ("Кипр", "office", None),
        ("Кипр", None, True),
    ]


def test_an_excluded_relocation_record_is_not_a_geographic_exclusion():
    # Not understood (no such record in the feed so far): skipped, never turned into "not in Moscow".
    result = places(
        {"location_id": "russia", "country": "Россия", "format": "remote"},
        {
            "location_id": "moscow__mo__russia",
            "city": "Москва",
            "country": "Россия",
            "format": "relocation_candidate",
            "exclude": True,
        },
    )
    assert [(p.country, p.excluded) for p in result.locations] == [("RU", False)]


def test_empty_and_missing_labels_name_the_same_place():
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://getmatch.ru/1",
        source_data={
            "location_requirements": [
                {"location_id": "cyprus", "country": "Кипр", "city": "", "format": "office"},
                {"location_id": "cyprus", "country": "Кипр", "format": "relocation_company"},
            ]
        },
    )
    assert [(p.format, p.relocation_support) for p in enrich(vacancy, "getmatch").locations] == [
        ("office", True)
    ]
