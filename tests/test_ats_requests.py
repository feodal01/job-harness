"""Generic transport and engine tests; these payloads are not ATS website fixtures."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from src.core.config import Settings
from src.db import Database
from src.engine import SearchEngine
from src.engine.listing import ListingWalker
from src.engine.models import Limits
from src.models.brief import BriefInput
from src.models.run import RunInput
from src.models.vacancy import Vacancy
from src.network.client import FetchError, Network
from src.parsers.interfaces import RequestSettings, SearchPage, SearchPlan, SearchRequest, SourceAdapter
from tests.test_network import setup


def post(offset=0):
    return SearchRequest("https://hh.ru/search", "QA", method="POST", json_body={"offset": offset})


def test_pagination_distinguishes_json_pages_and_rejects_same_body():
    first = post()
    walker = ListingWalker((first,), Limits(100, 10))
    step = walker.next()
    assert step is not None and not walker.repeated(step)
    walker.finished(step, SearchPage(next_requests=(post(20),)), 20)
    step = walker.next()
    assert step is not None and not walker.repeated(step)
    walker.finished(step, SearchPage(next_requests=(post(20),)), 20)
    step = walker.next()
    assert step is not None and walker.repeated(step)
    a = replace(first, json_body={"offset": 0, "limit": 20})
    b = replace(first, json_body={"limit": 20, "offset": 0})
    assert a.identity == b.identity
    assert first.identity != SearchRequest(first.url, first.query).identity


def test_invalid_search_method_or_body_is_rejected():
    with pytest.raises(ValueError, match="GET"):
        SearchRequest("https://example.org", "", json_body={})
    with pytest.raises(ValueError, match="requires"):
        SearchRequest("https://example.org", "", method="POST")
    with pytest.raises(ValueError, match="GET or"):
        SearchRequest("https://example.org", "", method="DELETE")


@pytest.mark.parametrize("encoding", ["json", "form"])
async def test_post_retries_preserve_body_and_request_budget(tmp_path, encoding):
    source, store, run_id = setup(tmp_path, max_attempts=2, retry_base_seconds=0)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, text="ok")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            net = Network(source, store.resources, store.events, client)
            request = (
                post(20)
                if encoding == "json"
                else SearchRequest(
                    post().url,
                    "QA",
                    method="POST",
                    form_body={"keywords": "QA & C++", "location": ["1", "2"]},
                )
            )
            assert await net.fetch(run_id, request) == "ok"
        assert [r.method for r in calls] == ["POST", "POST"]
        if encoding == "json":
            assert all(json.loads(r.content) == {"offset": 20} for r in calls)
            assert all(r.headers["content-type"] == "application/json" for r in calls)
        else:
            from urllib.parse import parse_qs

            assert all(
                parse_qs(r.content.decode()) == {"keywords": ["QA & C++"], "location": ["1", "2"]}
                for r in calls
            )
            assert all(r.headers["content-type"] == "application/x-www-form-urlencoded" for r in calls)
        assert len([e for e in store.events.events(run_id) if e["event"] == "http.started"]) == 2
    finally:
        store.close()


@pytest.mark.parametrize("status,method", [(307, "POST"), (308, "POST"), (303, "GET")])
@pytest.mark.parametrize("encoding", ["json", "form"])
async def test_post_redirect_method_is_explicit(tmp_path, status, method, encoding):
    source, store, run_id = setup(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(status, headers={"location": "/next"}) if len(calls) == 1 else httpx.Response(200)
        )

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            request = (
                post()
                if encoding == "json"
                else SearchRequest(post().url, "QA", method="POST", form_body={"keywords": "QA"})
            )
            await Network(source, store.resources, store.events, client).fetch(run_id, request)
        assert calls[1].method == method
        assert calls[1].content == (calls[0].content if method == "POST" else b"")
    finally:
        store.close()


@pytest.mark.parametrize("status", [301, 302])
async def test_post_ambiguous_redirect_fails_before_second_request(tmp_path, status):
    source, store, run_id = setup(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "/next"})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(FetchError, match="Ambiguous"):
                await Network(source, store.resources, store.events, client).fetch(run_id, post())
        assert len(calls) == 1
    finally:
        store.close()


async def test_post_redirect_host_is_checked_before_resending_body(tmp_path):
    source, store, run_id = setup(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://untrusted.example/search"})

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(FetchError, match="outside allowed"):
                await Network(source, store.resources, store.events, client).fetch(run_id, post())
        assert len(calls) == 1
    finally:
        store.close()


async def test_engine_walks_post_pages_and_keeps_existing_storage_and_filters(tmp_path):
    def request(offset):
        return SearchRequest("https://example.org/search", "QA", method="POST", json_body={"offset": offset})

    async def prepare(brief, fetch, *, second_chance=False):
        return SearchPlan((request(0),), {"query": "native"})

    def parse(body, original):
        offset = json.loads(body)["offset"]
        return SearchPage(
            [Vacancy(id=str(offset), title="QA Engineer", url=f"https://example.org/jobs/{offset}")],
            total=2,
            next_requests=(request(1),) if offset == 0 else (),
        )

    search = SimpleNamespace(
        prepare=prepare,
        build=lambda query, location=None: request(0),
        parse=parse,
        parameters={"query": "native"},
        query_notes="",
        fields=frozenset({"title"}),
    )
    source = SourceAdapter(
        id="post-search",
        name="Synthetic POST source",
        type="ats",
        resource="example.org",
        allowed_hosts=("example.org",),
        search=search,
        settings=RequestSettings(interval_seconds=0),
    )
    store = Database(tmp_path / "test.sqlite")
    calls = []

    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(200, json=json.loads(incoming.content))

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            engine = SearchEngine(Settings(), store, {source.id: source}, client)
            brief = store.briefs.save_brief(BriefInput(name="POST", queries=["QA"]))
            run = engine.start(RunInput(brief_id=brief["id"], sources=[source.id]))
            await asyncio.gather(*engine.tasks.values())
            assert store.runs.run(run["id"])["status"] == "complete"
            assert [json.loads(r.content)["offset"] for r in calls] == [0, 1]
            assert len(store.vacancies.results(run["id"])) == 2
            assert len(store.vacancies.pool(brief["id"])) == 2
            await engine.close()
    finally:
        store.close()


async def test_real_native_adapter_carries_brief_queries_through_engine_and_pagination(tmp_path):
    import gzip
    from pathlib import Path
    from urllib.parse import parse_qs

    from src.parsers.ats.models import Board
    from src.parsers.ats.smartrecruiters.parser import create_adapter

    fixtures = Path(__file__).parent / "fixtures/ats/native_filters"
    cases = [
        c
        for c in json.loads((fixtures / "cases.json").read_text())
        if c["platform"] == "smartrecruiters" and c["company"] == "Ubisoft2"
    ]
    positive = next(c for c in cases if c["label"] == "positive")
    negative = next(c for c in cases if c["label"] == "negative")
    responses = {
        (c["query"], str(index * 100)): gzip.decompress((fixtures / page["file"]).read_bytes())
        for c in cases
        for index, page in enumerate(c["pages"])
    }
    calls = []

    def handler(request):
        params = parse_qs(request.url.query.decode())
        key = (params.get("q", [""])[0], params.get("offset", ["0"])[0])
        calls.append(key)
        return httpx.Response(200, content=responses[key])

    source = create_adapter(
        Board(positive["board_url"], "Ubisoft"),
        RequestSettings(
            interval_seconds=0,
            max_items_per_query=5000,
            max_pages_per_query=50,
        ),
    )
    store = Database(tmp_path / "native.sqlite")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            engine = SearchEngine(Settings(), store, {source.id: source}, client)
            brief = store.briefs.save_brief(
                BriefInput(name="Native query", queries=[positive["query"], negative["query"]])
            )
            run = engine.start(RunInput(brief_id=brief["id"], sources=[source.id]))
            await asyncio.gather(*engine.tasks.values())
            assert calls == [("Community", "0"), ("zxqvnonexistent", "0"), ("Community", "100")]
            assert store.runs.run(run["id"])["status"] == "complete"
            assert len(store.vacancies.results(run["id"])) == 109
            await engine.close()
    finally:
        store.close()
