"""Real subprocess/HTTP checks; run with local socket access."""

import asyncio
import json
import socket
from pathlib import Path

import httpx
import pytest
from geo_support import confirmed

from src.runtime.config import RuntimeSettings
from src.runtime.manager import ServiceError, ServiceManager


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeSettings:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return RuntimeSettings(data_dir=tmp_path, port=port, startup_seconds=15)


async def test_concurrent_start_single_process_and_persistent_restart(runtime: RuntimeSettings) -> None:
    one, two = ServiceManager(runtime), ServiceManager(runtime)
    assert (await one.status()).status == "stopped"
    assert not runtime.state_path.exists()
    try:
        a, b = await asyncio.gather(one.start(), two.start())
        assert a.instance_id == b.instance_id
        first_pid = json.loads(runtime.state_path.read_text())["pid"]
        created = await one.request(
            "POST", "/api/v1/briefs", body={"name": "Persist", "queries": ["python"], "filters": confirmed()}
        )
        assert created["id"]
        stopped = await two.stop()
        assert stopped.status == "stopped"
        stopped_again = await one.stop()
        assert stopped_again.status == "stopped"
        restarted = await one.start()
        assert restarted.instance_id != a.instance_id
        assert json.loads(runtime.state_path.read_text())["pid"] != first_pid
        briefs = await two.request("GET", "/api/v1/briefs")
        assert briefs[0]["id"] == created["id"]
    finally:
        await one.stop(cancel_active=True)


async def test_stale_instance_cannot_stop_another_service(runtime: RuntimeSettings) -> None:
    manager = ServiceManager(runtime)
    try:
        started = await manager.start()
        state = runtime.state_path.read_text()
        altered = json.loads(state)
        altered["instance_id"] = "stale"
        runtime.state_path.write_text(json.dumps(altered))
        try:
            with pytest.raises(ServiceError, match="другой экземпляр"):
                await manager.stop(True)
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.get(f"{started.url}/api/v1/health")).status_code == 200
        finally:
            runtime.state_path.write_text(state)
    finally:
        await manager.stop(True)


async def test_cli_and_mcp_manager_reuse_same_service(runtime: RuntimeSettings) -> None:
    import os
    import sys

    env = {**os.environ, "JOB_SEARCH_DATA_DIR": str(runtime.data_dir), "JOB_SEARCH_PORT": str(runtime.port)}
    manager = ServiceManager(runtime)
    try:
        for _ in range(2):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "src",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
            assert process.returncode == 0, stderr.decode()
            assert runtime.url in stdout.decode()
            status = await manager.start()
            if _ == 0:
                first_id = status.instance_id
            else:
                assert status.instance_id == first_id
        assert (runtime.data_dir / "search.sqlite").exists()
    finally:
        await manager.stop(True)


def test_application_and_runtime_use_same_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import Settings

    monkeypatch.setenv("JOB_SEARCH_DATA_DIR", str(tmp_path / "shared"))
    assert Settings().database_path == RuntimeSettings().data_dir / "search.sqlite"


async def test_service_rotates_logs_without_redirecting_stdout_to_same_file(
    runtime: RuntimeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOB_SEARCH_LOG_MAX_BYTES", "2048")
    monkeypatch.setenv("JOB_SEARCH_LOG_BACKUP_COUNT", "2")
    monkeypatch.setenv("JOB_SEARCH_LOG_FORMAT", "json")
    manager = ServiceManager(runtime)
    try:
        status = await manager.start()
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(30):
                assert (await client.get(f"{status.url}/api/v1/health")).status_code == 200
    finally:
        await manager.stop(True)
    files = sorted(runtime.data_dir.glob("service.log*"))
    assert [p.name for p in files] == ["service.log", "service.log.1", "service.log.2"]
    assert all(0 < p.stat().st_size <= 2048 for p in files)
    records = [json.loads(line) for p in files for line in p.read_text().splitlines()]
    assert any(r["message"] == "application.stopped" for r in records)
    assert (runtime.data_dir / "startup.log").read_text() == ""
