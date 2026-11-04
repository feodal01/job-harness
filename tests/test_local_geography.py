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
            assert response.json()[0]["geoname_id"] == 524901
            assert response.json()[0]["path"][0] == "Russia"
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


@pytest.mark.parametrize("country,text", [("ZZ", "Москва"), ("RU", "  "), ("RU", "x" * 101)])
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
        ("Москва", "Moscow", "Москва", None, "1"),
        ("Троицк", "Chelyabinsk", "Троицк (Челябинская область)", "Челябинская область", "100"),
        ("Екатеринбург", "Sverdlovsk Oblast", "Екатеринбург", "Свердловская область", "3"),
        ("Казань", "Tatarstan Republic", "Казань", "Татарстан, Республика", "88"),
    ],
)
async def test_local_selection_reaches_native_hh_search(
    query, region, native_name, native_region, expected_id
):
    from urllib.parse import parse_qs, urlsplit

    from src.geography.catalog import default_catalog
    from src.models.brief import BriefInput
    from src.parsers.aggregators.hh.geography import HHGeography
    from src.parsers.aggregators.hh.parser import HHSearch

    selected = next(
        p for p in default_catalog().search("RU", query) if p["name"] == query and p["region"] == region
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
            "locations": [
                {
                    "country": "RU",
                    "geoname_id": selected["geoname_id"],
                    "city": selected["ascii_name"],
                    "region": selected["region"],
                }
            ],
            "minimum_work_format": "office",
        },
    )
    plan = await search.prepare(brief, fetch)
    assert any(parse_qs(urlsplit(r.url).query).get("area") == [expected_id] for r in plan.requests)


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
    from tests.filter_support import evaluate

    result = evaluate(
        Vacancy(
            id="1",
            title="QA",
            url="https://hh.ru/vacancy/1",
            work_formats=["office"],
            locations=[{"country": "RU", "city": actual}],
        ),
        SearchFilters(locations=[{"country": "RU", "city": "Troitsk", "geoname_id": 481608}]),
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
                    # Neither suggestion names its region: GeoNames cannot tell which Troitsk it is.
                    {"id": "1", "text": "Троицк", "parent": {"id": "113", "text": "Россия"}},
                    {"id": "2", "text": "Троицк", "parent": {"id": "113", "text": "Россия"}},
                ]
            }
        )

    search = HHSearch(HHGeography(300))
    for geoname_id in (481608, 1489246):  # Troitsk, Moscow / Troitsk, Chelyabinsk
        brief = BriefInput(
            name="Ambiguous",
            queries=["QA"],
            filters={"locations": [{"country": "RU", "city": "Troitsk", "geoname_id": geoname_id}]},
        )
        with pytest.raises(ValueError, match="не удалось однозначно"):
            await search.prepare(brief, fetch)


def test_one_version_of_each_reference_source_and_catalog_bounds():
    """src/geography/README.md: one CLDR and one GeoNames dataset, nothing else in data/."""
    from pathlib import Path

    from src.geography.catalog import default_catalog

    data = Path(__file__).resolve().parents[1] / "src/geography/data"
    assert {p.name for p in data.iterdir()} == {
        "cities.sqlite",
        "cldr.json.gz",
        "iso3166.json",
        "UNICODE-LICENSE.txt",
    }
    catalog = default_catalog()
    assert catalog.cldr["metadata"]["cldr_version"] == "49"
    assert len(catalog.codes) == 249 and len(catalog.cldr["locales"]) == 463
    assert sorted(catalog.group_countries("EU"))[:3] == ["AT", "BE", "BG"]
    assert len(catalog.search("US", "San")) == 20  # Prefix search, bounded.
    assert catalog.search("RU", "несуществующийгород") == []


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

    from src.geography.catalog import default_catalog
    from src.models.brief import BriefInput
    from src.parsers.aggregators.hh.geography import HHGeography
    from src.parsers.aggregators.hh.parser import HHSearch

    selected = default_catalog().search("RU", city)[0]

    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        # Real HH responses captured read-only on 2026-10-08.
        return (Path(__file__).parent / "fixtures" / fixture_name).read_text()

    brief = BriefInput(
        name="Region spelling",
        queries=["QA"],
        filters={
            "locations": [
                {"country": "RU", "geoname_id": selected["geoname_id"], "city": selected["ascii_name"]}
            ],
        },
    )
    plan = await HHSearch(HHGeography(300)).prepare(brief, fetch)
    assert any(parse_qs(urlsplit(r.url).query).get("area") == [native_id] for r in plan.requests)


@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize("wanted_city,region", [("Троицк", "Москва"), ("Троицк (Москва)", None)])
def test_missing_homonym_region_stays_unknown_including_exclusions(excluded, wanted_city, region):
    from src.models.filters import SearchFilters
    from src.models.vacancy import Vacancy
    from tests.filter_support import evaluate

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


async def test_hh_region_hierarchy_conflicting_with_geonames_is_not_guessed():
    """Known limit for the HH stage: HH lists Troitsk (Moscow) under «Московская область»
    (api.hh.ru/suggests/area_leaves, 2026-11-03) while GeoNames puts it in the city of Moscow.
    The conflicting region is not overridden; the place stays unresolved instead of guessed."""
    import json

    from src.models.brief import BriefInput
    from src.parsers.aggregators.hh.geography import HHGeography
    from src.parsers.aggregators.hh.parser import HHSearch

    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        parent = {"id": "2019", "text": "Московская область", "parent": {"id": "113", "text": "Россия"}}
        return json.dumps({"items": [{"id": "2095", "text": "Троицк (Москва)", "parent": parent}]})

    brief = BriefInput(
        name="Troitsk",
        queries=["QA"],
        filters={"locations": [{"country": "RU", "geoname_id": 481608, "city": "Troitsk"}]},
    )
    with pytest.raises(ValueError, match="не удалось однозначно"):
        await HHSearch(HHGeography(300)).prepare(brief, fetch)
