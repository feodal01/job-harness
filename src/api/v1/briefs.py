from typing import Annotated, Literal

from fastapi import APIRouter, Query

from src.api.openapi import error_responses
from src.dependencies import BriefDep, VacancyDep
from src.models.brief import BriefInput
from src.schemas.briefs import BriefOverview, BriefResponse
from src.schemas.vacancies import (
    PoolStatusInput,
    PoolVacancy,
    PoolVacancyPage,
    PoolView,
    SelectionView,
    VacancyNavigation,
)

router = APIRouter(prefix="/briefs", tags=["briefs"])


@router.get(
    "", response_model=list[BriefOverview], response_model_exclude_unset=True, responses=error_responses()
)
async def list_briefs(service: BriefDep) -> list[BriefOverview]:
    """Список брифов с количеством вакансий по статусам и последним запуском."""
    return [BriefOverview.model_validate(item) for item in service.list_briefs()]


@router.post(
    "",
    status_code=201,
    response_model=BriefResponse,
    response_model_exclude_unset=True,
    responses=error_responses(403, 422),
)
async def create_brief(brief: BriefInput, service: BriefDep) -> BriefResponse:
    """Создать бриф и сохранить общие фильтры и параметры поиска."""
    return BriefResponse.model_validate(service.create(brief))


@router.put(
    "/{brief_id}",
    response_model=BriefResponse,
    response_model_exclude_unset=True,
    responses=error_responses(403, 404, 422),
)
async def update_brief(brief_id: str, brief: BriefInput, service: BriefDep) -> BriefResponse:
    """Сохранить бриф и пересчитать отбор уже найденных вакансий."""
    return BriefResponse.model_validate(service.update(brief_id, brief))


@router.get(
    "/{brief_id}/vacancies",
    response_model=PoolVacancyPage,
    response_model_exclude_unset=True,
    responses=error_responses(404, 422),
)
async def list_pool(
    brief_id: str,
    service: VacancyDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    selection: SelectionView = "candidates",
    status: PoolView = "all",
    q: Annotated[str, Query(max_length=200)] = "",
    sort: Literal["first_seen", "newest", "updated"] = "first_seen",
) -> PoolVacancyPage:
    """Страница общего списка вакансий брифа с отбором и пользовательскими статусами."""
    return PoolVacancyPage.model_validate(
        service.list_vacancies(brief_id, page, page_size, selection, status, q, sort)
    )


@router.get(
    "/{brief_id}/vacancies/{vacancy_key}/adjacent",
    response_model=VacancyNavigation,
    response_model_exclude_unset=True,
    responses=error_responses(404, 422),
)
async def adjacent_pool_item(
    brief_id: str,
    vacancy_key: str,
    service: VacancyDep,
    direction: Literal["next", "previous"] = "next",
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    selection: SelectionView = "candidates",
    status: PoolView = "all",
    q: Annotated[str, Query(max_length=200)] = "",
    sort: Literal["first_seen", "newest", "updated"] = "first_seen",
    anchor_time: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
) -> VacancyNavigation:
    """Соседняя вакансия и её страница с учётом текущих фильтров и порядка."""
    return VacancyNavigation.model_validate(
        service.adjacent(brief_id, vacancy_key, direction, page_size, selection, status, q, sort, anchor_time)
    )


@router.get(
    "/{brief_id}/vacancies/{vacancy_key}",
    response_model=PoolVacancy,
    response_model_exclude_unset=True,
    responses=error_responses(404),
)
async def get_pool_item(brief_id: str, vacancy_key: str, service: VacancyDep) -> PoolVacancy:
    """Карточка вакансии брифа с происхождением и историей появления."""
    return PoolVacancy.model_validate(service.get(brief_id, vacancy_key))


@router.patch(
    "/{brief_id}/vacancies/{vacancy_key}",
    response_model=PoolVacancy,
    response_model_exclude_unset=True,
    responses=error_responses(403, 404, 422),
)
async def set_pool_status(
    brief_id: str, vacancy_key: str, body: PoolStatusInput, service: VacancyDep
) -> PoolVacancy:
    """Обновить пользовательский статус и заметку вакансии."""
    return PoolVacancy.model_validate(service.set_status(brief_id, vacancy_key, body.status, body.note))
