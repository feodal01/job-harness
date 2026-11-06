"""Runs with company boards read from the catalog at start: with and without a session."""

import asyncio
import json
import time
from pathlib import Path

import httpx
from test_engine_runs import Recorder, brief, run, service

from src.core.config import Settings
from src.db import Database
from src.network.client import FetchError, HostSlots, Network
from src.parsers.ats.lever.parser import create_adapter as lever
from src.parsers.ats.models import Board
from src.parsers.interfaces import RequestSettings
from src.schemas.companies import CompanyLink
from src.services.auth import LoginRequired
from src.services.sources import NO_LOGIN

LEVER = json.loads((Path(__file__).parent / "fixtures/ats/lever/airslate/response.json").read_text())
AIRSLATE = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
HIDDEN = "00000000-0000-4000-8000-000000000003"


def link(id: str, name: str, slug: str) -> CompanyLink:
    return CompanyLink(
        id=id,
        name=name,
        url=f"https://jobs.lever.co/{slug}",
        link_kind="ats_board",
        provider="ats:lever",
        categories=[],
        specialization=None,
    )


ROWS = [
    link(AIRSLATE, "airSlate", "airslate"),
    link(OTHER, "Other", "other"),
    link(HIDDEN, "Hidden", "hidden"),
]


class Boards(Recorder):
    """HH and Getmatch as in the engine tests, plus Lever boards answering from a fixture."""

    def __init__(self) -> None:
        super().__init__({"QA": [1]}, pages=1)
        self.lever: list[str] = []

    async def __call__(self, request):
        if request.url.host == "api.lever.co":
            self.lever.append(request.url.path)
            if request.url.path.endswith("/other"):
                return httpx.Response(503, text="down")
            return httpx.Response(200, json=LEVER)
        return await super().__call__(request)


def catalog(rows=ROWS, error=None):
    async def read(**filters):
        if error:
            raise error
        providers = filters.get("providers")
        return [r for r in rows if providers is None or r.provider in providers]

    return read


def fast_boards(app):
    """Boards come from the catalog at start; keep their pacing out of the test's time."""
    registry = app.state.sources_service.registry
    original = registry.board

    def board(*args, **kwargs):
        built = original(*args, **kwargs)
        if hasattr(built, "settings"):
            from dataclasses import replace

            settings = built.settings.model_copy(update={"interval_seconds": 0, "max_attempts": 1})
            return replace(built, settings=settings)
        return built

    registry.board = board


async def test_without_login_the_run_searches_what_it_can_and_records_why_boards_are_missing(tmp_path):
    fake = Boards()
    async with service(tmp_path, fake) as (app, client):
        app.state.sources_service.catalog = catalog(error=LoginRequired("http://127.0.0.1:8765/auth"))
        b = await brief(client, ["QA"])
        result = await run(
            app, client, b["id"], sources=("hh_ru", "ats:lever:airslate"), company_profiles=False
        )
        assert result["sources"]["hh_ru"]["status"] == "complete"
        assert result["sources"]["ats:lever:airslate"]["status"] == "skipped"
        assert result["sources"]["ats:lever:airslate"]["error"] == NO_LOGIN
        assert result["request"]["skipped"]["ats:lever:airslate"] == NO_LOGIN
        assert fake.lever == []

        only_boards = await client.post(
            "/api/v1/runs", json={"brief_id": b["id"], "sources": ["ats:lever:airslate"]}
        )
        assert only_boards.status_code == 422 and NO_LOGIN in only_boards.text


async def test_unknown_sources_are_still_refused(tmp_path):
    async with service(tmp_path, Boards()) as (app, client):
        app.state.sources_service.catalog = catalog()
        b = await brief(client, ["QA"])
        for source in ("nope", "ats:manatal:acme"):
            response = await client.post("/api/v1/runs", json={"brief_id": b["id"], "sources": [source]})
            assert response.status_code == 422 and "Unknown source" in response.text


async def test_boards_from_the_catalog_run_beside_fixed_sources_and_keep_only_ids(tmp_path):
    fake = Boards()
    async with service(tmp_path, fake) as (app, client):
        app.state.sources_service.catalog = catalog()
        fast_boards(app)
        b = await brief(client, ["QA"])
        result = await run(
            app,
            client,
            b["id"],
            sources=("hh_ru", "ats:lever:airslate", "ats:lever:other", "ats:lever:gone"),
            company_profiles=False,
        )
        states = result["sources"]
        assert states["ats:lever:airslate"]["status"] == "complete"
        assert states["ats:lever:airslate"]["metrics"]["unique_seen"] == len(LEVER)
        assert states["ats:lever:other"]["status"] == "failed"  # one board failing stops only itself
        assert states["hh_ru"]["status"] == "complete"
        assert states["ats:lever:gone"]["status"] == "skipped"
        assert "больше нет в каталоге" in states["ats:lever:gone"]["error"]
        assert result["status"] == "partial"
        assert result["request"]["sources"] == ["hh_ru", "ats:lever:airslate", "ats:lever:other"]
        # The run keeps ids of searched boards; catalog rows it did not search leave no trace.
        stored = json.dumps(result) + json.dumps(app.state.database.runs.run(result["id"]))
        assert "hidden" not in stored.lower() and HIDDEN not in stored
        # The board's vacancies are filtered with the platform's own capabilities.
        pool = (await client.get(f"/api/v1/briefs/{b['id']}/vacancies", params={"view": "all"})).json()
        assert any(item["source"] == "ats:lever:airslate" for item in pool["items"])
        # Re-saving the brief re-evaluates board vacancies without asking the catalog.
        app.state.sources_service.catalog = catalog(error=AssertionError("catalog must not be read"))
        saved = await client.put(
            f"/api/v1/briefs/{b['id']}", json={"name": "Again", "queries": ["QA"], "filters": b["filters"]}
        )
        assert saved.status_code == 200, saved.text


async def test_a_board_whose_breaker_is_open_does_not_stop_its_neighbour_on_the_same_host(tmp_path):
    store = Database(tmp_path / "boards.sqlite")
    settings = RequestSettings(interval_seconds=0, max_attempts=1, breaker_seconds=60)
    first = lever(Board("https://jobs.lever.co/first", "First"), settings)
    second = lever(Board("https://jobs.lever.co/second", "Second"), settings)
    state = store.resources.resource(first.resource)
    store.resources.resource(first.resource, {**state, "open_until": time.time() + 60})
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
        ) as client:
            hosts = HostSlots(4)
            blocked = Network(first, store.resources, store.events, client, hosts)
            open_one = Network(second, store.resources, store.events, client, hosts)
            try:
                await blocked.get(None, "https://api.lever.co/v0/postings/first?mode=json", "listing")
                raise AssertionError("the open breaker must refuse the request")
            except FetchError as error:
                assert error.kind == "circuit_open"
            assert (
                await open_one.get(None, "https://api.lever.co/v0/postings/second?mode=json", "listing")
                == "[]"
            )
    finally:
        store.close()


async def test_many_boards_on_one_host_never_exceed_the_host_ceiling(tmp_path):
    store = Database(tmp_path / "ceiling.sqlite")
    active = peak = 0

    async def slow(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=[])

    settings = RequestSettings(interval_seconds=0, max_attempts=1, concurrency=3)
    boards = [lever(Board(f"https://jobs.lever.co/b{i}", f"B{i}"), settings) for i in range(20)]
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            hosts = HostSlots(2)
            networks = [Network(board, store.resources, store.events, client, hosts) for board in boards]
            await asyncio.gather(
                *(
                    network.get(None, f"https://api.lever.co/v0/postings/b{i}?mode=json", "listing")
                    for i, network in enumerate(networks)
                    for _ in range(3)
                )
            )
        assert peak == 2
    finally:
        store.close()


def test_the_host_ceiling_is_a_setting():
    assert Settings().max_requests_per_host == 4
    assert Settings(max_requests_per_host=1).max_requests_per_host == 1
