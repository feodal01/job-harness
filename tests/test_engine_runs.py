"""Spec v2 step 2 runs: phrases, origins, reuse across runs and briefs, pool, parallel sources."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from geo_support import confirmed, scheduling_sources, with_geography

from src.app import create_app
from src.core.config import Settings
from src.db import Database
from src.engine.filter_stage import FilterStage
from src.engine.models import Clock
from src.engine.recorder import SourceRecorder
from src.models.brief import BriefInput
from src.models.filters import SearchFilters
from src.models.vacancy import Vacancy

FIXTURES = Path(__file__).parent / "fixtures"
GETMATCH_OFFER = json.loads((FIXTURES / "getmatch/feed_page1.json").read_text())["offers"][2]


def hh_listing(ids: list[int], page: int = 0, pages: int = 1) -> str:
    result = {
        "totalResults": len(ids) * pages,
        "vacancies": [
            {"vacancyId": v, "name": f"QA {v}", "company": {"id": 100 + v % 2, "visibleName": f"Co {v % 2}"}}
            for v in ids
        ],
        "paging": {"next": {"page": page + 1, "disabled": page + 1 >= pages}},
    }
    return '<template id="HH-Lux-InitialState">' + json.dumps({"vacancySearchResult": result}) + "</template>"


def getmatch_listing(request: httpx.Request, total: int = 3) -> str:
    offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
    offers = [
        {**GETMATCH_OFFER, "id": 7000 + n, "url": f"/vacancies/{7000 + n}-qa", "offer_type": "vacancy"}
        for n in range(offset, min(offset + limit, total))
    ]
    return json.dumps({"offers": offers, "meta": {"total": total, "offset": offset, "limit": limit}})


class Recorder:
    """Fake HH and Getmatch; listing ids per phrase are configurable, every request is recorded."""

    def __init__(self, phrases: dict[str, list[int]], pages: int = 1) -> None:
        self.phrases, self.pages, self.calls = phrases, pages, []
        self.hh_status = 200

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        await asyncio.sleep(0.005)
        path = request.url.path
        if request.url.host == "getmatch.ru":
            if path == "/api/offers":
                return httpx.Response(200, text=getmatch_listing(request))
            return httpx.Response(
                200,
                text=(FIXTURES / "getmatch/detail-34499.html")
                .read_text()
                .replace("34499", path.split("/")[2].split("-")[0]),
            )
        if self.hh_status != 200:
            return httpx.Response(200, text=(FIXTURES / "blocked.html").read_text())
        if path == "/search/vacancy":
            params = parse_qs(urlsplit(str(request.url)).query)
            page = int(params["page"][0])
            ids = [v + page * 1000 for v in self.phrases[params["text"][0]]]
            return httpx.Response(200, text=hh_listing(ids, page, self.pages))
        if path.startswith("/employer/"):
            return httpx.Response(200, text=(FIXTURES / "employer_profile_official_site.html").read_text())
        return httpx.Response(200, text=(FIXTURES / "detail_structured.html").read_text())

    def paths(self, prefix: str) -> list[str]:
        return [r.url.path for r in self.calls if r.url.path.startswith(prefix)]

    def listing_texts(self) -> list[tuple[str, str]]:
        return [
            (r.url.params["text"], r.url.params["page"])
            for r in self.calls
            if r.url.path == "/search/vacancy"
        ]


@asynccontextmanager
async def service(tmp_path, fake):
    settings = Settings(
        database_path=tmp_path / "test.sqlite",
        sources={
            "hh_ru": {"interval_seconds": 0, "max_attempts": 1},
            "getmatch": {"interval_seconds": 0, "max_attempts": 1, "page_size": 10},
        },
    )
    app = create_app(settings, transport=httpx.MockTransport(with_geography(fake)))
    async with app.router.lifespan_context(app):
        scheduling_sources(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            yield app, client


async def brief(client, queries, filters=None, name="Бриф"):
    body = {"name": name, "queries": queries, "filters": confirmed(filters)}
    response = await client.post("/api/v1/briefs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def run(app, client, brief_id, sources=("hh_ru",), **body):
    response = await client.post(
        "/api/v1/runs", json={"brief_id": brief_id, "sources": list(sources), **body}
    )
    assert response.status_code == 202, response.text
    await asyncio.gather(*app.state.engine.tasks.values())
    return (await client.get(f"/api/v1/runs/{response.json()['id']}")).json()


async def test_every_phrase_is_walked_in_turns_under_its_own_limit(tmp_path):
    fake = Recorder({"QA": [1, 2, 3], "AQA": [11, 12, 13], "тестировщик": [21, 22, 23]}, pages=2)
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, ["QA", "AQA", "тестировщик"])
        result = await run(
            app,
            client,
            b["id"],
            company_profiles=False,
            limits={"hh_ru": {"max_items_per_query": 5, "max_pages_per_query": 2}},
        )
    assert result["status"] == "complete"
    # First pages of all phrases before anyone's second page.
    assert fake.listing_texts() == [
        ("QA", "0"),
        ("AQA", "0"),
        ("тестировщик", "0"),
        ("QA", "1"),
        ("AQA", "1"),
        ("тестировщик", "1"),
    ]
    queries = result["sources"]["hh_ru"]["metrics"]["queries"]
    assert {q: v["new"] for q, v in queries.items()} == {"QA": 5, "AQA": 5, "тестировщик": 5}
    assert result["limit_reached"] is True


async def test_vacancy_found_by_two_phrases_has_one_card_and_two_origins(tmp_path):
    fake = Recorder({"QA": [1, 2], "AQA": [2, 3]})
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, ["QA", "AQA"])
        result = await run(app, client, b["id"], company_profiles=False)
        page = (await client.get(f"/api/v1/runs/{result['id']}/results")).json()
        rows = {r["id"]: r for r in page["items"]}
    assert sorted(fake.paths("/vacancy/")) == ["/vacancy/1", "/vacancy/2", "/vacancy/3"]
    assert [o["query"] for o in rows["2"]["origins"]] == ["QA", "AQA"]
    assert [o["query"] for o in rows["3"]["origins"]] == ["AQA"]
    assert rows["2"]["origins"][0]["page"] == 1


async def test_next_run_and_other_brief_reuse_cards_and_profiles_without_requests(tmp_path):
    fake = Recorder({"QA": [1, 2]})
    async with service(tmp_path, fake) as (app, client):
        first_brief = await brief(client, ["QA"])
        first = await run(app, client, first_brief["id"], company_profiles=True)
        assert first["metrics"]["details_ok"] == 2 and first["metrics"]["companies_ok"] == 2
        fetched = len(fake.calls)

        fake.phrases["QA"] = [1, 2, 3]  # One new vacancy appeared in the feed.
        second = await run(app, client, first_brief["id"], company_profiles=True)
        new_calls = [r.url.path for r in fake.calls[fetched:]]
        assert new_calls.count("/search/vacancy") == 1  # The feed is walked again.
        assert [p for p in new_calls if p.startswith("/vacancy/")] == ["/vacancy/3"]
        assert not any(p.startswith("/employer/") for p in new_calls)  # Both employers are known.
        assert second["metrics"]["details_reused"] == 2 and second["metrics"]["companies_reused"] == 2

        fetched = len(fake.calls)
        other = await brief(client, ["QA"], {"excluded_companies": ["Co 1"]}, name="Другой бриф")
        third = await run(app, client, other["id"], company_profiles=True)
        assert [r.url.path for r in fake.calls[fetched:]] == ["/search/vacancy"]
        assert third["metrics"]["details_reused"] == 3 - third["metrics"]["prefiltered"]
        pool = (await client.get(f"/api/v1/briefs/{other['id']}/vacancies?selection=all")).json()["items"]
        assert {v["id"]: v["selection"]["status"] for v in pool}["1"] == "rejected"  # Its own decision.
        first_pool = (await client.get(f"/api/v1/briefs/{first_brief['id']}/vacancies?selection=all")).json()[
            "items"
        ]
        assert {v["id"]: v["selection"]["status"] for v in first_pool}["1"] != "rejected"


async def test_pool_keeps_user_status_and_recomputes_decisions_without_network(tmp_path):
    fake = Recorder({"QA": [1, 2]})
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, ["QA"])
        await run(app, client, b["id"], company_profiles=False)
        pool = (await client.get(f"/api/v1/briefs/{b['id']}/vacancies?selection=all")).json()["items"]
        assert {v["user_status"] for v in pool} == {"new"}
        key = next(v["key"] for v in pool if v["id"] == "1")
        response = await client.patch(
            f"/api/v1/briefs/{b['id']}/vacancies/{key}", json={"status": "applied", "note": "Откликнулся"}
        )
        assert response.status_code == 200 and response.json()["user_status"] == "applied"
        assert (
            await client.patch(
                f"/api/v1/briefs/{b['id']}/vacancies/hh_ru:missing", json={"status": "applied"}
            )
        ).status_code == 404

        await run(app, client, b["id"], company_profiles=False)
        pool = {
            v["id"]: v
            for v in (await client.get(f"/api/v1/briefs/{b['id']}/vacancies?selection=all")).json()["items"]
        }
        assert pool["1"]["user_status"] == "applied" and pool["1"]["note"] == "Откликнулся"
        assert len(pool["1"]["origins"]) == 2  # Found again by the second run.

        calls = len(fake.calls)
        changed = {"name": "Бриф", "queries": ["QA"], "filters": confirmed({"excluded_text": ["QA 2"]})}
        assert (await client.put(f"/api/v1/briefs/{b['id']}", json=changed)).status_code == 200
        pool = {
            v["id"]: v
            for v in (await client.get(f"/api/v1/briefs/{b['id']}/vacancies?selection=all")).json()["items"]
        }
        assert pool["2"]["selection"]["status"] == "rejected"
        assert len(fake.calls) == calls


async def test_sources_run_in_parallel_and_one_blocked_source_does_not_stop_others(tmp_path):
    fake = Recorder({"QA": [1, 2]})
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, ["QA"])
        both = await run(app, client, b["id"], sources=("hh_ru", "getmatch"), company_profiles=False)
        assert both["status"] == "complete"
        assert {s: v["status"] for s, v in both["sources"].items()} == {
            "hh_ru": "complete",
            "getmatch": "complete",
        }
        hosts = [r.url.host for r in fake.calls]
        assert hosts.index("getmatch.ru") < len(hosts) - 1 - hosts[::-1].index(
            "hh.ru"
        )  # Interleaved in time.

        fake.hh_status = 503  # HH now answers with a captcha page.
        fake.phrases["QA"] = [5]
        blocked = await run(app, client, b["id"], sources=("hh_ru", "getmatch"), company_profiles=False)
        assert blocked["status"] == "partial"
        assert blocked["sources"]["hh_ru"]["status"] == "blocked"
        assert blocked["sources"]["hh_ru"]["blocked_reason"] == "captcha"
        assert blocked["sources"]["getmatch"]["status"] == "complete"
        sources = {s["id"]: s for s in (await client.get("/api/v1/sources")).json()}
        assert sources["hh_ru"]["state"]["status"] == "blocked" and sources["hh_ru"]["type"] == "aggregator"
        assert sources["getmatch"]["state"]["status"] == "ready"


async def test_source_without_query_is_skipped_when_others_can_run(tmp_path):
    fake = Recorder({})
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, [])
        result = await run(app, client, b["id"], sources=("hh_ru", "getmatch"))
    assert result["sources"]["hh_ru"]["status"] == "skipped"
    assert result["sources"]["getmatch"]["status"] == "complete"
    assert result["status"] == "complete"
    assert {r.url.host for r in fake.calls} == {"getmatch.ru"}


async def test_second_chance_is_frozen_in_run_and_changes_only_native_filters(tmp_path):
    fake = Recorder({"QA": [1]})
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, ["QA"], filters={"minimum_work_format": "hybrid", "experience_years": 3})
        first = await run(app, client, b["id"], company_profiles=False)
        first_request = next(r for r in fake.calls if r.url.path == "/search/vacancy")
        assert first_request.url.params.get_list("work_format") == ["HYBRID", "REMOTE"]
        assert first["request"]["second_chance"] is False
        fake.calls.clear()
        second = await run(app, client, b["id"], company_profiles=False, second_chance=True)
        second_request = next(r for r in fake.calls if r.url.path == "/search/vacancy")
        assert (
            "work_format" not in second_request.url.params and "experience" not in second_request.url.params
        )
        assert second["request"]["second_chance"] is True
        assert (await client.get(f"/api/v1/runs/{first['id']}")).json()["request"]["second_chance"] is False
        one = (await client.get(f"/api/v1/runs/{first['id']}/results")).json()["items"][0]
        two = (await client.get(f"/api/v1/runs/{second['id']}/results")).json()["items"][0]
        assert one["selection"] == two["selection"]
        events = (await client.get(f"/api/v1/runs/{second['id']}/events")).json()
        plan = next(e["data"] for e in events if e["event"] == "search.plan")
        assert plan["second_chance"] is True
        assert plan["application"]["minimum_work_format"] == "local"


async def test_profile_hides_unrelated_roles_and_soft_failure_stays_in_pool(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.path == "/search/vacancy", "Rejected/maybe listings do not fetch cards"
        raw = {
            "totalResults": 2,
            "vacancies": [
                {"vacancyId": 1, "name": "Sales Manager"},
                {"vacancyId": 2, "name": "QA Engineer", "workExperience": "moreThan6"},
            ],
        }
        return httpx.Response(
            200,
            text='<template id="HH-Lux-InitialState">'
            + json.dumps({"vacancySearchResult": raw})
            + "</template>",
        )

    async with service(tmp_path, handler) as (app, client):
        b = await brief(client, ["QA"], filters={"experience_years": 3})
        finished = await run(app, client, b["id"], company_profiles=False, second_chance=True)
        assert finished["metrics"]["rejected"] == 1 and finished["metrics"]["maybe"] == 1
        default = (await client.get(f"/api/v1/briefs/{b['id']}/vacancies")).json()
        assert [v["id"] for v in default["items"]] == ["2"]
        checks = default["items"][0]["selection"]["checks"]
        assert next(c for c in checks if c["field"] == "experience_years")["kind"] == "soft"
        assert calls == ["/search/vacancy"]


async def test_legacy_brief_without_search_options_can_start(tmp_path):
    fake = Recorder({"QA": [1]})
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, ["QA"])
        with app.state.database._connection.db:
            app.state.database._connection.db.execute(
                "UPDATE briefs SET data=json_remove(data, '$.search') WHERE id=?", (b["id"],)
            )
        result = await run(app, client, b["id"], company_profiles=False)
        assert result["status"] == "complete"
        assert len(app.state.database.runs.runs(b["id"])) == 1


async def test_invalid_saved_brief_does_not_leave_queued_run(tmp_path):
    fake = Recorder({"QA": [1]})
    async with service(tmp_path, fake) as (app, client):
        b = await brief(client, ["QA"])
        with app.state.database._connection.db:
            app.state.database._connection.db.execute(
                "UPDATE briefs SET data=json_set(data, '$.filters.experience_years', -1) WHERE id=?",
                (b["id"],),
            )
        response = await client.post("/api/v1/runs", json={"brief_id": b["id"], "sources": ["hh_ru"]})
        assert response.status_code == 422
        assert app.state.database.runs.runs(b["id"]) == []
        assert fake.calls == []


async def test_active_run_preserves_current_brief_selection_after_edit(tmp_path):
    waiting, release = asyncio.Event(), asyncio.Event()
    fake = Recorder({"QA": [1]})

    async def handler(request):
        if request.url.path.startswith("/vacancy/"):
            waiting.set()
            await release.wait()
        return await fake(request)

    async with service(tmp_path, handler) as (app, client):
        b = await brief(client, ["QA"])
        response = await client.post(
            "/api/v1/runs", json={"brief_id": b["id"], "sources": ["hh_ru"], "company_profiles": False}
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            await client.patch(
                f"/api/v1/briefs/{b['id']}/vacancies/hh_ru:1",
                json={"status": "applied", "note": "Keep my note"},
            )
            updated = await client.put(
                f"/api/v1/briefs/{b['id']}",
                json={"name": b["name"], "queries": ["QA"], "filters": confirmed({"excluded_text": ["QA"]})},
            )
            assert updated.status_code == 200
            assert (
                app.state.database.vacancies.pool_item(b["id"], "hh_ru:1")["selection"]["status"]
                == "rejected"
            )
        finally:
            release.set()
        await asyncio.gather(*app.state.engine.tasks.values())
        pool = app.state.database.vacancies.pool_item(b["id"], "hh_ru:1")
        assert pool["selection"]["status"] == "rejected"
        assert pool["user_status"] == "applied" and pool["note"] == "Keep my note"
        assert app.state.database.vacancies.results(run_id)[0]["selection"]["status"] == "accepted"
        assert app.state.database.runs.run(run_id)["request"]["brief"]["filters"]["excluded_text"] == []
        overview = app.state.database.briefs.brief_overviews()[0]
        assert overview["hidden_count"] == 1 and overview["funnel"]["all"] == 0


def test_recorder_uses_cached_card_facts_for_current_pool_conditions(tmp_path):
    store = Database(tmp_path / "cached.sqlite")
    try:
        old = BriefInput(name="QA", queries=["QA"])
        b = store.briefs.save_brief(old)
        run = store.runs.create_run(b["id"], {}, {"hh_ru": {}})
        card = Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1", description="Forbidden employer")
        from src.enrichment.registry import default_enrichment

        recorder = SourceRecorder(
            store, run["id"], b["id"], "hh_ru", Clock(), lambda: None, enrichment=default_enrichment()
        )
        decision = FilterStage(
            old.filters, tuple(old.queries), catalog=default_enrichment().catalog
        ).evaluate(card)
        recorder.save(card, decision, "ready", detail_fetched=True)
        store.briefs.save_brief(
            old.model_copy(update={"filters": SearchFilters(excluded_text=["Forbidden"])}), b["id"]
        )
        listing = card.model_copy(update={"description": None})
        recorder.save(listing, decision, "pending")
        pool = store.vacancies.pool_item(b["id"], "hh_ru:1")
        assert pool["description"] == "Forbidden employer"
        assert pool["selection"]["status"] == "rejected"
        assert store.vacancies.results(run["id"])[0]["selection"]["status"] == "accepted"
    finally:
        store.close()


async def test_text_only_cached_card_gets_rich_description_then_is_reused(tmp_path):
    fake = Recorder({"QA": [1]})
    async with service(tmp_path, fake) as (app, client):
        saved_brief = await brief(client, ["QA"])
        first = await run(app, client, saved_brief["id"], company_profiles=False)
        historical = app.state.database.vacancies.results(first["id"])
        with app.state.database._connection.db:
            app.state.database._connection.db.execute(
                "UPDATE vacancies SET data=json_remove(data, '$.description_html') WHERE id='hh_ru:1'"
            )
        app.state.database.vacancies.set_pool_status(saved_brief["id"], "hh_ru:1", "applied", "Моя заметка")
        before = len(fake.paths("/vacancy/"))
        second = await run(app, client, saved_brief["id"], company_profiles=False)
        assert len(fake.paths("/vacancy/")) == before + 1
        assert second["metrics"]["details_ok"] == 1
        card = app.state.database.vacancies.pool_item(saved_brief["id"], "hh_ru:1")
        assert card["description_html"]
        assert card["user_status"] == "applied" and card["note"] == "Моя заметка"
        assert app.state.database.vacancies.results(first["id"]) == historical
        third = await run(app, client, saved_brief["id"], company_profiles=False)
        assert third["metrics"]["details_reused"] == 1
        assert len(fake.paths("/vacancy/")) == before + 1
