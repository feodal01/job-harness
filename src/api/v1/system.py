from fastapi import APIRouter

from src.api.openapi import error_responses
from src.dependencies import SystemDep
from src.schemas.system import HealthResponse, StatsResponse

router = APIRouter(tags=["system"])


@router.get(
    "/health", response_model=HealthResponse, response_model_exclude_unset=True, responses=error_responses()
)
async def health(service: SystemDep) -> HealthResponse:
    """Доступность приложения и список подключённых адаптеров; без проверки сайтов источников."""
    return HealthResponse.model_validate(service.health())


@router.get(
    "/stats", response_model=StatsResponse, response_model_exclude_unset=True, responses=error_responses()
)
async def stats(service: SystemDep) -> StatsResponse:
    """Количество брифов, запусков, вакансий и событий в локальной базе."""
    return StatsResponse.model_validate(service.stats())
