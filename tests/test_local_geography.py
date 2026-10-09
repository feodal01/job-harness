import httpx
import pytest

from src.app import create_app
from src.core.config import Settings


@pytest.mark.parametrize("query", ["Москва", "moscow", "МОСКВА"])
async def test_city_suggestions_work_without_external_requests(tmp_path, query):
    requests = []
    app = create_app(
        Settings(database_path=tmp_path / "test.sqlite", sources={"hh_ru": {"max_attempts": 1}}),
        transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(503)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            response = await client.get("/api/v1/geography/cities", params={"country": "RU", "text": query})
            assert response.status_code == 200, response.text
            assert response.json()[0]["name"] == "Москва"
            assert response.json()[0]["path"][0] == "Россия"
            assert requests == []


async def test_local_geography_does_not_require_hh_adapter(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "test.sqlite"))
    async with app.router.lifespan_context(app):
        del app.state.engine.sources["hh_ru"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            response = await client.get(
                "/api/v1/geography/cities", params={"country": "RU", "text": "Троицк"}
            )
            assert response.status_code == 200
            places = [p for p in response.json() if p["name"] == "Троицк"]
            assert len({p["region"] for p in places}) >= 2
            assert all("id" not in p for p in places)


@pytest.mark.parametrize("country,text", [("ZZ", "Москва"), ("RU", "  "), ("RU", "М")])
async def test_invalid_local_geography_query_is_rejected(tmp_path, country, text):
    app = create_app(Settings(database_path=tmp_path / "test.sqlite"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            response = await client.get("/api/v1/geography/cities", params={"country": country, "text": text})
            assert response.status_code == 422


@pytest.mark.parametrize(
    "query,region,native_name,native_region,expected_id",
    [
        ("Москва", None, "Москва", None, "1"),
        ("Троицк", "Москва", "Троицк (Москва)", "Московская область", "2095"),
        ("Троицк", "Челябинская", "Троицк (Челябинская область)", "Челябинская область", "100"),
        ("Екатеринбург", "Свердловская Область", "Екатеринбург", "Свердловская область", "3"),
        ("Казань", "Республика Татарстан", "Казань", "Татарстан, Республика", "88"),
    ],
)
async def test_local_selection_reaches_native_hh_search(
    query, region, native_name, native_region, expected_id
):
    from urllib.parse import parse_qs, urlsplit

    from src.geography.directory import CityDirectory
    from src.models.brief import BriefInput
    from src.parsers.aggregators.hh.geography import HHGeography
    from src.parsers.aggregators.hh.parser import HHSearch

    selected = next(
        p for p in CityDirectory().search("RU", query) if p["name"] == query and p["region"] == region
    )
    search = HHSearch(HHGeography(300))

    async def fetch(url):
        import json

        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        parent = {"id": "113", "text": "Россия"}
        if native_region:
            parent = {"id": "region", "text": native_region, "parent": parent}
        return json.dumps({"items": [{"id": expected_id, "text": native_name, "parent": parent}]})

    brief = BriefInput(
        name="Local city",
        queries=["QA"],
        filters={
            "locations": [{"country": "RU", "city": selected["name"], "region": selected["region"]}],
            "minimum_work_format": "office",
        },
    )
    plan = await search.prepare(brief, fetch)
    assert any(parse_qs(urlsplit(r.url).query).get("area") == [expected_id] for r in plan.requests)
    # Legacy briefs containing HH's qualifier remain supported as well.
    if "(" in native_name:
        brief.filters.locations[0].city = native_name
        brief.filters.locations[0].region = native_region
        assert (await search.prepare(brief, fetch)).requests


@pytest.mark.parametrize(
    "actual,expected",
    [
        ("Троицк (Москва)", "accepted"),
        ("Троицк (Челябинская область)", "rejected"),
    ],
)
def test_common_city_filter_respects_native_region_qualifier(actual, expected):
    from src.models.filters import SearchFilters
    from src.models.vacancy import Vacancy
    from src.selection.filtering import evaluate

    result = evaluate(
        Vacancy(
            id="1",
            title="QA",
            url="https://hh.ru/vacancy/1",
            work_formats=["office"],
            locations=[{"country": "RU", "city": actual}],
        ),
        SearchFilters(locations=[{"country": "RU", "city": "Троицк", "region": "Москва"}]),
    )
    assert next(check for check in result.checks if check.field == "locations").status == expected


async def test_hh_does_not_guess_between_homonymous_cities():
    import json

    from src.models.brief import BriefInput
    from src.parsers.aggregators.hh.geography import HHGeography
    from src.parsers.aggregators.hh.parser import HHSearch

    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        return json.dumps(
            {
                "items": [
                    {"id": "1", "text": "Троицк (Москва)", "parent": {"id": "113", "text": "Россия"}},
                    {
                        "id": "2",
                        "text": "Троицк (Челябинская область)",
                        "parent": {"id": "113", "text": "Россия"},
                    },
                ]
            }
        )

    search = HHSearch(HHGeography(300))
    for region in (None, "Омская область"):
        brief = BriefInput(
            name="Ambiguous",
            queries=["QA"],
            filters={
                "locations": [{"country": "RU", "city": "Троицк", "region": region}],
            },
        )
        with pytest.raises(ValueError, match="не удалось однозначно"):
            await search.prepare(brief, fetch)


def test_bundled_directory_integrity_coverage_and_result_bound():
    import hashlib
    import json
    from pathlib import Path

    from src.geography.countries import COUNTRIES
    from src.geography.directory import CityDirectory

    data = Path(__file__).resolve().parents[1] / "src/geography/data"
    metadata = json.loads((data / "metadata.json").read_text())
    assert hashlib.sha256((data / "cities.json.gz").read_bytes()).hexdigest() == metadata["snapshot_sha256"]
    directory = CityDirectory()
    assert set(directory._countries) == set(COUNTRIES)
    assert all(directory._countries.values())
    assert sum(map(len, directory._countries.values())) == metadata["cities"]
    assert len(directory.search("US", "ville")) == 20
    assert directory.search("RU", "несуществующийгород") == []
    assert directory.search("RU", "орел")[0]["name"] == directory.search("RU", "Орёл")[0]["name"]


@pytest.mark.parametrize(
    "city,fixture_name,native_id",
    [
        ("Кемерово", "hh_city_kemerovo.json", "47"),
        ("Ижевск", "hh_city_izhevsk.json", "96"),
    ],
)
async def test_local_city_resolves_actual_hh_region_spelling(city, fixture_name, native_id):
    import json
    from pathlib import Path
    from urllib.parse import parse_qs, urlsplit

    from src.geography.directory import CityDirectory
    from src.models.brief import BriefInput
    from src.parsers.aggregators.hh.geography import HHGeography
    from src.parsers.aggregators.hh.parser import HHSearch

    selected = CityDirectory().search("RU", city)[0]

    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        # Real HH responses captured read-only on 2026-10-08.
        return (Path(__file__).parent / "fixtures" / fixture_name).read_text()

    brief = BriefInput(
        name="Region spelling",
        queries=["QA"],
        filters={
            "locations": [{"country": "RU", "city": selected["name"], "region": selected["region"]}],
        },
    )
    plan = await HHSearch(HHGeography(300)).prepare(brief, fetch)
    assert any(parse_qs(urlsplit(r.url).query).get("area") == [native_id] for r in plan.requests)


@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize("wanted_city,region", [("Троицк", "Москва"), ("Троицк (Москва)", None)])
def test_missing_homonym_region_stays_unknown_including_exclusions(excluded, wanted_city, region):
    from src.models.filters import SearchFilters
    from src.models.vacancy import Vacancy
    from src.selection.filtering import evaluate

    result = evaluate(
        Vacancy(
            id="1",
            title="QA",
            url="https://hh.ru/vacancy/1",
            work_formats=["office"],
            locations=[{"country": "RU", "city": "Троицк", "excluded": excluded}],
        ),
        SearchFilters(locations=[{"country": "RU", "city": wanted_city, "region": region}]),
    )
    assert next(c for c in result.checks if c.field == "locations").status == "unknown"
