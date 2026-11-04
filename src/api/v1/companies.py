from typing import Annotated

from fastapi import APIRouter, Query

from src.api.openapi import error_responses
from src.dependencies import CompanyCatalogDep
from src.schemas.companies import CompanyFilterField, CompanyFiltersPage, CompanyLinksPage

router = APIRouter(prefix="/companies", tags=["companies"])
SearchText = Annotated[str | None, Query(min_length=1, max_length=200)]


@router.get("", response_model=CompanyLinksPage, responses=error_responses(401, 403, 502, 503))
async def list_companies(
    service: CompanyCatalogDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    name: SearchText = None,
    category: SearchText = None,
    specialization: SearchText = None,
) -> CompanyLinksPage:
    """Читать общий каталог с пользовательской сессией; поиск не запускается."""
    return await service.list(page, page_size, name, category, specialization)


@router.get("/filters", response_model=CompanyFiltersPage, responses=error_responses(401, 403, 502, 503))
async def get_company_filters(
    service: CompanyCatalogDep,
    field: CompanyFilterField = "category",
    category: SearchText = None,
    query: SearchText = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CompanyFiltersPage:
    """Получить актуальные значения фильтров; группировка и поиск выполняются в Supabase."""
    return await service.filters(field, page, page_size, category, query)
