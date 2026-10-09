from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from src.api.openapi import error_responses
from src.dependencies import LifecycleDep
from src.schemas.service import ServiceStatus, StopServiceInput

router = APIRouter(prefix="/service", tags=["service"])


@router.get("", response_model=ServiceStatus, responses=error_responses())
async def service_status(service: LifecycleDep) -> ServiceStatus:
    """Состояние локального процесса и активные поиски; без запуска новых задач."""
    return service.status()


@router.post("/stop", response_model=ServiceStatus, responses=error_responses(403, 409, 422))
async def stop_service(
    body: StopServiceInput,
    service: LifecycleDep,
    background: BackgroundTasks,
    instance_id: Annotated[str, Header(alias="X-Job-Search-Instance")],
) -> ServiceStatus:
    """Остановить этот экземпляр сервиса после ответа; активные поиски требуют подтверждения."""
    if instance_id != service.instance_id:
        raise HTTPException(403, "Экземпляр сервиса изменился. Обновите его состояние.")
    result = service.prepare_stop(body.cancel_active)
    background.add_task(service.finish_stop)
    return result
