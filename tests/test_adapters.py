"""Different source workflows must run through the same engine and HTTP budget."""

import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from geo_support import confirmed, with_geography

from src.app import create_app
from src.core.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def payload(name):
    return (FIXTURES / "getmatch" / f"{name}.json").read_text()


def app_for(tmp_path, handler):
    return create_app(
        Settings(
            database_path=tmp_path / "test.sqlite",
            sources={"hh_ru": {"interval_seconds": 0}, "getmatch": {"interval_seconds": 0, "page_size": 10}},
        ),
        transport=httpx.MockTransport(with_geography(handler)),
    )


async def test_catalog_exposes_operations_and_data_origins_without_claiming_full_text_search(tmp_path):
    app = app_for(tmp_path, lambda r: httpx.Response(500))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            sources = {s["id"]: s for s in (await c.get("/api/v1/sources")).json()}
            assert "getmatch" in sources
            hh, gm = (sources[key]["capabilities"] for key in ("hh_ru", "getmatch"))
            assert set(hh["actions"]) == {"search", "vacancy_details", "company_profile"}
            assert gm["actions"] == ["search", "vacancy_details"]
            assert hh["fields"]["description"] == ["vacancy_details"]
            assert gm["fields"]["description"] == ["vacancy_details"]
            assert gm["fields"]["summary"] == ["search"]
            assert gm["fields"]["company_profile_url"] == ["search"]
            assert gm["query_parameters"]["query"] == "ignored"
            assert gm["query_parameters"]["locations"] == "native"


async def test_feed_only_component_streams_summaries_without_optional_requests(tmp_path):
    calls = []
    second_page_requested = asyncio.Event()
    release_second_page = asyncio.Event()

    async def handler(request):
        calls.append(request)
        assert request.url.path == "/api/offers"
        assert "sp" not in request.url.params
        assert request.url.params["limit"] == "10"
        if request.url.params["offset"] == "0":
            return httpx.Response(200, text=payload("qa_auto"))
        assert request.url.params["offset"] == "10"
        second_page_requested.set()
        await release_second_page.wait()
        return httpx.Response(200, text=payload("qa_auto_page2"))

    app = app_for(tmp_path, handler)
    async with app.router.lifespan_context(app):
        from dataclasses import replace

        engine = app.state.engine
        engine.sources["getmatch"] = replace(engine.sources["getmatch"], vacancy_details=None)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            brief = (
                await c.post(
                    "/api/v1/briefs",
                    json={"name": "Getmatch", "queries": ["QA Auto"], "filters": confirmed()},
                )
            ).json()
            response = await c.post(
                "/api/v1/runs",
                json={
                    "brief_id": brief["id"],
                    "sources": ["getmatch"],
                    "limits": {"getmatch": {"max_items_per_query": 12, "max_pages_per_query": 2}},
                },
            )
            assert response.status_code == 202, response.text
            run_id = response.json()["id"]
            try:
                await asyncio.wait_for(second_page_requested.wait(), 5)
                interim = (await c.get(f"/api/v1/runs/{run_id}/results")).json()["items"]
                assert len(interim) == 10
                assert all(
                    v["detail_status"] == "not_required" and v["summary"] and not v["description"]
                    for v in interim
                )
                assert (await c.get(f"/api/v1/runs/{run_id}")).json()["status"] == "running"
            finally:
                release_second_page.set()
            await asyncio.gather(*app.state.engine.tasks.values())
            final = (await c.get(f"/api/v1/runs/{run_id}")).json()
            rows = (await c.get(f"/api/v1/runs/{run_id}/results")).json()["items"]
            assert final["status"] == "complete"
            assert final["limit_reached"] is True
            assert len(rows) == len({v["id"] for v in rows}) == 12
            assert final["metrics"]["pages"] == 2
            assert final["metrics"]["preparation_requests"] == 1  # Getmatch vacancy form check
            assert final["metrics"]["details_ok"] == 0
            assert final["metrics"]["descriptions_ready"] == 0
            assert final["metrics"]["first_description_ms"] is None
            events = (await c.get(f"/api/v1/runs/{run_id}/events")).json()
            assert any(e["event"] == "search.mapping" and e["data"]["mode"] == "ignored" for e in events)
            assert len(calls) == 2


@pytest.mark.parametrize("queries", [None, [], ["QA", "C++", "zzzz-not-a-role"]])
async def test_getmatch_collects_feed_once_with_or_without_brief_queries(tmp_path, queries):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/offers"
        assert "sp" not in request.url.params
        return httpx.Response(200, text=payload("feed_page1"))

    app = app_for(tmp_path, handler)
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        engine.sources["getmatch"] = replace(engine.sources["getmatch"], vacancy_details=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            data = {"name": "Whole feed", "filters": confirmed()}
            if queries is not None:
                data["queries"] = queries
            created = await c.post("/api/v1/briefs", json=data)
            assert created.status_code == 201, created.text
            brief = created.json()
            response = await c.post(
                "/api/v1/runs",
                json={
                    "brief_id": brief["id"],
                    "sources": ["getmatch"],
                    "limits": {"getmatch": {"max_items_per_query": 200, "max_pages_per_query": 1}},
                },
            )
            assert response.status_code == 202, response.text
            run_id = response.json()["id"]
            await asyncio.gather(*app.state.engine.tasks.values())
            run = (await c.get(f"/api/v1/runs/{run_id}")).json()
            assert run["status"] == "complete" and run["limit_reached"]
            assert run["request"]["brief"]["queries"] == (queries or [])
            assert len(calls) == 1
            assert len((await c.get(f"/api/v1/runs/{run_id}/results")).json()["items"]) == 12
            events = (await c.get(f"/api/v1/runs/{run_id}/events")).json()
            skipped = [e for e in events if e["event"] == "search.query_skipped"]
            assert len(skipped) == 1
            assert skipped[0]["data"]["queries"] == (queries or [])


async def test_getmatch_skips_company_profiles_it_does_not_implement(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=payload("qa_manual"))  # 10 offers at offset 0.

    app = app_for(tmp_path, handler)
    async with app.router.lifespan_context(app):
        from src.models.brief import BriefInput

        app.state.engine.sources["getmatch"] = replace(
            app.state.engine.sources["getmatch"], vacancy_details=None
        )
        brief = app.state.briefs_service.create(
            BriefInput(name="Unsupported", queries=["QA"], filters=confirmed())
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            response = await c.post(
                "/api/v1/runs",
                json={
                    "brief_id": brief["id"],
                    "sources": ["getmatch"],
                    "limits": {"getmatch": {"max_pages_per_query": 1}},
                    "company_profiles": True,
                },
            )
            assert response.status_code == 202, response.text
            await asyncio.gather(*app.state.engine.tasks.values())
            assert response.json()["request"]["company_profiles"] == {"getmatch": False}
            assert set(calls) == {"/api/offers"}
            final = (await c.get(f"/api/v1/runs/{response.json()['id']}")).json()
            assert final["status"] == "complete" and final["metrics"]["unique_seen"] > 0


async def test_company_discovered_in_detail_is_enriched_and_shared(tmp_path):
    from dataclasses import replace

    from src.models.brief import BriefInput
    from src.models.run import RunInput
    from src.models.vacancy import Vacancy
    from src.parsers.interfaces import SearchPage, SearchRequest

    class Feed:
        fields = frozenset({"title"})
        parameters = {"query": "native"}
        query_notes = ""

        async def prepare(self, brief, fetch, *, second_chance=False):
            from src.parsers.interfaces import SearchPlan

            return SearchPlan(tuple(self.build(q) for q in brief.queries), self.parameters)

        def build(self, query, location=None):
            return SearchRequest("https://hh.ru/feed", query)

        def parse(self, body, request):
            return SearchPage(
                [Vacancy(id=str(i), title="QA", url=f"https://hh.ru/vacancy/{i}") for i in [101, 102]],
                total=2,
            )

    from types import SimpleNamespace

    from src.models.company import Company

    details = SimpleNamespace(
        fields=frozenset({"company", "description"}),
        url=lambda v: v.url,
        parse=lambda body, v: v.model_copy(
            update={
                "description": "Details",
                "company": Company(id="shared"),
            }
        ),
    )
    profile = SimpleNamespace(
        fields=frozenset({"company_website"}),
        url=lambda company: f"https://hh.ru/employer/{company.id}",
        parse=lambda body, company: company.model_copy(update={"site_url": "https://example.org/careers"}),
    )
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200)

    app = app_for(tmp_path, handler)
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        engine.sources["hh_ru"] = replace(
            engine.sources["hh_ru"], search=Feed(), vacancy_details=details, company_profile=profile
        )
        brief = app.state.database.briefs.save_brief(BriefInput(name="Late company", queries=["QA"]))
        run = engine.start(RunInput(brief_id=brief["id"], sources=["hh_ru"]))
        await asyncio.gather(*engine.tasks.values())
        assert app.state.database.runs.run(run["id"])["status"] == "complete"
        rows = app.state.database.vacancies.results(run["id"])
        assert len(rows) == 2
        assert all(row["company"]["site_url"] == "https://example.org/careers" for row in rows)
        assert len([p for p in calls if p.startswith("/employer/")]) == 1


async def test_feed_without_description_or_optional_operations_finishes_honestly(tmp_path):
    from dataclasses import replace

    from src.models.brief import BriefInput
    from src.models.run import RunInput
    from src.models.vacancy import Vacancy
    from src.parsers.interfaces import SearchPage, SearchRequest

    class Feed:
        fields = frozenset({"title"})
        parameters = {"query": "native"}
        query_notes = ""

        async def prepare(self, brief, fetch, *, second_chance=False):
            from src.parsers.interfaces import SearchPlan

            return SearchPlan(tuple(self.build(q) for q in brief.queries), self.parameters)

        def build(self, query, location=None):
            return SearchRequest("https://hh.ru/feed", query)

        def parse(self, body, request):
            return SearchPage([Vacancy(id="1", title="QA", url="https://hh.ru/vacancy/1")], 1)

    calls = []
    app = app_for(tmp_path, lambda r: calls.append(r) or httpx.Response(200))
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        engine.sources["hh_ru"] = replace(
            engine.sources["hh_ru"],
            search=Feed(),
            vacancy_details=None,
            company_profile=None,
        )
        brief = app.state.database.briefs.save_brief(BriefInput(name="Feed only", queries=["QA"]))
        run = engine.start(RunInput(brief_id=brief["id"], sources=["hh_ru"]))
        await asyncio.gather(*engine.tasks.values())
        final = app.state.database.runs.run(run["id"])
        assert final["status"] == "complete"
        assert final["metrics"]["descriptions_ready"] == 0
        assert final["metrics"]["first_description_ms"] is None
        row = app.state.database.vacancies.results(run["id"])[0]
        assert row["detail_status"] == "not_required"
        assert row["description"] is None
        assert len(calls) == 1


async def test_unexpected_parse_failure_is_logged_with_stage_and_url(tmp_path):
    from dataclasses import replace

    from src.models.brief import BriefInput
    from src.models.run import RunInput
    from src.parsers.interfaces import SearchRequest

    class BrokenFeed:
        fields = frozenset({"title"})
        parameters = {"query": "native"}
        query_notes = ""

        async def prepare(self, brief, fetch, *, second_chance=False):
            from src.parsers.interfaces import SearchPlan

            return SearchPlan(tuple(self.build(q) for q in brief.queries), self.parameters)

        def build(self, query, location=None):
            return SearchRequest("https://hh.ru/feed", query)

        def parse(self, body, request):
            raise TypeError("Broken source payload")

    app = app_for(tmp_path, lambda r: httpx.Response(200))
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        engine.sources["hh_ru"] = replace(engine.sources["hh_ru"], search=BrokenFeed())
        brief = app.state.database.briefs.save_brief(BriefInput(name="Broken", queries=["QA"]))
        run = engine.start(RunInput(brief_id=brief["id"], sources=["hh_ru"]))
        await asyncio.gather(*engine.tasks.values())
        assert app.state.database.runs.run(run["id"])["status"] == "failed"
        errors = [
            e["data"] for e in app.state.database.events.events(run["id"]) if e["event"] == "parse.error"
        ]
        assert len(errors) == 1
        assert errors[0]["stage"] == "listing" and errors[0]["url"] == "https://hh.ru/feed"
        assert "TypeError" in errors[0]["error"]


@pytest.mark.parametrize(
    ("http_status", "run_status", "detail_status"),
    [(200, "complete", "ready"), (503, "partial", "error"), (404, "complete", "not_found")],
)
async def test_getmatch_detail_enrichment_preserves_summary_and_reports_failures(
    tmp_path, http_status, run_status, detail_status
):
    """A failed card keeps the summary and makes the run partial; a removed vacancy (404) is not a failure."""

    def handler(request):
        if request.url.path == "/api/offers":
            return httpx.Response(200, text=payload("qa_auto"))
        assert request.url.path.startswith("/vacancies/34499-")
        return httpx.Response(http_status, text=(FIXTURES / "getmatch/detail-34499.html").read_text())

    app = app_for(tmp_path, handler)
    async with app.router.lifespan_context(app):
        from src.models.brief import BriefInput, SourceLimitsInput
        from src.models.run import RunInput

        brief = app.state.database.briefs.save_brief(BriefInput(name="Full text", queries=["QA Auto"]))
        run = app.state.engine.start(
            RunInput(
                brief_id=brief["id"],
                sources=["getmatch"],
                limits={"getmatch": SourceLimitsInput(max_items_per_query=1)},
            )
        )
        await asyncio.gather(*app.state.engine.tasks.values())
        final = app.state.database.runs.run(run["id"])
        assert final["status"] == run_status
        row = app.state.database.vacancies.results(run["id"])[0]
        assert row["summary"]
        assert row["detail_status"] == detail_status
        assert bool(row["description"]) is (http_status == 200)
        if http_status == 200:
            assert "Знание фундаментальной теории тестирования." in row["description"]
