import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from geo_support import confirmed, getmatch_form, with_geography

from src.app import create_app
from src.core.config import Settings
from src.models.brief import BriefInput
from src.parsers.registry import build_sources


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"locations": [{"country": "KZ"}], "minimum_work_format": "office"}, {"relocate", "remote"}),
        (
            {
                "locations": [{"country": "RU", "city": "Moscow", "geoname_id": 524901}],
                "minimum_work_format": "hybrid",
            },
            {"moscow", "remote"},
        ),
        (
            {"locations": [{"country": "RU", "city": "Казань"}], "minimum_work_format": "office"},
            {"regions_ru", "remote"},
        ),
        ({"locations": [{"country": "KZ"}], "minimum_work_format": "remote_country"}, {"remote"}),
        ({"locations": [{"country": "KZ"}], "minimum_work_format": "remote_worldwide"}, {"remote"}),
        ({"relocation_support": True}, set()),
    ],
)
async def test_getmatch_translates_common_conditions_without_confusing_relocation(filters, expected):
    source = build_sources(Settings())["getmatch"]

    brief = BriefInput(name="Shared", queries=["QA", "Python"], filters=filters)
    plan = await source.search.prepare(brief, getmatch_form)
    assert len(plan.requests) == 1
    params = parse_qs(urlsplit(plan.requests[0].url).query)
    assert set(params.get("l", [])) == expected
    assert "sp" not in params
    assert plan.application["relocation_support"] == "local"


async def test_hh_resolves_country_and_city_and_retains_all_query_branches():
    source = build_sources(Settings())["hh_ru"]

    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "40", "name": "Казахстан"}])
        assert parse_qs(urlsplit(url).query)["area_id"] == ["40"]
        return json.dumps(
            {"items": [{"id": "160", "text": "Алматы", "parent": {"id": "40", "text": "Казахстан"}}]}
        )

    brief = BriefInput(
        name="Shared",
        queries=["QA", "Python"],
        filters={"locations": [{"country": "KZ", "city": "Almaty", "geoname_id": 1526384}]},
    )
    plan = await source.search.prepare(brief, fetch)
    params = [parse_qs(urlsplit(r.url).query) for r in plan.requests]
    assert [p["text"] for p in params] == [["QA"]] * 3 + [["Python"]] * 3
    assert {tuple(p.get("area", [])) for p in params} == {("160",), ("40",), ()}
    assert all("search_field" not in p for p in params)


async def test_hh_remote_minimum_does_not_resolve_or_restrict_to_city():
    source = build_sources(Settings())["hh_ru"]

    async def fetch(url):
        assert "/areas/countries" in url
        return json.dumps([{"id": "113", "name": "Россия"}])

    brief = BriefInput(
        name="Remote",
        queries=["QA"],
        filters={
            "locations": [{"country": "RU", "city": "Москва"}],
            "minimum_work_format": "remote_country",
        },
    )
    plan = await source.search.prepare(brief, fetch)
    params = [parse_qs(urlsplit(r.url).query) for r in plan.requests]
    assert {tuple(p.get("area", [])) for p in params} == {("113",), ()}
    assert all(p["work_format"] == ["REMOTE"] for p in params)


async def test_common_brief_roundtrip_and_filtering_before_and_after_details(tmp_path):
    calls = []
    fixture = Path(__file__).parent / "fixtures/getmatch"
    payload = json.loads((fixture / "feed_page1.json").read_text())
    payload["offers"] = [payload["offers"][2], payload["offers"][3]]
    payload["offers"][0]["company"]["name"] = "Excluded"
    payload["meta"]["total"] = 2

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/api/offers":
            return httpx.Response(200, json=payload)
        assert "/vacancies/34499-" in request.url.path
        return httpx.Response(200, text=(fixture / "detail-34499.html").read_text())

    settings = Settings(
        database_path=tmp_path / "search.sqlite",
        sources={"getmatch": {"interval_seconds": 0, "page_size": 10}},
    )
    app = create_app(settings, transport=httpx.MockTransport(with_geography(handler)))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            filters = {"excluded_companies": ["Excluded"], "relocation_support": True}
            response = await c.post(
                "/api/v1/briefs", json={"name": "Common filters", "filters": confirmed(filters)}
            )
            assert response.status_code == 201, response.text
            b = response.json()
            assert b["filters"]["locations"][0]["relocation_support"] is True
            assert "location" not in b
            r = await c.post(
                "/api/v1/runs",
                json={
                    "brief_id": b["id"],
                    "sources": ["getmatch"],
                    "limits": {"getmatch": {"max_items_per_query": 10}},
                },
            )
            assert r.status_code == 202, r.text
            rid = r.json()["id"]
            await asyncio.gather(*app.state.engine.tasks.values())
            rows = (await c.get(f"/api/v1/runs/{rid}/results?selection=all")).json()["items"]
            assert len(calls) == 2  # feed + only the non-excluded detail
            rejected = next(v for v in rows if v["company"]["name"] == "Excluded")
            assert rejected["selection"]["status"] == "rejected"
            assert rejected["detail_status"] == "filtered"
            other = next(v for v in rows if v["id"] == "34499")
            assert other["selection"]["status"] == "accepted"
            assert other["description"]
            run = (await c.get(f"/api/v1/runs/{rid}")).json()
            assert run["status"] == "complete"
            assert run["metrics"]["rejected"] == 1 and run["metrics"]["accepted"] == 1
            events = (await c.get(f"/api/v1/runs/{rid}/events")).json()
            assert any(e["event"] == "filters.evaluated" and e["data"]["stage"] == "detail" for e in events)


async def test_common_country_catalog_has_no_source_ids(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "search.sqlite"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            response = await c.get("/api/v1/geography/countries")
            assert response.status_code == 200
            assert {"code": "KZ", "name": "Kazakhstan"} in response.json()
            assert all("id" not in p for p in response.json())


async def test_hh_preparation_requests_are_in_run_metrics(tmp_path):
    def handler(request):
        if request.url.path == "/areas/countries":
            return httpx.Response(200, json=[{"id": "40", "name": "Казахстан"}])
        assert request.url.params.get("area") == "40" or request.url.params.get("work_format") == "REMOTE"
        return httpx.Response(200, text=(Path(__file__).parent / "fixtures/no_results.html").read_text())

    app = create_app(
        Settings(database_path=tmp_path / "search.sqlite", sources={"hh_ru": {"interval_seconds": 0}}),
        transport=httpx.MockTransport(with_geography(handler)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            b = (
                await c.post(
                    "/api/v1/briefs",
                    json={
                        "name": "Preparation",
                        "queries": ["QA"],
                        "filters": confirmed(
                            {"locations": [{"country": "KZ"}], "minimum_work_format": "remote_country"}
                        ),
                    },
                )
            ).json()
            r = (
                await c.post(
                    "/api/v1/runs",
                    json={"brief_id": b["id"], "sources": ["hh_ru"], "company_profiles": False},
                )
            ).json()
            await asyncio.gather(*app.state.engine.tasks.values())
            run = (await c.get("/api/v1/runs/" + r["id"])).json()
            assert run["status"] == "complete"
            assert run["metrics"]["preparation_requests"] == 1
            assert run["metrics"]["pages"] == 2


async def test_second_chance_controls_hh_soft_parameters_and_worldwide_branch():
    source = build_sources(Settings())["hh_ru"]

    async def fetch(url):
        if "/areas/countries" in url:
            return json.dumps([{"id": "113", "name": "Россия"}])
        return json.dumps(
            {"items": [{"id": "90", "text": "Томск", "parent": {"id": "113", "text": "Россия"}}]}
        )

    brief = BriefInput(
        name="Native",
        queries=["QA"],
        filters={
            "locations": [{"country": "RU", "city": "Tomsk", "geoname_id": 1489425}],
            "minimum_work_format": "hybrid",
            "employment": ["full_time", "part_time"],
            "experience_years": 3,
            "published_after": "2026-10-01",
            "salary": {"minimum": 250000, "currency": "RUB", "period": "month"},
        },
    )
    narrow = await source.search.prepare(brief, fetch)
    params = [parse_qs(urlsplit(r.url).query) for r in narrow.requests]
    assert {tuple(p.get("area", [])) for p in params} == {("90",), ("113",), ()}
    assert next(p for p in params if p.get("area") == ["90"])["work_format"] == ["HYBRID", "REMOTE"]
    assert all(p["employment_form"] == ["FULL", "PART"] for p in params)
    assert all(p["experience"] == ["noExperience", "between1And3", "between3And6"] for p in params)
    assert all("date_from" in p and "salary" not in p and "only_with_salary" not in p for p in params)
    assert narrow.application["minimum_work_format"] == "native"
    broad = await source.search.prepare(brief, fetch, second_chance=True)
    params = [parse_qs(urlsplit(r.url).query) for r in broad.requests]
    assert next(p for p in params if p.get("area") == ["90"]).get("work_format") is None
    assert all("employment_form" not in p and "experience" not in p and "date_from" not in p for p in params)
    assert broad.application["minimum_work_format"] == "local"


async def test_getmatch_native_date_is_broad_and_preserved_on_next_page():
    from datetime import UTC, datetime, timedelta

    source = build_sources(Settings())["getmatch"]

    async def fetch(url):
        raise AssertionError("No preparation HTTP")

    brief = BriefInput(
        name="Date",
        filters={
            "minimum_work_format": "remote_country",
            "published_after": (datetime.now(UTC).date() - timedelta(days=3)).isoformat(),
        },
    )
    plan = await source.search.prepare(brief, fetch)
    params = parse_qs(urlsplit(plan.requests[0].url).query)
    assert params["pa"] == ["7d"]
    assert params["l"] == ["remote"]
    assert params["sa"] == ["any"] and "sp" not in params and "se" not in params
    broad = await source.search.prepare(brief, fetch, second_chance=True)
    assert parse_qs(urlsplit(broad.requests[0].url).query)["pa"] == ["all"]
    assert "l" not in parse_qs(urlsplit(broad.requests[0].url).query)
    payload = json.loads((Path(__file__).parent / "fixtures/getmatch/feed_page1.json").read_text())
    payload["meta"]["limit"] = source.settings.page_size
    next_page = source.search.parse(json.dumps(payload), plan.requests[0]).next_requests[0]
    assert parse_qs(urlsplit(next_page.url).query)["pa"] == ["7d"]
