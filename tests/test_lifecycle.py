"""Failures during startup and shutdown must unwind acquired application resources."""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import src.app as app_module
from src.core.config import Settings
from src.db import Database
from src.engine import SearchEngine


@dataclass
class LifecycleProbe:
    events: list[str] = field(default_factory=list)
    databases: list[Database] = field(default_factory=list)
    clients: list[httpx.AsyncClient] = field(default_factory=list)
    enter_error: RuntimeError | None = None
    close_error: RuntimeError | None = None


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> LifecycleProbe:
    observed = LifecycleProbe()

    class TrackedDatabase(Database):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            observed.databases.append(self)
            observed.events.append("database.open")

        def close(self) -> None:
            observed.events.append("database.close")
            super().close()

    class TrackedClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            observed.clients.append(self)

        async def __aenter__(self) -> httpx.AsyncClient:
            observed.events.append("http.enter")
            if observed.enter_error is not None:
                raise observed.enter_error
            client = await super().__aenter__()
            observed.events.append("http.acquired")
            return client

        async def aclose(self) -> None:
            observed.events.append("http.close")
            await super().aclose()

    class TrackedEngine(SearchEngine):
        async def close(self) -> None:
            observed.events.append("engine.close")
            await super().close()
            if observed.close_error is not None:
                raise observed.close_error

    monkeypatch.setattr(app_module, "Database", TrackedDatabase)
    monkeypatch.setattr(app_module.httpx, "AsyncClient", TrackedClient)
    monkeypatch.setattr(app_module, "SearchEngine", TrackedEngine)
    return observed


def application(tmp_path: Path) -> FastAPI:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Lifecycle tests must not send requests: {request.url.host}")

    return app_module.create_app(
        Settings(database_path=tmp_path / "search.sqlite"),
        transport=httpx.MockTransport(unexpected_request),
    )


def assert_database_released(tmp_path: Path, probe: LifecycleProbe) -> None:
    assert len(probe.databases) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        probe.databases[0]._connection.db.execute("SELECT 1")
    reopened = Database(tmp_path / "search.sqlite")
    reopened.close()


async def test_http_entry_failure_closes_client_and_releases_database(
    tmp_path: Path, probe: LifecycleProbe
) -> None:
    failure = RuntimeError("HTTP entry failed")
    probe.enter_error = failure
    app = application(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must fail before serving")
    assert caught.value is failure
    assert probe.events == ["database.open", "http.enter", "http.close", "database.close"]
    assert probe.clients[0].is_closed
    assert_database_released(tmp_path, probe)


async def test_partial_transport_entry_is_closed_when_client_entry_raises(
    tmp_path: Path, probe: LifecycleProbe
) -> None:
    failure = RuntimeError("Transport failed after acquiring a resource")

    class PartiallyOpenedTransport(httpx.AsyncBaseTransport):
        acquired = False
        closed = False

        async def __aenter__(self) -> httpx.AsyncBaseTransport:
            self.acquired = True
            probe.events.append("transport.acquired")
            raise failure

        async def aclose(self) -> None:
            assert self.acquired
            self.closed = True
            probe.events.append("transport.close")

    transport = PartiallyOpenedTransport()
    app = app_module.create_app(Settings(database_path=tmp_path / "search.sqlite"), transport=transport)
    with pytest.raises(RuntimeError) as caught:
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must fail before serving")
    assert caught.value is failure
    assert probe.events == [
        "database.open",
        "http.enter",
        "transport.acquired",
        "http.close",
        "transport.close",
        "database.close",
    ]
    assert transport.closed
    assert probe.clients[0].is_closed
    assert_database_released(tmp_path, probe)


async def test_engine_construction_failure_closes_http_and_database(
    tmp_path: Path, probe: LifecycleProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("Engine construction failed")

    def fail_engine(*args: Any, **kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(app_module, "SearchEngine", fail_engine)
    app = application(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must fail before serving")
    assert caught.value is failure
    assert probe.events == [
        "database.open",
        "http.enter",
        "http.acquired",
        "http.enter",
        "http.acquired",
        "http.close",
        "http.close",
        "database.close",
    ]
    assert len(probe.clients) == 2 and all(client.is_closed for client in probe.clients)
    assert_database_released(tmp_path, probe)


async def test_late_service_failure_closes_engine_http_and_database(
    tmp_path: Path, probe: LifecycleProbe, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("Service construction failed")

    def fail_service(*args: Any, **kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(app_module, "SystemService", fail_service)
    app = application(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must fail before serving")
    assert caught.value is failure
    assert probe.events[-4:] == ["engine.close", "http.close", "http.close", "database.close"]
    assert all(client.is_closed for client in probe.clients)
    assert_database_released(tmp_path, probe)


async def test_engine_close_failure_still_closes_http_and_database(
    tmp_path: Path, probe: LifecycleProbe
) -> None:
    failure = RuntimeError("Engine shutdown failed")
    probe.close_error = failure
    app = application(tmp_path)
    with pytest.raises(RuntimeError) as caught:
        async with app.router.lifespan_context(app):
            assert not probe.clients[0].is_closed
            assert app.state.database.stats()["briefs"] == 0
    assert caught.value is failure
    assert probe.events[-4:] == ["engine.close", "http.close", "http.close", "database.close"]
    assert all(client.is_closed for client in probe.clients)
    assert_database_released(tmp_path, probe)


async def test_normal_shutdown_closes_engine_before_http_before_database(
    tmp_path: Path, probe: LifecycleProbe
) -> None:
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        assert probe.events == ["database.open", "http.enter", "http.acquired", "http.enter", "http.acquired"]
        assert all(not client.is_closed for client in probe.clients)
    assert probe.events == [
        "database.open",
        "http.enter",
        "http.acquired",
        "http.enter",
        "http.acquired",
        "engine.close",
        "http.close",
        "http.close",
        "database.close",
    ]
    assert all(client.is_closed for client in probe.clients)
    assert_database_released(tmp_path, probe)
