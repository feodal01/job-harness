import asyncio
import time

import httpx
import pytest

from src.core.config import Settings
from src.db import Database
from src.models.brief import BriefInput
from src.network.client import FetchError, Network
from src.parsers.registry import build_sources


def setup(tmp_path, **values):
    settings = Settings(
        database_path=tmp_path / "test.sqlite",
        sources={"hh_ru": {"interval_seconds": 0, "retry_base_seconds": 0, **values}},
    )
    source = build_sources(settings)["hh_ru"]
    store = Database(settings.database_path)
    brief = store.briefs.save_brief(BriefInput(name="network", queries=["hello"]))
    run = store.runs.create_run(brief["id"], {"sources": ["hh_ru"]}, {})
    return source, store, run["id"]


async def test_429_honors_retry_after_and_logs_each_attempt(tmp_path):
    starts = []

    def handler(request):
        starts.append(time.monotonic())
        return (
            httpx.Response(429, headers={"Retry-After": ".04"}) if len(starts) == 1 else httpx.Response(200)
        )

    source, store, run_id = setup(tmp_path)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await Network(source, store.resources, store.events, client).get(
                run_id, "https://hh.ru/search/vacancy", "listing"
            )
        assert starts[1] - starts[0] >= 0.038
        assert len([e for e in store.events.events(run_id) if e["event"] == "http.retry"]) == 1
    finally:
        store.close()


async def test_403_opens_breaker_across_restart_and_never_retries(tmp_path):
    source, store, run_id = setup(tmp_path, breaker_seconds=1)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            net = Network(source, store.resources, store.events, client)
            with pytest.raises(FetchError, match="blocked"):
                await net.get(run_id, "https://hh.ru/search/vacancy", "listing")
            net = Network(source, store.resources, store.events, client)
            with pytest.raises(FetchError, match="circuit_open"):
                await net.get(run_id, "https://hh.ru/vacancy/1", "detail")
        assert len(calls) == 1
    finally:
        store.close()


async def test_redirect_cannot_leave_hh_and_counts_as_request(tmp_path):
    source, store, run_id = setup(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
            )
        ) as client:
            with pytest.raises(FetchError, match="unsupported_redirect"):
                await Network(source, store.resources, store.events, client).get(
                    run_id, "https://hh.ru/employer/1", "company"
                )
        assert len([e for e in store.events.events(run_id) if e["event"] == "http.started"]) == 1
    finally:
        store.close()


async def test_waiting_request_rechecks_new_global_cooldown(tmp_path):
    source, store, run_id = setup(tmp_path, max_attempts=1)
    calls = []
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: calls.append(time.time()) or httpx.Response(200),
            )
        ) as client:
            net = Network(source, store.resources, store.events, client)
            net.state["next_request_at"] = time.time() + 0.04
            waiting = asyncio.create_task(net.get(run_id, "https://hh.ru/vacancy/1", "detail"))
            await asyncio.sleep(0.01)
            extended = time.time() + 0.09
            net.state["next_request_at"] = extended
            await waiting
            assert calls[0] >= extended - 0.003
    finally:
        store.close()


def test_second_service_cannot_own_same_database(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    try:
        with pytest.raises(RuntimeError, match="already has"):
            Database(tmp_path / "test.sqlite")
    finally:
        store.close()
