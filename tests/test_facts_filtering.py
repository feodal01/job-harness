"""Stage 4 of docs/vacancy-facts-plan.md (§11): the local filter compares places as facts.

A vacancy whose places carry `scope` (set by a source normalizer, today Getmatch) is compared by
GeoNames ID, GeoNames region, ISO country and CLDR group; strings and the description are not read.
"""

import ast
from pathlib import Path

import pytest

from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from tests.filter_support import TEST_RANGE, evaluate

MOSCOW, KHIMKI, KAZAN, BERLIN = 524901, 550280, 551487, 2950159
CITIES = {
    "Москва": ("RU", MOSCOW),
    "Химки": ("RU", KHIMKI),
    "Казань": ("RU", KAZAN),
    "Berlin": ("DE", BERLIN),
}


def city(name, **conditions):
    country, geoname_id = CITIES[name]
    return {"country": country, "geoname_id": geoname_id, "city": name, **conditions}


def country(code, **conditions):
    return {"country": code, "scope": "country", **conditions}


def vacancy(*places, **fields):
    return Vacancy(id="1", title="QA", url="https://getmatch.ru/1", locations=list(places), **fields)


def check(result, field):
    return next(c for c in result.checks if c.field == field)


def status(places, wanted, **filters):
    places = places if isinstance(places, list) else [places]
    result = evaluate(vacancy(*places), SearchFilters(locations=[wanted], **filters))
    return check(result, "locations").status


WORLD = {"scope": "world", "worldwide": True, "label": "Весь мир"}
EUROPE = {"scope": "group", "group": "150", "label": "Европа"}
EMEA = {"scope": "group", "group": None, "label": "EMEA"}
RUSSIA = {"scope": "country", "country": "RU", "label": "Россия"}
MOSCOW_OBLAST = {"scope": "region", "country": "RU", "region": "Moscow Oblast", "label": "Московская область"}
MOSCOW_CITY = {"scope": "city", "country": "RU", "geoname_id": MOSCOW, "region": "Moscow", "label": "Москва"}
INNOPOLIS = {"scope": "city", "country": "RU", "label": "Иннополис"}  # Not in GeoNames: no ID.
UNKNOWN_COUNTRY = {"scope": "city", "label": "Екатеринбург"}
CYPRUS = {"scope": "country", "country": "CY", "label": "Кипр"}


@pytest.mark.parametrize(
    ("place", "fmt", "wanted", "expected"),
    [
        # The whole world.
        (WORLD, "remote_worldwide", city("Москва"), "accepted"),
        (WORLD, "office", city("Москва"), "accepted"),
        # A CLDR group: membership decides the country; a city inside stays open for office work.
        (EUROPE, "remote_country", country("DE"), "accepted"),
        (EUROPE, "remote_country", country("AM"), "rejected"),
        (EUROPE, "remote_country", city("Berlin"), "accepted"),
        (EUROPE, "office", city("Berlin"), "unknown"),
        (EUROPE, "office", country("AM"), "rejected"),
        (EMEA, "remote_country", country("DE"), "unknown"),
        # A country.
        (RUSSIA, "office", country("RU"), "accepted"),
        (RUSSIA, "office", city("Москва"), "unknown"),
        (RUSSIA, "remote_country", city("Москва"), "accepted"),
        (RUSSIA, "office", country("DE"), "rejected"),
        # A GeoNames region: equal region accepts, another region stays unknown (§11 decision).
        (MOSCOW_OBLAST, "office", city("Химки"), "accepted"),
        (MOSCOW_OBLAST, "office", city("Москва"), "unknown"),
        (MOSCOW_OBLAST, "hybrid", city("Казань"), "unknown"),
        (MOSCOW_OBLAST, "office", country("RU"), "accepted"),
        (MOSCOW_OBLAST, "remote_country", city("Казань"), "accepted"),
        (MOSCOW_OBLAST, "office", country("DE"), "rejected"),
        # A city with a GeoNames ID.
        (MOSCOW_CITY, "office", city("Москва"), "accepted"),
        (MOSCOW_CITY, "office", city("Казань"), "rejected"),
        (MOSCOW_CITY, "hybrid", country("RU"), "accepted"),
        (MOSCOW_CITY, "office", city("Berlin"), "rejected"),
        # A city without an ID: only its country is known.
        (INNOPOLIS, "office", city("Казань"), "unknown"),
        (INNOPOLIS, "office", country("RU"), "accepted"),
        (INNOPOLIS, "office", country("DE"), "rejected"),
        # Unknown country.
        (UNKNOWN_COUNTRY, "office", country("RU"), "unknown"),
        # Unknown format: another country might still allow worldwide remote work.
        (CYPRUS, None, country("DE"), "unknown"),
        (CYPRUS, None, country("CY"), "accepted"),
        (MOSCOW_CITY, None, city("Казань"), "unknown"),
    ],
)
def test_place_scope_against_brief_place(place, fmt, wanted, expected):
    assert status({**place, "format": fmt}, wanted) == expected


def test_city_identity_is_the_geonames_id_not_the_label():
    kazan_labelled_moscow = {"scope": "city", "country": "RU", "geoname_id": KAZAN, "city": "Москва"}
    assert status({**kazan_labelled_moscow, "format": "office"}, city("Москва")) == "rejected"
    assert status({**kazan_labelled_moscow, "format": "office"}, city("Казань")) == "accepted"


@pytest.mark.parametrize(
    ("excluded", "wanted", "expected"),
    [
        ({"scope": "country", "country": "RU"}, country("RU"), "rejected"),
        ({"scope": "country", "country": "RU"}, city("Москва"), "rejected"),
        ({"scope": "country", "country": "RU"}, country("DE"), "accepted"),
        ({"scope": "group", "group": "EU"}, country("DE"), "rejected"),
        ({"scope": "group", "group": "EU"}, country("AM"), "accepted"),
        ({"scope": "group", "group": None}, country("DE"), "unknown"),
        ({"scope": "city", "country": "RU", "geoname_id": MOSCOW}, city("Москва"), "rejected"),
        ({"scope": "city", "country": "RU", "geoname_id": MOSCOW}, city("Казань"), "accepted"),
        ({"scope": "city", "country": "RU", "geoname_id": MOSCOW}, country("RU"), "unknown"),
        ({"scope": "region", "country": "RU", "region": "Moscow Oblast"}, city("Химки"), "rejected"),
        ({"scope": "region", "country": "RU", "region": "Moscow Oblast"}, city("Москва"), "unknown"),
        ({"scope": "city", "country": "RU", "label": "Иннополис"}, city("Казань"), "unknown"),
    ],
)
def test_explicit_exclusions_use_the_same_comparison(excluded, wanted, expected):
    places = [{**WORLD, "format": "remote_worldwide"}, {**excluded, "excluded": True}]
    assert status(places, wanted) == expected


def test_office_exclusion_does_not_exclude_another_office_format():
    # As before stage 4: for office and hybrid work only an exclusion of that format (or of any) applies.
    places = [{**MOSCOW_CITY, "format": "office"}, {**MOSCOW_CITY, "format": "hybrid", "excluded": True}]
    assert status(places, city("Москва")) == "accepted"
    places[1]["format"] = None
    assert status(places, city("Москва")) == "rejected"


def test_format_is_never_borrowed_from_other_places_or_work_formats():
    places = [{**MOSCOW_CITY, "format": "office"}, {**CYPRUS, "format": None, "relocation_support": True}]
    result = evaluate(
        vacancy(*places, work_formats=["office"]),
        SearchFilters(locations=[country("CY", minimum_work_format="office", relocation_support=True)]),
    )
    assert check(result, "locations").status == "accepted"
    assert check(result, "minimum_work_format").status == "unknown"
    assert check(result, "relocation_support").status == "accepted"
    # work_formats of the whole vacancy add no alternative to fact places.
    result = evaluate(
        vacancy({**MOSCOW_CITY, "format": "office"}, work_formats=["office", "remote_country"]),
        SearchFilters(locations=[city("Казань")]),
    )
    assert check(result, "locations").status == "rejected"


@pytest.mark.parametrize(
    ("wanted", "relocation", "expected"),
    [
        (city("Berlin", relocation_support=True), "accepted", "accepted"),
        (city("Москва", relocation_support=True), "unknown", "accepted"),
        (country("CY", relocation_support=True), "rejected", "maybe"),
    ],
)
def test_relocation_is_read_from_the_same_place(wanted, relocation, expected):
    places = [
        {**MOSCOW_CITY, "format": "office"},
        {
            "scope": "city",
            "country": "DE",
            "geoname_id": BERLIN,
            "format": "office",
            "relocation_support": True,
        },
        {**CYPRUS, "format": "hybrid", "relocation_support": False},
    ]
    result = evaluate(vacancy(*places), SearchFilters(locations=[wanted]))
    assert check(result, "relocation_support").status == relocation
    assert result.status == expected


def test_relocation_is_known_without_the_full_description_and_text_is_not_read():
    place = {**CYPRUS, "format": "hybrid", "relocation_support": True}
    filters = SearchFilters(locations=[country("CY", relocation_support=True)])
    assert check(evaluate(vacancy(place), filters), "relocation_support").status == "accepted"
    text = vacancy(place, description="Relocation support is not provided.")
    assert check(evaluate(text, filters, complete=True), "relocation_support").status == "accepted"


def test_reasons_name_the_deciding_place_and_the_comparison():
    result = evaluate(
        vacancy({**MOSCOW_CITY, "format": "office", "relocation_support": True}),
        SearchFilters(locations=[city("Москва", relocation_support=True)]),
    )
    assert "Москва" in check(result, "locations").reason
    assert "GeoNames" in check(result, "locations").reason
    assert "Москва" in check(result, "relocation_support").reason
    unknown = evaluate(
        vacancy({**MOSCOW_OBLAST, "format": "office"}), SearchFilters(locations=[city("Москва")])
    )
    assert "Московская область" in check(unknown, "locations").reason


def test_catalog_is_passed_explicitly():
    from src.selection.filtering import evaluate as bare

    with pytest.raises(TypeError):
        bare(vacancy(), SearchFilters())  # type: ignore[call-arg]


SELECTION = Path(__file__).parents[1] / "src/selection"


def test_checks_compare_facts_and_parse_no_text():
    """Only the user's own excluded words are searched in text; nothing else uses regular expressions."""
    for path in [SELECTION / "filtering.py", *sorted((SELECTION / "checks").rglob("*.py"))]:
        tree = ast.parse(path.read_text())
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        if path.name != "excluded_text.py":
            assert "re" not in imports, path
        assert "default_catalog" not in path.read_text(), path
        if path.name != "legacy.py":
            assert "catalog.matches(" not in path.read_text(), path  # Label comparison: legacy branch only.


def test_one_module_per_brief_condition():
    from src.models.filters import FILTER_NAMES
    from src.selection.checks import CHECKS

    produced = [name for c in CHECKS for name in c.fields]
    assert sorted(produced) == sorted(FILTER_NAMES)


# --- Review findings (04.11.2026) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("place", "wanted", "expected"),
    [
        (MOSCOW_OBLAST, city("Казань"), "unknown"),
        (EUROPE, country("AM"), "unknown"),
        (EUROPE, country("DE"), "accepted"),
    ],
)
def test_region_and_group_of_unknown_format(place, wanted, expected):
    assert status({**place, "format": None}, wanted) == expected


def test_a_possible_exclusion_does_not_hide_a_clear_mismatch():
    almaty = {"scope": "country", "country": "KZ", "format": "office", "label": "Алматы"}
    possible = {**INNOPOLIS, "excluded": True}
    result = evaluate(
        vacancy(almaty, possible), SearchFilters(locations=[city("Москва", minimum_work_format="office")])
    )
    assert check(result, "locations").status == "rejected"
    assert result.status == "rejected"


def test_an_exclusion_of_unknown_country_leaves_the_place_unknown():
    places = [{**RUSSIA, "format": "remote_country"}, {**UNKNOWN_COUNTRY, "excluded": True}]
    assert status(places, country("RU")) == "unknown"


@pytest.mark.parametrize(
    ("option", "excluded", "wanted", "expected"),
    [
        # An office exclusion does not forbid remote work there, and the other way round.
        (
            {**RUSSIA, "format": "remote_country"},
            {**MOSCOW_CITY, "format": "office"},
            city("Москва"),
            "accepted",
        ),
        (
            {**MOSCOW_CITY, "format": "office"},
            {**RUSSIA, "format": "remote_country"},
            city("Москва"),
            "accepted",
        ),
        # Any remote exclusion applies to remote work: "worldwide except Germany".
        (
            {**WORLD, "format": "remote_worldwide"},
            {"scope": "country", "country": "DE", "format": "remote_country"},
            country("DE"),
            "rejected",
        ),
    ],
)
def test_exclusions_apply_to_their_own_work_format(option, excluded, wanted, expected):
    assert status([option, {**excluded, "excluded": True}], wanted) == expected


def test_unproven_option_keeps_unknown_and_the_reason_names_the_proven_place():
    # Office in Moscow fails «hybrid or freer»; hybrid in Moscow Oblast may include Moscow (§11 decision 3).
    places = [{**MOSCOW_CITY, "format": "office"}, {**MOSCOW_OBLAST, "format": "hybrid"}]
    result = evaluate(
        vacancy(*places), SearchFilters(locations=[city("Москва", minimum_work_format="hybrid")])
    )
    assert check(result, "locations").status == "accepted"
    assert "«Москва»" in check(result, "locations").reason
    assert check(result, "minimum_work_format").status == "unknown"


@pytest.mark.parametrize(
    "places", [[{**MOSCOW_OBLAST, "format": "office"}], [{**CYPRUS, "format": "office"}]]
)
def test_relocation_reason_agrees_with_its_status(places):
    places = [{**p, "relocation_support": True} for p in places]
    result = evaluate(vacancy(*places), SearchFilters(locations=[city("Москва", relocation_support=True)]))
    relocation = check(result, "relocation_support")
    assert relocation.status == "unknown"
    assert "есть" not in relocation.reason


def test_place_without_label_is_named_by_its_identifier():
    place = {"scope": "city", "country": "RU", "geoname_id": KAZAN, "format": "office"}
    result = evaluate(vacancy(place), SearchFilters(locations=[city("Москва")]))
    assert str(KAZAN) in check(result, "locations").reason


def test_the_passed_catalog_is_the_one_used():
    from src.geography.catalog import GeographyCatalog
    from src.selection.filtering import evaluate as bare

    class Armenia150(GeographyCatalog):
        def group_countries(self, code):
            return frozenset({"AM"}) if code == "150" else super().group_countries(code)

    filters = SearchFilters(locations=[country("AM")])
    result = bare(
        vacancy({**EUROPE, "format": "remote_country"}),
        filters,
        catalog=Armenia150(),
        salary_range=TEST_RANGE,
    )
    assert check(result, "locations").status == "accepted"


def test_brief_city_region_is_read_from_the_reference_once(monkeypatch):
    from src.geography.catalog import GeographyCatalog

    catalog = GeographyCatalog()
    calls = []
    original = catalog.get
    monkeypatch.setattr(catalog, "get", lambda geoname_id: calls.append(geoname_id) or original(geoname_id))
    from src.selection.filtering import evaluate as bare

    filters = SearchFilters(locations=[city("Химки")])
    for _ in range(3):
        bare(
            vacancy({**MOSCOW_OBLAST, "format": "office"}), filters, catalog=catalog, salary_range=TEST_RANGE
        )
    assert calls == [KHIMKI]
