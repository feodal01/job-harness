import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

import httpx
import pytest
from geo_support import confirmed, scheduling_sources, with_geography

from src.app import create_app
from src.core.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text()


@asynccontextmanager
async def client_for(tmp_path, handler, **overrides):
    settings = Settings(
        database_path=tmp_path / "test.sqlite",
        sources={"hh_ru": {"interval_seconds": 0, "max_attempts": 1, "breaker_threshold": 10}},
        **overrides,
    )
    app = create_app(settings, transport=httpx.MockTransport(with_geography(handler)))
    async with app.router.lifespan_context(app):
        scheduling_sources(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            yield client


async def launch(client, *, name="Тест", queries=None, max_items=2, companies=False):
    response = await client.post(
        "/api/v1/briefs", json={"name": name, "queries": queries or ["QA"], "filters": confirmed()}
    )
    assert response.status_code == 201
    brief = response.json()
    response = await client.post(
        "/api/v1/runs",
        json={
            "brief_id": brief["id"],
            "source_rules": {"mode": "selected", "include": ["hh_ru"]},
            "limits": {"hh_ru": {"max_items_per_query": max_items}},
            "company_profiles": companies,
        },
    )
    assert response.status_code == 202
    return response.json(), brief


async def wait_run(client, run_id, condition=lambda r: r["status"] not in {"queued", "running"}):
    async with asyncio.timeout(5):
        while True:
            run = (await client.get(f"/api/v1/runs/{run_id}")).json()
            if condition(run):
                return run
            await asyncio.sleep(0.01)


async def test_company_catalog_requires_login_after_restart_and_is_not_a_local_stat(tmp_path):
    for _ in range(2):
        async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
            assert (await client.get("/api/v1/companies")).status_code == 401
            assert "companies" not in (await client.get("/api/v1/stats")).json()


async def test_saved_brief_runs_are_found_beyond_global_history_limit(tmp_path):
    from src.db import Database
    from src.models.brief import BriefInput

    store = Database(tmp_path / "test.sqlite")
    try:
        old_brief = store.briefs.save_brief(BriefInput(name="Старый бриф"))
        empty_brief = store.briefs.save_brief(BriefInput(name="Без поисков"))
        other_brief = store.briefs.save_brief(BriefInput(name="Другой бриф"))
        old_runs = [store.runs.create_run(old_brief["id"], {}, {}) for _ in range(2)]
        for _ in range(101):
            store.runs.create_run(other_brief["id"], {}, {})
    finally:
        store.close()

    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        history = (await client.get("/api/v1/runs")).json()
        assert len(history) == 100
        assert all(r["brief_id"] == other_brief["id"] for r in history)
        response = await client.get("/api/v1/runs", params={"brief_id": old_brief["id"]})
        assert response.status_code == 200
        assert [r["id"] for r in response.json()] == [r["id"] for r in reversed(old_runs)]
        assert (await client.get("/api/v1/runs", params={"brief_id": empty_brief["id"]})).json() == []


@pytest.mark.parametrize("failure", [None, "detail", "company"])
async def test_search_keeps_profiles_in_results_without_company_directory(tmp_path, failure):
    calls = []
    listing = {
        "vacancySearchResult": {
            "totalResults": 2,
            "vacancies": [
                {"vacancyId": v, "name": "QA", "company": {"id": 5174681, "visibleName": "Listing name"}}
                for v in (101, 102)
            ],
        }
    }

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/search/vacancy":
            return httpx.Response(
                200, text='<template id="HH-Lux-InitialState">' + json.dumps(listing) + "</template>"
            )
        if request.url.path.startswith("/employer/"):
            await asyncio.sleep(0.01)
            return httpx.Response(
                503 if failure == "company" else 200, text=fixture("employer_profile_official_site")
            )
        return httpx.Response(503 if failure == "detail" else 200, text=fixture("detail_structured"))

    async with client_for(tmp_path, handler) as client:
        run, _ = await launch(client, companies=True)
        final = await wait_run(client, run["id"])
        assert final["status"] == ("partial" if failure else "complete")
        assert calls.count("/employer/5174681") == 1
        results = (await client.get(f"/api/v1/runs/{run['id']}/results")).json()["items"]
        assert len(results) == 2
        assert all(v["company"]["name"] == "Listing name" for v in results)
        assert all(v["detail_status"] == ("error" if failure == "detail" else "ready") for v in results)
        if failure != "company":
            assert final["metrics"]["companies_ok"] == 1
            assert all(v["company"]["site_url"] == "https://crowd.yandex.ru/vacancies" for v in results)
        else:
            assert final["metrics"]["company_errors"] == 1


async def test_progress_before_details_then_complete_at_normal_cap(tmp_path):
    release = asyncio.Event()

    async def handler(request):
        if request.url.path == "/search/vacancy":
            return httpx.Response(200, text=fixture("success"))
        await release.wait()
        return httpx.Response(200, text=fixture("detail_structured"))

    async with client_for(tmp_path, handler) as client:
        run, _ = await launch(client)
        await wait_run(client, run["id"], lambda r: r["metrics"].get("unique_seen", 0) == 2)
        results = (await client.get(f"/api/v1/runs/{run['id']}/results")).json()["items"]
        assert len(results) == 2
        assert all(r["detail_status"] == "pending" for r in results)
        release.set()
        finished = await wait_run(client, run["id"])
        assert finished["status"] == "complete"
        assert finished["limit_reached"] is True
        assert finished["metrics"]["details_ok"] == 2
        assert finished["metrics"]["pages"] == 1
        assert finished["metrics"]["first_listing_ms"] <= finished["metrics"]["first_detail_ms"]


async def test_rerun_deduplicates_but_retains_each_run_and_brief_snapshot(tmp_path):
    def handler(request):
        return httpx.Response(
            200, text=fixture("success" if request.url.path == "/search/vacancy" else "detail_structured")
        )

    async with client_for(tmp_path, handler) as client:
        first, brief = await launch(client)
        await wait_run(client, first["id"])
        await client.put(
            f"/api/v1/briefs/{brief['id']}",
            json={"name": "Изменён", "queries": ["AQA"], "filters": confirmed()},
        )
        second = (
            await client.post(
                "/api/v1/runs",
                json={
                    "brief_id": brief["id"],
                    "source_rules": {"mode": "selected", "include": ["hh_ru"]},
                    "limits": {"hh_ru": {"max_items_per_query": 2}},
                    "company_profiles": False,
                },
            )
        ).json()
        second = await wait_run(client, second["id"])
        # Cards fetched by the first run are reused, never fetched again.
        assert second["metrics"]["details_reused"] == 2 and second["metrics"]["details_ok"] == 0
        assert (await client.get("/api/v1/stats")).json()["vacancies"] == 2
        first_saved = (await client.get(f"/api/v1/runs/{first['id']}")).json()
        assert first_saved["request"]["brief"]["queries"] == ["QA"]
        assert len((await client.get(f"/api/v1/runs/{second['id']}/results")).json()["items"]) == 2
        second_results = (await client.get(f"/api/v1/runs/{second['id']}/results")).json()["items"]
        assert second_results[0]["is_new"] is False
        assert {r["detail_status"] for r in second_results} == {"reused"}


async def test_one_failed_detail_does_not_drop_other_cards_and_has_http_log(tmp_path):
    def handler(request):
        if request.url.path == "/search/vacancy":
            return httpx.Response(200, text=fixture("success"))
        if request.url.path.endswith("134371846"):
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, text=fixture("detail_structured"))

    async with client_for(tmp_path, handler) as client:
        run, _ = await launch(client)
        final = await wait_run(client, run["id"])
        assert final["status"] == "partial"
        results = (await client.get(f"/api/v1/runs/{run['id']}/results")).json()["items"]
        assert {r["detail_status"] for r in results} == {"ready", "error"}
        events = (await client.get(f"/api/v1/runs/{run['id']}/events")).json()
        assert any(e["data"].get("status_code") == 503 for e in events)
        assert any(e["event"] == "filters.evaluated" for e in events)


async def test_blocked_and_parse_failures_are_visible(tmp_path):
    async with client_for(tmp_path, lambda r: httpx.Response(200, text=fixture("blocked"))) as client:
        run, _ = await launch(client)
        final = await wait_run(client, run["id"])
        assert final["status"] == "failed"
        assert "captcha" in final["error"]
        assert final["sources"]["hh_ru"]["status"] == "blocked"
        assert final["sources"]["hh_ru"]["hint"]
        events = (await client.get(f"/api/v1/runs/{run['id']}/events")).json()
        assert any(e["event"] == "source.blocked" and e["data"]["kind"] == "captcha" for e in events)


async def test_stop_and_deadline_are_terminal_and_keep_listings(tmp_path):
    async def handler(request):
        if request.url.path == "/search/vacancy":
            return httpx.Response(200, text=fixture("success"))
        await asyncio.sleep(10)
        return httpx.Response(200)

    async with client_for(tmp_path, handler, run_timeout_seconds=0.2) as client:
        run, _ = await launch(client)
        final = await wait_run(client, run["id"])
        assert final["status"] == "partial"
        assert "deadline" in final["error"]
        assert len((await client.get(f"/api/v1/runs/{run['id']}/results")).json()["items"]) == 2
        assert all(
            v["detail_status"] != "pending"
            for v in (await client.get(f"/api/v1/runs/{run['id']}/results")).json()["items"]
        )
        other, _ = await launch(client)
        await client.post(f"/api/v1/runs/{other['id']}/cancel")
        assert (await wait_run(client, other["id"]))["status"] == "cancelled"


async def test_shared_resource_limit_for_two_runs(tmp_path):
    starts, active, peak = [], 0, 0

    async def handler(request):
        nonlocal active, peak
        starts.append(monotonic())
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        return httpx.Response(
            200, text=fixture("success" if request.url.path == "/search/vacancy" else "detail_structured")
        )

    settings = Settings(
        database_path=tmp_path / "test.sqlite",
        sources={"hh_ru": {"interval_seconds": 0.02, "concurrency": 2}},
    )
    app = create_app(settings, transport=httpx.MockTransport(with_geography(handler)))
    async with app.router.lifespan_context(app):
        scheduling_sources(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            first, _ = await launch(client)
            second, _ = await launch(client)
            await asyncio.gather(wait_run(client, first["id"]), wait_run(client, second["id"]))
    assert peak <= 2
    # Two listings; the two cards are fetched once and shared by both runs.
    assert len(starts) == 4
    assert min(b - a for a, b in zip(starts, starts[1:], strict=False)) >= 0.018


async def test_restart_marks_unfinished_runs_without_replaying_requests(tmp_path):
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200)

    async with client_for(tmp_path, handler) as client:
        run, _ = await launch(client)
        await wait_run(client, run["id"], lambda r: r["status"] == "running")
    calls = []
    async with client_for(tmp_path, lambda r: calls.append(r)) as client:
        saved = (await client.get(f"/api/v1/runs/{run['id']}")).json()
        assert saved["status"] == "interrupted"
        assert calls == []


async def test_input_validation_and_missing_brief(tmp_path):
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        assert (await client.post("/api/v1/briefs", json={"name": "x", "queries": ["  "]})).status_code == 422
        assert (await client.post("/api/v1/runs", json={"brief_id": "missing"})).status_code == 404
        assert (await client.get("/api/v1/runs/missing")).status_code == 404


async def test_versioned_api_is_documented_by_resource(tmp_path):
    async with client_for(tmp_path, lambda r: httpx.Response(200)) as client:
        assert (await client.get("/api/v1/health")).json()["status"] == "ok"
        assert (await client.get("/api/health")).status_code == 404
        paths = (await client.get("/openapi.json")).json()["paths"]
        expected = {
            "/api/v1/auth": {"get": "auth"},
            "/api/v1/auth/authorize": {"post": "auth"},
            "/api/v1/auth/providers": {"get": "auth"},
            "/api/v1/auth/start": {"post": "auth"},
            "/api/v1/auth/logout": {"post": "auth"},
            "/api/v1/companies": {"get": "companies"},
            "/api/v1/companies/filters": {"get": "companies"},
            "/api/v1/briefs": {"get": "briefs", "post": "briefs"},
            "/api/v1/briefs/{brief_id}": {"put": "briefs"},
            "/api/v1/briefs/{brief_id}/vacancies/{vacancy_key}/adjacent": {"get": "briefs"},
            "/api/v1/briefs/{brief_id}/vacancies": {"get": "briefs"},
            "/api/v1/briefs/{brief_id}/vacancies/{vacancy_key}": {"get": "briefs", "patch": "briefs"},
            "/api/v1/runs": {"get": "runs", "post": "runs"},
            "/api/v1/runs/{run_id}": {"get": "runs"},
            "/api/v1/runs/{run_id}/cancel": {"post": "runs"},
            "/api/v1/runs/{run_id}/results": {"get": "runs"},
            "/api/v1/runs/{run_id}/events": {"get": "runs"},
            "/api/v1/sources/events": {"get": "sources"},
            "/api/v1/sources": {"get": "sources"},
            "/api/v1/sources/counts": {"get": "sources"},
            "/api/v1/sources/preview": {"post": "sources"},
            "/api/v1/sources/search": {"post": "sources"},
            "/api/v1/geography/countries": {"get": "geography"},
            "/api/v1/geography/cities": {"get": "geography"},
            "/api/v1/health": {"get": "system"},
            "/api/v1/stats": {"get": "system"},
            "/api/v1/service": {"get": "service"},
            "/api/v1/service/stop": {"post": "service"},
        }
        assert {p for p in paths if p.startswith("/api/")} == set(expected)
        for path, operations in expected.items():
            for method, tag in operations.items():
                assert paths[path][method]["tags"] == [tag]
