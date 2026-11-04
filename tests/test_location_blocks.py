"""Location-specific conditions stay correlated across saving, filtering and native queries."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from geo_support import getmatch_form

from src.models.brief import BriefInput
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy
from src.parsers.aggregators.getmatch.parser import GetmatchSearch
from src.parsers.aggregators.hh.parser import HHSearch
from tests.filter_support import evaluate
from tests.test_geography_sqlite import client as client


def place(country, city, mode=None, relocation=False):
    return {"country": country, "city": city, "minimum_work_format": mode, "relocation_support": relocation}


@pytest.mark.parametrize(
    "country,city,mode,expected",
    [
        ("RU", "Москва", "office", "maybe"),
        ("RU", "Москва", "hybrid", "accepted"),
        ("DE", "Berlin", "office", "accepted"),
        ("DE", "Berlin", "hybrid", "accepted"),
    ],
)
def test_format_is_specific_to_the_matching_place(country, city, mode, expected):
    filters = SearchFilters(locations=[place("RU", "Москва", "hybrid"), place("DE", "Berlin", "office")])
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://example.org/1",
        locations=[{"country": country, "city": city, "format": mode}],
    )
    assert evaluate(vacancy, filters).status == expected


def test_relocation_is_only_required_for_its_place():
    filters = SearchFilters(locations=[place("RU", "Москва"), place("DE", "Berlin", relocation=True)])
    for country, city, expected in [("RU", "Москва", "accepted"), ("DE", "Berlin", "maybe")]:
        vacancy = Vacancy(
            id="1",
            title="QA",
            url="https://example.org/1",
            # Relocation is a fact of the place, set by the source normalizer (§11).
            locations=[{"country": country, "city": city, "format": "office", "relocation_support": False}],
        )
        assert evaluate(vacancy, filters).status == expected


def test_conditions_from_different_location_branches_cannot_rescue_each_other():
    filters = SearchFilters(
        locations=[place("RU", "Москва", "hybrid"), place("DE", "Berlin", "office", relocation=True)]
    )
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://example.org/1",
        locations=[
            {"country": "RU", "city": "Москва", "format": "office", "relocation_support": False},
            {"country": "DE", "city": "Berlin", "format": "office", "relocation_support": False},
        ],
    )
    assert evaluate(vacancy, filters).status == "maybe"


async def test_place_conditions_survive_save_and_read(client):
    locations = [
        {
            "country": "RU",
            "geoname_id": 524901,
            "city_display_name": "Москва",
            "minimum_work_format": "hybrid",
            "relocation_support": False,
        },
        {
            "country": "DE",
            "geoname_id": 2950159,
            "city_display_name": "Berlin",
            "minimum_work_format": "office",
            "relocation_support": True,
        },
    ]
    response = await client.post(
        "/api/v1/briefs", json={"name": "Blocks", "filters": {"locations": locations}}
    )
    assert response.status_code == 201, response.text
    saved = response.json()["filters"]["locations"]
    assert [(p["minimum_work_format"], p["relocation_support"]) for p in saved] == [
        ("hybrid", False),
        ("office", True),
    ]
    reread = (await client.get("/api/v1/briefs")).json()[0]
    assert reread["filters"]["locations"] == saved


async def test_legacy_general_conditions_are_moved_to_places_when_saving(client):
    response = await client.post(
        "/api/v1/briefs",
        json={
            "name": "Old",
            "filters": {
                "locations": [{"country": "RU", "geoname_id": 524901, "city_display_name": "Москва"}],
                "minimum_work_format": "hybrid",
                "relocation_support": True,
            },
        },
    )
    assert response.status_code == 201
    filters = response.json()["filters"]
    assert filters["locations"][0]["minimum_work_format"] == "hybrid"
    assert filters["locations"][0]["relocation_support"] is True
    assert filters.get("minimum_work_format") is None and not filters.get("relocation_support")


async def test_hh_uses_the_format_of_each_city_branch():
    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        text = parse_qs(urlsplit(url).query)["text"][0]
        return json.dumps({"items": [{"id": "1", "text": text, "parent": {"id": "113", "text": "Россия"}}]})

    brief = BriefInput(
        name="Native",
        queries=["QA"],
        filters={
            "locations": [
                {**place("RU", "Moscow", "hybrid"), "geoname_id": 524901},
                {**place("RU", "Saint Petersburg", "remote_country"), "geoname_id": 498817},
            ]
        },
    )
    requests = (await HHSearch().prepare(brief, fetch)).requests
    physical = [
        parse_qs(urlsplit(r.url).query)
        for r in requests
        if parse_qs(urlsplit(r.url).query).get("area") == ["1"]
    ]
    assert physical and all(p["work_format"] == ["HYBRID", "REMOTE"] for p in physical)
    broadened = (await HHSearch().prepare(brief, fetch, second_chance=True)).requests
    assert any("work_format" not in parse_qs(urlsplit(r.url).query) for r in broadened)


async def test_getmatch_does_not_add_physical_regions_for_remote_only_places():
    brief = BriefInput(
        name="Native",
        filters={
            "locations": [
                {**place("RU", "Moscow", "hybrid"), "geoname_id": 524901},
                place("DE", "Berlin", "remote_country"),
            ]
        },
    )

    requests = (await GetmatchSearch(100).prepare(brief, getmatch_form)).requests
    zones = sorted(sorted(parse_qs(urlsplit(r.url).query)["l"]) for r in requests)
    assert zones == [["moscow", "remote"], ["remote"]]
