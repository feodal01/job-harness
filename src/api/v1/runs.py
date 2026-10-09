from typing import Annotated

from fastapi import APIRouter, Query

from src.api.openapi import error_responses
from src.dependencies import RunDep
from src.models.run import RunInput
from src.schemas.runs import EventResponse, RunResponse
from src.schemas.vacancies import RunVacancyPage, SelectionView

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get(
    "", response_model=list[RunResponse], response_model_exclude_unset=True, responses=error_responses()
)
async def list_runs(service: RunDep, brief_id: str | None = None) -> list[RunResponse]:
    """Последние 100 запусков, при необходимости для одного брифа."""
    return [RunResponse.model_validate(item) for item in service.list_runs(brief_id)]


@router.post(
    "",
    status_code=202,
    response_model=RunResponse,
    response_model_exclude_unset=True,
    responses=error_responses(403, 404, 409, 422),
)
async def launch_run(request: RunInput, service: RunDep) -> RunResponse:
    """Запустить поиск и вернуть сохранённый снимок его параметров."""
    return RunResponse.model_validate(service.start(request))


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    response_model_exclude_unset=True,
    responses=error_responses(404),
)
async def get_run(run_id: str, service: RunDep) -> RunResponse:
    """Состояние запуска, статистика и состояния источников."""
    return RunResponse.model_validate(service.get(run_id))


@router.post(
    "/{run_id}/cancel",
    response_model=RunResponse,
    response_model_exclude_unset=True,
    responses=error_responses(403, 404),
)
async def cancel_run(run_id: str, service: RunDep) -> RunResponse:
    """Запросить отмену незавершённого запуска и вернуть его состояние."""
    return RunResponse.model_validate(service.cancel(run_id))


@router.get(
    "/{run_id}/results",
    response_model=RunVacancyPage,
    response_model_exclude_unset=True,
    responses=error_responses(404, 422),
)
async def list_results(
    run_id: str,
    service: RunDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    selection: SelectionView = "all",
) -> RunVacancyPage:
    """Страница сохранённых результатов запуска без пересчёта исторического отбора."""
    return RunVacancyPage.model_validate(service.results(run_id, page, page_size, selection))


@router.get(
    "/{run_id}/events",
    response_model=list[EventResponse],
    response_model_exclude_unset=True,
    responses=error_responses(404, 422),
)
async def list_events(
    run_id: str, service: RunDep, after: Annotated[int, Query(ge=0)] = 0
) -> list[EventResponse]:
    """До 1000 событий запуска с идентификатором больше after."""
    return [EventResponse.model_validate(item) for item in service.events(run_id, after)]
