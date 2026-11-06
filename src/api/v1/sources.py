from typing import Annotated

from fastapi import APIRouter, Query

from src.api.openapi import error_responses
from src.dependencies import SourceDep
from src.schemas.runs import EventResponse
from src.schemas.sources import SourceCounts, SourceFilter, SourceQuery, SourcesPage

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=SourcesPage, response_model_exclude_unset=True, responses=error_responses(422))
async def list_sources(service: SourceDep, query: Annotated[SourceQuery, Query()]) -> SourcesPage:
    """Источники по фильтрам: встроенные площадки и сайты компаний из каталога.

    Сайты компаний читаются из каталога сейчас, с сессией пользователя. Без входа ответ содержит
    остальные источники, а `catalog.status` объясняет, почему сайтов компаний нет.
    """
    filters = SourceFilter.model_validate(query.model_dump(exclude={"page", "page_size"}))
    return SourcesPage.model_validate(await service.list_sources(filters, query.page, query.page_size))


@router.get("/counts", response_model=SourceCounts, responses=error_responses(422))
async def count_sources(service: SourceDep, filters: Annotated[SourceFilter, Query()]) -> SourceCounts:
    """Число поддерживаемых и неподдерживаемых источников по группам для тех же фильтров."""
    return SourceCounts.model_validate(await service.counts(filters))


@router.get(
    "/events",
    response_model=list[EventResponse],
    response_model_exclude_unset=True,
    responses=error_responses(422),
)
async def source_events(service: SourceDep, after: Annotated[int, Query(ge=0)] = 0) -> list[EventResponse]:
    """До 1000 общих событий источников с идентификатором больше after."""
    return [EventResponse.model_validate(item) for item in service.events(after)]
