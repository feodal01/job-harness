"""Stage 5 of docs/vacancy-facts-plan.md (§12): HH places from HH's own area directory.

Area ids, names and parents come from the snapshot of `api.hh.ru/areas` (hh/data/areas.json.gz);
countries, regions and cities are resolved through the single CLDR/GeoNames catalog.
"""

import pytest

from src.enrichment.registry import default_enrichment
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from tests.filter_support import evaluate

MOSCOW, IZHEVSK, KHIMKI, TROITSK_MOSCOW = 524901, 554840, 550280, 481608


def hh(area, formats=("ON_SITE",), **fields):
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://hh.ru/vacancy/1",
        source_data={"area": area, "workFormats": list(formats)},
        **fields,
    )
    return default_enrichment().enrich(vacancy, "hh_ru")


def place(area, **kwargs):
    [result] = hh(area, **kwargs).locations
    return result


@pytest.mark.parametrize(
    ("area", "expected"),
    [
        # Listing: only the id, the name and the path; the country comes from the HH directory.
        (
            {"@id": 1, "name": "Москва", "path": ".113.232.1."},
            {"scope": "city", "country": "RU", "geoname_id": MOSCOW, "region": "Moscow"},
        ),
        # Card: the same area with HH's own country code.
        (
            {"id": 96, "regionId": 1646, "countryIsoCode": "RU", "name": "Ижевск"},
            {"scope": "city", "country": "RU", "geoname_id": IZHEVSK, "region": "Udmurtiya Republic"},
        ),
        # A region as the vacancy area.
        (
            {"@id": 2019, "name": "Московская область"},
            {"scope": "region", "country": "RU", "region": "Moscow Oblast", "geoname_id": None},
        ),
        # The qualifier in brackets names the region: Khimki of Moscow Oblast.
        (
            {"@id": 2077, "name": "Химки (Московская область)"},
            {"scope": "city", "geoname_id": KHIMKI, "region": "Moscow Oblast"},
        ),
        # HH lists Troitsk (Moscow) under Moscow Oblast; its own qualifier «Москва» identifies the city.
        (
            {"@id": 2095, "name": "Троицк (Москва)"},
            {"scope": "city", "geoname_id": TROITSK_MOSCOW, "region": "Moscow"},
        ),
        # A village outside GeoNames cities15000: no ID, but the region is known.
        (
            {"@id": 19834, "name": "Алашайка"},
            {"scope": "city", "country": "RU", "geoname_id": None, "region": "Mariy-El Republic"},
        ),
        # A homonym whose HH region has no GeoNames counterpart: not guessed.
        (
            {"@id": 3378, "name": "Кировск"},
            {"scope": "city", "country": "RU", "geoname_id": None, "region": None},
        ),
        # A country of «Другие регионы».
        ({"@id": 13, "name": "Армения"}, {"scope": "country", "country": "AM", "geoname_id": None}),
        ({"@id": 2758, "name": "Тбилиси"}, {"scope": "city", "country": "GE", "geoname_id": 611717}),
    ],
)
def test_each_hh_area_becomes_a_place_in_the_brief_vocabulary(area, expected):
    result = place(area)
    assert {k: getattr(result, k) for k in expected} == expected
    assert result.label == area["name"]
    assert result.evidence is not None
    assert (result.evidence.enricher, result.evidence.method, result.evidence.field) == (
        "hh_ru",
        "structured",
        "area",
    )
    assert result.evidence.raw == str(area.get("id") or area.get("@id"))


def test_unknown_area_id_keeps_hh_country_and_label():
    result = place({"id": 99999999, "countryIsoCode": "RU", "name": "Новый город"})
    assert (result.scope, result.country, result.geoname_id, result.label) == (
        "city",
        "RU",
        None,
        "Новый город",
    )


def test_no_area_means_no_place():
    vacancy = Vacancy(
        id="1", title="QA", url="https://hh.ru/vacancy/1", source_data={"workFormats": ["REMOTE"]}
    )
    assert default_enrichment().enrich(vacancy, "hh_ru").locations == []


@pytest.mark.parametrize(
    ("codes", "formats"),
    [
        (["ON_SITE"], ["office"]),
        (["ON_SITE", "HYBRID"], ["office", "hybrid"]),
        (["REMOTE"], ["remote_country"]),
        (["FIELD_WORK"], [None]),  # «Разъездной»: no such format in the brief.
        # Live card 139023863: an extra unknown option would make a Kazan job «maybe remote» elsewhere.
        (["ON_SITE", "FIELD_WORK"], ["office"]),
        ([], [None]),
        (["SPACE_STATION"], [None]),  # Unknown code: not understood, never guessed.
    ],
)
def test_one_place_per_hh_work_format(codes, formats):
    result = hh({"@id": 1, "name": "Москва"}, formats=codes)
    assert [p.format for p in result.locations] == formats
    assert {p.geoname_id for p in result.locations} == {MOSCOW}


def test_listing_formats_are_kept_in_source_data_and_stored_vacancies_reenrich_identically():
    from pathlib import Path

    from src.parsers.aggregators.hh.parser import HHSearch

    search = HHSearch()
    body = (Path(__file__).parent / "fixtures/success.html").read_text()
    listing = search.parse(body, search.build("QA")).vacancies
    for raw in listing:
        assert isinstance(raw.source_data.get("workFormats"), list)
        assert raw.locations == []  # The parser no longer builds places; the HH normalizer does.
        enriched = default_enrichment().enrich(raw, "hh_ru")
        assert enriched.locations and all(p.scope == "city" for p in enriched.locations)
        assert default_enrichment().enrich(enriched, "hh_ru") == enriched


def test_hh_places_are_compared_as_facts():
    khimki_office = hh({"@id": 2077, "name": "Химки (Московская область)"})
    brief = {"country": "RU", "geoname_id": KHIMKI, "city": "Khimki", "minimum_work_format": "office"}
    result = evaluate(khimki_office, SearchFilters(locations=[brief]))
    locations = next(c for c in result.checks if c.field == "locations")
    assert locations.status == "accepted"
    assert "GeoNames" in locations.reason
    moscow = {**brief, "geoname_id": MOSCOW, "city": "Moscow"}
    assert evaluate(khimki_office, SearchFilters(locations=[moscow])).status == "rejected"


def test_snapshot_is_one_version_with_provenance():
    from src.parsers.aggregators.hh.enrichment.areas import default_areas

    areas = default_areas()
    assert areas.metadata["source"] == "https://api.hh.ru/areas"
    assert len(areas.metadata["source_sha256"]) == 64
    assert areas.metadata["areas"] > 18000
    assert [a.name for a in areas.chain("96")] == ["Россия", "Удмуртская Республика", "Ижевск"]
