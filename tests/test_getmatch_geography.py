import asyncio
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from src.app import create_app
from src.core.config import Settings

FIXTURES = Path(__file__).parent / "fixtures/getmatch"


def app_for(tmp_path, handler):
    return create_app(
        Settings(
            database_path=tmp_path / "test.sqlite",
            sources={
                "getmatch": {"interval_seconds": 0, "max_attempts": 1, "page_size": 10},
            },
        ),
        transport=httpx.MockTransport(handler),
    )


async def test_getmatch_region_choices_are_available_without_fake_countries(tmp_path):
    calls = []
    app = app_for(tmp_path, lambda r: calls.append(r) or httpx.Response(500))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            choices = [p.model_dump() for p in app.state.engine.sources["getmatch"].geography.options]
            assert {x["id"] for x in choices} == {"moscow", "saints_p", "regions_ru", "relocate", "remote"}
            assert next(x["name"] for x in choices if x["id"] == "moscow") == "Офис или гибрид в Москве"
            catalog = {s["id"]: s for s in (await c.get("/api/v1/sources")).json()}
            assert catalog["getmatch"]["geography"] == "regions"
            assert catalog["hh_ru"]["geography"] == "country_city"
            assert catalog["getmatch"]["capabilities"]["query_parameters"]["locations"] == "native"
            assert (await c.get("/api/v1/sources/getmatch/countries")).status_code == 404
            assert (await c.get("/api/v1/sources/hh_ru/regions")).status_code == 404
            assert calls == []


async def test_selected_regions_reach_offer_requests_pagination_and_logs(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        assert r.url.path == "/api/offers"
        assert "sp" not in r.url.params
        name = "qa_auto_page2" if r.url.params["offset"] == "10" else "qa_auto"
        return httpx.Response(200, text=(FIXTURES / f"{name}.json").read_text())

    app = app_for(tmp_path, handler)
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        engine.sources["getmatch"] = replace(engine.sources["getmatch"], vacancy_details=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            response = await c.post(
                "/api/v1/briefs",
                json={
                    "name": "Regions",
                    "queries": ["QA Auto"],
                    "filters": {
                        "locations": [{"country": "RU", "city": "Москва"}],
                        "minimum_work_format": "office",
                    },
                },
            )
            assert response.status_code == 201, response.text
            brief = response.json()
            run = await c.post(
                "/api/v1/runs",
                json={
                    "brief_id": brief["id"],
                    "sources": ["getmatch"],
                    "limits": {"getmatch": {"max_items_per_query": 12}},
                },
            )
            assert run.status_code == 202, run.text
            await asyncio.gather(*engine.tasks.values())
            result = (await c.get("/api/v1/runs/" + run.json()["id"])).json()
            assert result["status"] == "complete"
            assert result["request"]["brief"]["filters"]["locations"][0]["city"] == "Москва"
            offers = [r for r in calls if r.url.path == "/api/offers"]
            assert len(offers) == 2
            assert all(r.url.params.get_list("l") == ["moscow", "remote"] for r in offers)
            events = (await c.get("/api/v1/runs/" + run.json()["id"] + "/events")).json()
            mapped = [e["data"] for e in events if e["event"] == "search.mapping"]
            assert len(mapped) == 2 and all(e["parameters"]["l"] == ["moscow", "remote"] for e in mapped)
            assert result["metrics"]["pages"] == 2


@pytest.mark.parametrize(
    "location",
    [
        {"source": "getmatch", "country": {"id": "113", "name": "Россия"}},
        {"source": "getmatch", "options": [{"id": "113", "name": "Россия"}]},
        {"source": "getmatch", "options": [{"id": "remote", "name": "Из любой точки мира"}]},
        {"source": "getmatch", "options": []},
        {"source": "getmatch", "options": [{"id": "remote", "name": "Удалённо"}] * 2},
        {
            "source": "getmatch",
            "country": {"id": "113", "name": "Россия"},
            "options": [{"id": "remote", "name": "Удалённо"}],
        },
        {"source": "hh_ru", "options": [{"id": "remote", "name": "Удалённо"}]},
    ],
)
async def test_invalid_geography_is_rejected_before_http_or_brief_creation(tmp_path, location):
    calls = []
    app = app_for(tmp_path, lambda r: calls.append(r) or httpx.Response(500))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            r = await c.post(
                "/api/v1/briefs", json={"name": "Invalid", "queries": ["QA"], "location": location}
            )
            assert r.status_code == 422, r.text
            assert (await c.get("/api/v1/briefs")).json() == []
            assert calls == []


def test_each_native_region_survives_feed_pagination():
    from src.parsers.aggregators.getmatch.parser import GetmatchSearch
    from src.parsers.models import Location

    for key in ["moscow", "saints_p", "regions_ru", "relocate", "remote"]:
        search = GetmatchSearch(10)
        location = Location(source="getmatch", options=[{"id": key, "name": key}])
        offers = search.build("", location=location)
        next_page = search.parse((FIXTURES / "qa_auto.json").read_text(), offers).next_requests[0]
        assert parse_qs(urlsplit(offers.url).query)["l"] == [key]
        assert parse_qs(urlsplit(next_page.url).query)["l"] == [key]
