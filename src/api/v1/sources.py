from typing import Annotated

from fastapi import APIRouter, Query

from src.api.openapi import error_responses
from src.dependencies import SourceDep
from src.schemas.runs import EventResponse
from src.schemas.sources import SourceResponse

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get(
    "",
    response_model=list[SourceResponse],
    response_model_exclude_unset=True,
    responses=error_responses(),
)
async def list_sources(service: SourceDep) -> list[SourceResponse]:
    """Настроенные источники, их возможности и известное локальное состояние без сетевой проверки."""
    return [SourceResponse.model_validate(item) for item in service.list_sources()]


@router.get(
    "/events",
    response_model=list[EventResponse],
    response_model_exclude_unset=True,
    responses=error_responses(422),
)
async def source_events(service: SourceDep, after: Annotated[int, Query(ge=0)] = 0) -> list[EventResponse]:
    """До 1000 общих событий источников с идентификатором больше after."""
    return [EventResponse.model_validate(item) for item in service.events(after)]
