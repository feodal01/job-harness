from typing import Annotated

from fastapi import APIRouter, Query

from src.api.openapi import error_responses
from src.dependencies import GeographyDep
from src.schemas.geography import CityResponse, CountryResponse

router = APIRouter(prefix="/geography", tags=["geography"])


@router.get(
    "/countries",
    response_model=list[CountryResponse],
    response_model_exclude_unset=True,
    responses=error_responses(),
)
async def countries(service: GeographyDep) -> list[CountryResponse]:
    """Общий справочник стран для фильтров брифа."""
    return [CountryResponse.model_validate(item) for item in service.countries()]


@router.get(
    "/cities",
    response_model=list[CityResponse],
    response_model_exclude_unset=True,
    responses=error_responses(422),
)
async def cities(
    service: GeographyDep,
    country: Annotated[str, Query(min_length=2, max_length=2)],
    text: Annotated[str, Query(min_length=2, max_length=100)],
) -> list[CityResponse]:
    """До 20 городов указанной страны из локального справочника."""
    return [CityResponse.model_validate(item) for item in service.cities(country, text)]
