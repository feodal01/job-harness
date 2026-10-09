"""Start/reuse one service across MCP sessions; never signal a PID from a stale file."""

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from pydantic import BaseModel, ValidationError

from src.runtime.config import RuntimeSettings
from src.runtime.identity import PROTOCOL_VERSION, application_version
from src.schemas.service import ServiceStatus


class ServiceError(RuntimeError):
    pass


class ServiceState(BaseModel):
    instance_id: str
    port: int
    pid: int


class ServiceManager:
    def __init__(self, settings: RuntimeSettings | None = None) -> None:
        self.settings = settings or RuntimeSettings()

    def _state(self) -> ServiceState | None:
        try:
            return ServiceState.model_validate_json(self.settings.state_path.read_text())
        except FileNotFoundError:
            return None
        except (ValidationError, ValueError) as exc:
            raise ServiceError(f"Повреждён файл состояния: {self.settings.state_path}") from exc

    def _service_lock_held(self) -> bool:
        try:
            lock = (self.settings.data_dir / "service.lock").open("r")
        except FileNotFoundError:
            return False
        with lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False

    async def status(self) -> ServiceStatus:
        state = self._state()
        stopped = ServiceStatus(version=application_version(), status="stopped")
        if state is None:
            return stopped
        url = f"http://127.0.0.1:{state.port}"
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            try:
                response = await client.get(f"{url}/api/v1/service")
            except (httpx.ConnectError, httpx.TimeoutException):
                return stopped
        try:
            response.raise_for_status()
            status = ServiceStatus.model_validate(response.json())
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise ServiceError(
                "Порт из файла состояния занят другим сервисом; он не будет остановлен."
            ) from exc
        if status.instance_id != state.instance_id or not status.managed:
            raise ServiceError("На этом порту другой экземпляр сервиса; он не будет остановлен.")
        return status

    @asynccontextmanager
    async def _launch_lock(self) -> AsyncIterator[None]:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.settings.data_dir / "launch.lock").open("a") as lock:
            deadline = time.monotonic() + self.settings.startup_seconds
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ServiceError(
                            "Другой запуск сервиса ещё не завершён. Повторите запрос."
                        ) from None
                    await asyncio.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    async def start(self) -> ServiceStatus:
        async with self._launch_lock():
            status = await self.status()
            if status.status == "stopping":
                raise ServiceError("Сервис ещё останавливается. Повторите запуск через несколько секунд.")
            if status.status == "running":
                self._check_version(status)
                return status
            if self._service_lock_held():
                raise ServiceError("Сервис запускается или завершает остановку. Повторите запрос позже.")
            env = os.environ.copy()
            env["JOB_SEARCH_DATA_DIR"] = str(self.settings.data_dir)
            env["JOB_SEARCH_PORT"] = str(self.settings.port)
            logfile = self.settings.data_dir / "service.log"
            startup_log = self.settings.data_dir / "startup.log"
            process = await asyncio.to_thread(self._spawn, env)
            deadline = time.monotonic() + self.settings.startup_seconds
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ServiceError(f"Не удалось запустить сервис. Подробности: {logfile}; {startup_log}")
                status = await self.status()
                if status.status == "running":
                    self._check_version(status)
                    return status
                await asyncio.sleep(0.1)
            # This is our own child, never a PID loaded from disk.
            process.terminate()
            await asyncio.to_thread(process.wait, 10)
            raise ServiceError(f"Сервис не запустился за отведённое время. Подробности: {logfile}")

    def _spawn(self, env: dict[str, str]) -> subprocess.Popen[bytes]:
        # Popen is intentional: an asyncio subprocess transport kills its child when
        # the MCP event loop closes. This service must outlive that loop.
        # Application logs rotate in the child. Capture only bootstrap failures;
        # replace this diagnostic file on each launch instead of growing forever.
        with (self.settings.data_dir / "startup.log").open("wb") as log:
            return subprocess.Popen(
                [sys.executable, "-m", "src.runtime"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=env,
                start_new_session=True,
            )

    @staticmethod
    def _check_version(status: ServiceStatus) -> None:
        if status.version != application_version() or status.protocol_version != PROTOCOL_VERSION:
            raise ServiceError(
                "Работает другая версия сервиса. Остановите её через stop_service, затем запустите снова."
            )

    async def request(
        self, method: str, path: str, *, body: object = None, params: dict | None = None
    ) -> object:
        status = await self.start()
        async with httpx.AsyncClient(base_url=str(status.url), timeout=30, trust_env=False) as client:
            try:
                response = await client.request(method, path, json=body, params=params)
            except httpx.RequestError as exc:
                # Never retry a write: it may already have been committed.
                raise ServiceError(
                    "Связь с сервисом потеряна. Проверьте его состояние перед повтором."
                ) from exc
        return self._response(response)

    @staticmethod
    def _response(response: httpx.Response) -> object:
        if response.is_error:
            try:
                detail = response.json().get("detail", "Ошибка сервиса")
            except ValueError:
                detail = "Ошибка сервиса"
            raise ServiceError(f"HTTP {response.status_code}: {json.dumps(detail, ensure_ascii=False)}")
        return response.json()

    async def stop(self, cancel_active: bool = False) -> ServiceStatus:
        async with self._launch_lock():
            status = await self.status()
            if status.status == "stopped" and not self._service_lock_held():
                return status
            if status.status == "running":
                async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                    response = await client.post(
                        f"{status.url}/api/v1/service/stop",
                        json={"cancel_active": cancel_active},
                        headers={"X-Job-Search-Instance": str(status.instance_id)},
                    )
                self._response(response)
            for _ in range(150):
                if (await self.status()).status == "stopped" and not self._service_lock_held():
                    return ServiceStatus(version=application_version(), status="stopped")
                await asyncio.sleep(0.1)
            raise ServiceError("Остановка ещё не завершена. Проверьте состояние сервиса.")
