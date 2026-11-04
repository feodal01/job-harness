import asyncio
from pathlib import Path

import httpx
import pytest
from geo_support import confirmed

from src.app import create_app
from src.core.config import Settings
from src.core.errors import ConflictError
from src.models.brief import BriefInput
from src.models.run import RunInput


async def test_stop_requires_instance_and_confirmation_and_blocks_new_runs(tmp_path: Path) -> None:
    stopped = []
    app = create_app(
        Settings(database_path=tmp_path / "db.sqlite"),
        shutdown=lambda: stopped.append(True),
        instance_id="this-instance",
        url="http://127.0.0.1:8765",
    )
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        # A real pending task exercises active-run detection without external requests.
        task = asyncio.create_task(asyncio.Event().wait())
        engine.tasks["active"] = task
        cancellations = []
        original_cancel = engine.cancel
        engine.cancel = lambda key: (cancellations.append(key), task.cancel())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            assert (await c.get("/api/v1/service")).json()["active_runs"] == ["active"]
            assert (await c.post("/api/v1/service/stop", json={})).status_code == 422
            assert (
                await c.post(
                    "/api/v1/service/stop",
                    json={},
                    headers={
                        "X-Job-Search-Instance": "old-instance",
                    },
                )
            ).status_code == 403
            headers = {"X-Job-Search-Instance": "this-instance"}
            refused = await c.post("/api/v1/service/stop", json={}, headers=headers)
            assert refused.status_code == 409
            assert not stopped and not cancellations and not engine.stopping
            cross_origin = await c.post(
                "/api/v1/service/stop",
                json={"cancel_active": True},
                headers={
                    **headers,
                    "Origin": "https://another-site.example",
                },
            )
            assert cross_origin.status_code == 403
            response = await c.post("/api/v1/service/stop", json={"cancel_active": True}, headers=headers)
            assert response.status_code == 200 and response.json()["status"] == "stopping"
            assert stopped == [True] and cancellations == ["active"]
            with pytest.raises(ConflictError, match="останавливается"):
                engine.start(RunInput(brief_id="anything"))
        engine.cancel = original_cancel
        await asyncio.gather(task, return_exceptions=True)


async def test_unmanaged_asgi_application_cannot_be_stopped(tmp_path: Path) -> None:
    app = create_app(Settings(database_path=tmp_path / "db.sqlite"))
    async with app.router.lifespan_context(app):
        status = app.state.lifecycle_service.status()
        assert not status.managed
        with pytest.raises(ConflictError, match="терминале"):
            app.state.lifecycle_service.prepare_stop(False)


async def test_confirmed_shutdown_persists_cancelled_run(tmp_path: Path) -> None:
    entered = asyncio.Event()

    async def stalled_source(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        return httpx.Response(200)

    settings = Settings(database_path=tmp_path / "db.sqlite")
    app = create_app(settings, transport=httpx.MockTransport(stalled_source), shutdown=lambda: None)
    async with app.router.lifespan_context(app):
        brief = app.state.briefs_service.create(
            BriefInput(name="Stop test", queries=["Python"], filters=confirmed())
        )
        run = app.state.runs_service.start(RunInput(brief_id=brief["id"], sources=["hh_ru"]))
        await asyncio.wait_for(entered.wait(), 5)
        app.state.lifecycle_service.prepare_stop(True)
        await app.state.engine.close()
        assert app.state.runs_service.get(run["id"])["status"] == "cancelled"
    reopened = create_app(settings)
    async with reopened.router.lifespan_context(reopened):
        assert reopened.state.runs_service.get(run["id"])["status"] == "cancelled"
