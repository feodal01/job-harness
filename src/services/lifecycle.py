from collections.abc import Callable

from src.core.errors import ConflictError
from src.engine import SearchEngine
from src.runtime.identity import application_version
from src.schemas.service import ServiceStatus


class LifecycleService:
    def __init__(
        self,
        engine: SearchEngine,
        shutdown: Callable[[], None] | None = None,
        instance_id: str | None = None,
        url: str | None = None,
    ) -> None:
        self.engine = engine
        self.shutdown = shutdown
        self.instance_id = instance_id
        self.url = url
        self.stopping = False

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            version=application_version(),
            status="stopping" if self.stopping else "running",
            managed=self.shutdown is not None,
            instance_id=self.instance_id,
            url=self.url,
            active_runs=[key for key, task in self.engine.tasks.items() if not task.done()],
        )

    def prepare_stop(self, cancel_active: bool) -> ServiceStatus:
        if self.shutdown is None:
            raise ConflictError("Этот сервис запущен без управления процессом. Остановите его в терминале.")
        active = self.status().active_runs
        if active and not cancel_active:
            raise ConflictError(f"Идут поиски ({len(active)}). Подтвердите их отмену: cancel_active=true.")
        self.stopping = True
        self.engine.stopping = True
        for run_id in active:
            self.engine.cancel(run_id)
        return self.status()

    def finish_stop(self) -> None:
        if self.shutdown is not None:
            self.shutdown()
