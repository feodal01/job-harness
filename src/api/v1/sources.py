from typing import Annotated

from fastapi import APIRouter, Query

from src.api.openapi import error_responses
from src.dependencies import SourceDep
from src.schemas.runs import EventResponse
from src.schemas.sources import (
    SourceCounts,
    SourceFilter,
    SourcePreview,
    SourceQuery,
    SourceRulesRequest,
    SourceSearchPage,
    SourceSearchRequest,
    SourcesPage,
)

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


@router.post("/preview", response_model=SourcePreview, responses=error_responses(422))
async def preview_sources(service: SourceDep, body: SourceRulesRequest) -> SourcePreview:
    """Что дадут правила источников для стран мест брифа: числа по типам и названия элементов.

    Правила могут быть ещё не сохранены. Сайты компаний читаются из каталога сейчас, с сессией;
    без входа их числа нулевые, а `catalog.status` объясняет почему. Ничего не осталось — это
    ответ с нулём, а не ошибка.
    """
    return SourcePreview.model_validate(await service.preview(body.source_rules, body.countries))


@router.post("/search", response_model=SourceSearchPage, responses=error_responses(422))
async def search_sources(service: SourceDep, body: SourceSearchRequest) -> SourceSearchPage:
    """Найти источник или компанию по названию и узнать, участвует ли он при этих правилах.

    `rule_item` — что положить в `include` или `exclude`, чтобы добавить, исключить или вернуть.
    """
    return SourceSearchPage.model_validate(
        await service.search(
            body.source_rules, body.countries, body.name, body.groups, body.page, body.page_size
        )
    )


@router.get(
    "/events",
    response_model=list[EventResponse],
    response_model_exclude_unset=True,
    responses=error_responses(422),
)
async def source_events(service: SourceDep, after: Annotated[int, Query(ge=0)] = 0) -> list[EventResponse]:
    """До 1000 общих событий источников с идентификатором больше after."""
    return [EventResponse.model_validate(item) for item in service.events(after)]
