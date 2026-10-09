"""Foreground service runner, with a lock held until SQLite and all tasks close."""

import fcntl
import logging
import os
import socket
import uuid

import uvicorn

from src.app import create_app
from src.core.config import Settings
from src.core.logger import configure_logging
from src.runtime.config import RuntimeSettings
from src.runtime.manager import ServiceState

logger = logging.getLogger(__name__)


def serve(runtime: RuntimeSettings | None = None, settings: Settings | None = None) -> None:
    runtime = runtime or RuntimeSettings()
    settings = settings or Settings(database_path=runtime.data_dir / "search.sqlite")
    runtime.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (runtime.data_dir / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("This data directory already has a running service") from None
        configure_logging(settings, runtime.data_dir / "service.log")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", runtime.port))
            instance_id = uuid.uuid4().hex

            def shutdown() -> None:
                server.should_exit = True

            app = create_app(settings, shutdown=shutdown, instance_id=instance_id, url=runtime.url)
            server = uvicorn.Server(
                uvicorn.Config(app, access_log=False, log_config=None, timeout_graceful_shutdown=10)
            )
            state = ServiceState(instance_id=instance_id, port=runtime.port, pid=os.getpid())
            temporary = runtime.state_path.with_suffix(".tmp")
            temporary.write_text(state.model_dump_json())
            temporary.replace(runtime.state_path)
            try:
                server.run(sockets=[sock])
            finally:
                runtime.state_path.unlink(missing_ok=True)


if __name__ == "__main__":
    serve()
