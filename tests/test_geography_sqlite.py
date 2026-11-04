"""Public geography and confirmed city storage, using the packaged dataset."""

from pathlib import Path

import httpx
import pytest

from src.app import create_app
from src.core.config import Settings


@pytest.fixture
async def client(tmp_path):
    app = create_app(
        Settings(database_path=tmp_path / "user.sqlite"),
        transport=httpx.MockTransport(lambda r: pytest.fail(f"Unexpected network: {r.url}")),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as api:
            yield api


async def test_complete_country_catalog_is_english_by_default(client):
    response = await client.get("/api/v1/geography/countries", headers={"Accept-Language": "ru"})
    assert response.status_code == 200
    countries = {c["code"]: c["name"] for c in response.json()}
    assert len(countries) == 249
    assert countries["DE"] == "Germany"
    assert countries["JP"] == "Japan"
    for locale, name in [("ru", "Германия"), ("de", "Deutschland")]:
        r = await client.get("/api/v1/geography/countries", params={"locale": locale})
        assert r.status_code == 200
        assert next(c for c in r.json() if c["code"] == "DE")["name"] == name
    assert (await client.get("/api/v1/geography/countries?locale=not-a-locale")).status_code == 422


@pytest.mark.parametrize(
    "country,query,gid,ascii_name",
    [
        ("RU", "санкт петербург", 498817, "Saint Petersburg"),
        ("RU", "Москва", 524901, "Moscow"),
        ("DE", "München", 2867714, "Munich"),
        ("TH", "กรุงเทพมหานคร", 1609350, "Bangkok"),
        ("JP", "東京", 1850147, "Tokyo"),
        ("GE", "თბილისი", 611717, "Tbilisi"),
    ],
)
async def test_multilingual_names_and_canonical_identity(client, country, query, gid, ascii_name):
    r = await client.get("/api/v1/geography/cities", params={"country": country, "text": query})
    assert r.status_code == 200
    city = r.json()[0]
    assert city["geoname_id"] == gid
    assert city["country"] == country
    assert city["ascii_name"] == ascii_name
    assert city["name"].casefold().startswith(query.casefold())
    assert len({c["geoname_id"] for c in r.json()}) == len(r.json())


@pytest.mark.parametrize("locations", [[], [{"country": "RU"}], [{"country": "RU", "city": "Москва"}]])
async def test_unconfirmed_city_cannot_be_saved(client, locations):
    r = await client.post("/api/v1/briefs", json={"name": "Invalid", "filters": {"locations": locations}})
    assert r.status_code == 422


async def test_save_resolves_ascii_on_server_and_keeps_display_name(client):
    place = {
        "country": "RU",
        "geoname_id": 524901,
        "city_display_name": "Москва",
        "city": "client forged name",
        "country_name_en": "client forged country",
    }
    r = await client.post(
        "/api/v1/briefs", json={"name": "Verified", "queries": ["QA"], "filters": {"locations": [place]}}
    )
    assert r.status_code == 201, r.text
    saved = r.json()["filters"]["locations"][0]
    assert saved["city"] == "Moscow"
    assert saved["country_name_en"] == "Russia"
    assert saved["city_display_name"] == "Москва"
    assert saved["geoname_id"] == 524901
    reread = (await client.get("/api/v1/briefs")).json()[0]
    assert reread["filters"]["locations"][0] == saved


@pytest.mark.parametrize(
    "country,gid,label", [("DE", 524901, "Москва"), ("RU", 524901, "Invented"), ("RU", 999999999, "Москва")]
)
async def test_forged_city_selection_is_rejected(client, country, gid, label):
    r = await client.post(
        "/api/v1/briefs",
        json={
            "name": "Invalid",
            "filters": {"locations": [{"country": country, "geoname_id": gid, "city_display_name": label}]},
        },
    )
    assert r.status_code == 422


async def test_one_ideograph_is_a_valid_prefix(client):
    r = await client.get("/api/v1/geography/cities", params={"country": "JP", "text": "東"})
    assert r.status_code == 200
    assert any(c["geoname_id"] == 1850147 for c in r.json())


def test_database_is_a_packaged_readonly_resource():
    path = Path(__file__).resolve().parents[1] / "src/geography/data/cities.sqlite"
    assert path.is_file()


async def test_old_brief_is_readable_but_requires_confirmed_city_on_edit(client):
    app = client._transport.app
    from src.models.brief import BriefInput

    legacy = app.state.database.briefs.save_brief(
        BriefInput(name="Old", filters={"locations": [{"country": "RU", "city": "Москва"}]})
    )
    r = await client.get("/api/v1/briefs")
    assert r.status_code == 200
    assert next(b for b in r.json() if b["id"] == legacy["id"])["filters"]["locations"][0]["city"] == "Москва"
    r = await client.put(
        f"/api/v1/briefs/{legacy['id']}",
        json={"name": "Old", "filters": {"locations": [{"country": "RU", "city": "Москва"}]}},
    )
    assert r.status_code == 422


def test_ascii_selection_matches_native_city_facts():
    from src.models.filters import SearchFilters
    from src.models.vacancy import Vacancy
    from tests.filter_support import evaluate

    filters = SearchFilters(locations=[{"country": "RU", "geoname_id": 524901, "city": "Moscow"}])
    vacancy = Vacancy(
        id="1",
        title="QA",
        url="https://example.org/1",
        work_formats=["office"],
        locations=[{"country": "RU", "city": "Москва"}],
    )
    result = evaluate(vacancy, filters)
    assert next(c for c in result.checks if c.field == "locations").status == "accepted"


async def test_ascii_selection_resolves_hh_city_without_losing_id():
    import json
    from urllib.parse import parse_qs, urlsplit

    from src.models.brief import BriefInput
    from src.parsers.aggregators.hh.parser import HHSearch

    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        assert parse_qs(urlsplit(url).query)["text"] == ["Москва"]
        return json.dumps(
            {"items": [{"id": "1", "text": "Москва", "parent": {"id": "113", "text": "Россия"}}]}
        )

    brief = BriefInput(
        name="Canonical",
        queries=["QA"],
        filters={
            "locations": [{"country": "RU", "city": "Moscow", "geoname_id": 524901}],
            "minimum_work_format": "office",
        },
    )
    plan = await HHSearch().prepare(brief, fetch)
    assert any(parse_qs(urlsplit(r.url).query).get("area") == ["1"] for r in plan.requests)


def test_city_missing_from_suggestion_dataset_is_not_automatic_rejection():
    from src.geography.catalog import default_catalog

    assert default_catalog().matches(524901, "A place absent from cities15000") is None


async def test_old_unconfirmed_brief_cannot_start_without_reselection(client):
    app = client._transport.app
    from src.models.brief import BriefInput

    old = app.state.database.briefs.save_brief(BriefInput(name="Old", queries=["QA"]))
    r = await client.post("/api/v1/runs", json={"brief_id": old["id"], "sources": ["hh_ru"]})
    assert r.status_code == 422


async def test_five_places_are_allowed_and_duplicates_and_sixth_are_rejected(client):
    places = [
        {"country": country, "geoname_id": gid, "city_display_name": name}
        for country, gid, name in [
            ("RU", 524901, "Москва"),
            ("RU", 498817, "Санкт-Петербург"),
            ("DE", 2867714, "München"),
            ("JP", 1850147, "東京"),
            ("TH", 1609350, "กรุงเทพมหานคร"),
            ("GE", 611717, "თბილისი"),
        ]
    ]
    for selections, status in [(places[:5], 201), (places, 422), ([places[0], places[0]], 422)]:
        r = await client.post("/api/v1/briefs", json={"name": "Limits", "filters": {"locations": selections}})
        assert r.status_code == status, r.text


def test_readonly_database_and_complete_canonical_names():
    import sqlite3
    from contextlib import closing

    from src.geography.catalog import default_catalog

    with closing(default_catalog().connect()) as c:
        assert all(r[0] and r[0].isascii() for r in c.execute("SELECT ascii_name FROM cities"))
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            c.execute("DELETE FROM cities")


@pytest.mark.parametrize(
    "gid,actual,regions,expected",
    [
        (463343, "Железногорск (Красноярский край)", (), False),
        (524901, "Москва (Тверская область)", (), False),
        (471456, "Востряково", ("Россия", "Московская область"), None),
        (471457, "Востряково", ("Россия", "Московская область"), None),
    ],
)
def test_region_evidence_must_not_conflict_or_hide_city_ambiguity(gid, actual, regions, expected):
    from src.geography.catalog import default_catalog

    assert default_catalog().matches(gid, actual, regions) is expected
