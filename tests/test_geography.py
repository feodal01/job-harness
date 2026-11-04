import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from geo_support import confirmed

from src.app import create_app
from src.core.config import Settings
from src.network.client import FetchError

FIXTURES = Path(__file__).parent / "fixtures"


async def test_dynamic_countries_city_selection_cache_and_search(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/areas/countries":
            return httpx.Response(200, json=json.loads((FIXTURES / "hh_countries.json").read_text()))
        if request.url.path == "/suggests/area_leaves":
            return httpx.Response(200, json=json.loads((FIXTURES / "hh_city_suggestions.json").read_text()))
        return httpx.Response(200, text=(FIXTURES / "no_results.html").read_text())

    app = create_app(
        Settings(database_path=tmp_path / "test.sqlite", sources={"hh_ru": {"interval_seconds": 0}}),
        transport=httpx.MockTransport(handler),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            geo = app.state.engine.sources["hh_ru"].geography
            fetch = app.state.engine.catalog_fetch("hh_ru")
            countries = await geo.countries(fetch)
            country = next(x for x in countries if x.name == "Россия")
            assert country.id == "113"
            cities = await geo.cities(fetch, "113", "Москва")
            city = next(x for x in cities if x.name == "Москва")
            assert city.id == "1" and "Россия" in city.path
            await geo.cities(fetch, "113", "Москва")
            assert len(requests) == 2
            assert requests[1].url.params["area_id"] == "113"
            assert "host" not in requests[1].url.params
            payload = {
                "name": "Город",
                "queries": ["QA"],
                "filters": confirmed(),
            }
            response = await c.post("/api/v1/briefs", json=payload)
            assert response.status_code == 201
            brief = response.json()
            assert brief["filters"]["locations"][0]["city"] == "Moscow"
            invalid_source = await c.post(
                "/api/v1/runs", json={"brief_id": brief["id"], "sources": ["unknown"]}
            )
            assert invalid_source.status_code == 422
            wrong = {**payload, "filters": {"locations": [{"country": "ZZ"}]}}
            assert (await c.post("/api/v1/briefs", json=wrong)).status_code == 422
            assert len((await c.get("/api/v1/briefs")).json()) == 1
            run = await c.post("/api/v1/runs", json={"brief_id": brief["id"], "sources": ["hh_ru"]})
            assert run.status_code == 202
            await asyncio.gather(*app.state.engine.tasks.values())
            plans = [
                parse_qs(urlsplit(str(r.url)).query) for r in requests if r.url.path == "/search/vacancy"
            ]
            assert any(p.get("area") == ["1"] for p in plans)
            assert any(p.get("area") == ["113"] and p.get("work_format") == ["REMOTE"] for p in plans)
            assert any("area" not in p and p.get("work_format") == ["REMOTE"] for p in plans)
            assert len(requests) == 5
            events = (await c.get("/api/v1/sources/events")).json()
            assert any(e["event"] == "http.finished" and e["data"]["status_code"] == 200 for e in events)


async def test_geography_failure_and_invalid_selection_are_explicit(tmp_path):
    app = create_app(
        Settings(
            database_path=tmp_path / "test.sqlite",
            sources={"hh_ru": {"interval_seconds": 0, "max_attempts": 1}},
        ),
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            geo = app.state.engine.sources["hh_ru"].geography
            fetch = app.state.engine.catalog_fetch("hh_ru")
            with pytest.raises(FetchError):
                await geo.countries(fetch)
            with pytest.raises(ValueError, match="два символа"):
                await geo.cities(fetch, "113", "М")
            assert (await c.get("/api/v1/sources/hh_ru/countries")).status_code == 404
            response = await c.post(
                "/api/v1/briefs",
                json={
                    "name": "Bad",
                    "queries": ["QA"],
                    "location": {
                        "source": "hh_ru",
                        "country": {"id": "113", "name": "Россия"},
                        "city": "Москва",
                    },
                },
            )
            assert response.status_code == 422
            assert (await c.get("/api/v1/briefs")).json() == []


async def test_concurrent_catalog_requests_coalesce_and_failed_parse_is_not_cached(tmp_path):
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        if len(calls) == 1:
            return httpx.Response(200, json={"unexpected": "response"})
        return httpx.Response(200, json=json.loads((FIXTURES / "hh_countries.json").read_text()))

    app = create_app(
        Settings(database_path=tmp_path / "test.sqlite", sources={"hh_ru": {"interval_seconds": 0}}),
        transport=httpx.MockTransport(handler),
    )
    async with app.router.lifespan_context(app):
        geo = app.state.engine.sources["hh_ru"].geography
        fetch = app.state.engine.catalog_fetch("hh_ru")
        with pytest.raises(FetchError) as exc:
            await geo.countries(fetch)
        assert exc.value.kind == "parse_error"
        responses = await asyncio.gather(geo.countries(fetch), geo.countries(fetch))
        assert all(any(p.name == "Россия" for p in response) for response in responses)
        assert len(calls) == 2
        for key, (_, value) in list(geo.cache.items()):
            geo.cache[key] = (0, value)
        assert await geo.countries(fetch)
        assert len(calls) == 3


async def test_catalog_and_search_share_same_hh_request_limit(tmp_path):
    active, peak = 0, 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.02)
        active -= 1
        if request.url.path == "/areas/countries":
            return httpx.Response(200, json=json.loads((FIXTURES / "hh_countries.json").read_text()))
        return httpx.Response(200, text=(FIXTURES / "no_results.html").read_text())

    app = create_app(
        Settings(
            database_path=tmp_path / "test.sqlite",
            sources={"hh_ru": {"interval_seconds": 0, "concurrency": 1}},
        ),
        transport=httpx.MockTransport(handler),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            brief = (
                await c.post(
                    "/api/v1/briefs", json={"name": "Budget", "queries": ["QA"], "filters": confirmed()}
                )
            ).json()
            catalog, search = await asyncio.gather(
                app.state.engine.sources["hh_ru"].geography.countries(
                    app.state.engine.catalog_fetch("hh_ru")
                ),
                c.post("/api/v1/runs", json={"brief_id": brief["id"], "sources": ["hh_ru"]}),
            )
            assert catalog and search.status_code == 202
            await asyncio.gather(*app.state.engine.tasks.values())
            assert peak == 1
