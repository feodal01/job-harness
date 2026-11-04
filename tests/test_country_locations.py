"""Explicit whole-country selection is distinct from an unconfirmed city."""

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


async def test_country_selection_is_saved_without_inventing_city_identity(client):
    response = await client.post(
        "/api/v1/briefs",
        json={
            "name": "Country",
            "filters": {
                "locations": [
                    {
                        "country": "RU",
                        "scope": "country",
                        "minimum_work_format": "hybrid",
                        "relocation_support": True,
                    }
                ]
            },
        },
    )
    assert response.status_code == 201, response.text
    place = response.json()["filters"]["locations"][0]
    assert place["scope"] == "country" and place["country_name_en"] == "Russia"
    assert all(place.get(key) is None for key in ("city", "geoname_id", "city_display_name", "region"))
    assert place["minimum_work_format"] == "hybrid" and place["relocation_support"] is True
    assert (await client.get("/api/v1/briefs")).json()[0]["filters"]["locations"][0] == place


async def test_multiple_country_selections_do_not_collide_on_missing_geonames_id(client):
    places = [{"country": c, "scope": "country"} for c in ["RU", "DE", "JP", "TH", "VN", "GE"]]
    for selections, status in [(places[:5], 201), (places, 422), ([places[0], places[0]], 422)]:
        response = await client.post(
            "/api/v1/briefs", json={"name": "Limits", "filters": {"locations": selections}}
        )
        assert response.status_code == status, response.text


@pytest.mark.parametrize(
    "place",
    [
        {"country": "RU"},
        {"country": "RU", "scope": "country", "geoname_id": 524901},
        {"country": "RU", "scope": "country", "city": "Moscow"},
        {"country": "RU", "scope": "country", "city_display_name": "Москва"},
    ],
)
async def test_country_choice_must_be_explicit_and_cannot_contain_city_data(client, place):
    response = await client.post(
        "/api/v1/briefs", json={"name": "Invalid", "filters": {"locations": [place]}}
    )
    assert response.status_code == 422


async def test_hh_prepares_country_area_without_city_lookup():
    async def fetch(url):
        assert "/areas/countries" in url, "Whole-country search must not request city suggestions"
        return json.dumps([{"id": "113", "name": "Россия"}])

    brief = BriefInput(
        name="Country", queries=["QA"], filters={"locations": [{"country": "RU", "scope": "country"}]}
    )
    requests = (await HHSearch().prepare(brief, fetch)).requests
    assert any(parse_qs(urlsplit(r.url).query).get("area") == ["113"] for r in requests)


async def test_getmatch_uses_all_russian_physical_categories_and_remote():
    brief = BriefInput(name="Country", filters={"locations": [{"country": "RU", "scope": "country"}]})
    request = (await GetmatchSearch(100).prepare(brief, getmatch_form)).requests[0]
    assert set(parse_qs(urlsplit(request.url).query)["l"]) == {"moscow", "saints_p", "regions_ru", "remote"}


@pytest.mark.parametrize("country,expected", [("RU", "accepted"), ("DE", "rejected")])
def test_country_selection_accepts_any_city_in_its_country(country, expected):
    filters = SearchFilters(locations=[{"country": "RU", "scope": "country"}])
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://example.org/1",
        locations=[{"country": country, "city": "City absent from local city dataset", "format": "office"}],
    )
    assert evaluate(vacancy, filters).status == expected
