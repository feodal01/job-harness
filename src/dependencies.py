from typing import Annotated, cast

from fastapi import Depends, Request

from src.services.auth import AuthService
from src.services.briefs import BriefService
from src.services.companies import CompanyCatalog
from src.services.geography import GeographyService
from src.services.lifecycle import LifecycleService
from src.services.runs import RunService
from src.services.sources import SourceService
from src.services.system import SystemService
from src.services.vacancies import VacancyService


async def get_briefs(request: Request) -> BriefService:
    return cast(BriefService, request.app.state.briefs_service)


async def get_vacancies(request: Request) -> VacancyService:
    return cast(VacancyService, request.app.state.vacancies_service)


async def get_runs(request: Request) -> RunService:
    return cast(RunService, request.app.state.runs_service)


async def get_sources(request: Request) -> SourceService:
    return cast(SourceService, request.app.state.sources_service)


async def get_geography(request: Request) -> GeographyService:
    return cast(GeographyService, request.app.state.geography_service)


async def get_system(request: Request) -> SystemService:
    return cast(SystemService, request.app.state.system_service)


BriefDep = Annotated[BriefService, Depends(get_briefs)]
VacancyDep = Annotated[VacancyService, Depends(get_vacancies)]
RunDep = Annotated[RunService, Depends(get_runs)]
SourceDep = Annotated[SourceService, Depends(get_sources)]
GeographyDep = Annotated[GeographyService, Depends(get_geography)]
SystemDep = Annotated[SystemService, Depends(get_system)]


async def get_lifecycle(request: Request) -> LifecycleService:
    return cast(LifecycleService, request.app.state.lifecycle_service)


LifecycleDep = Annotated[LifecycleService, Depends(get_lifecycle)]


async def get_auth(request: Request) -> AuthService:
    return cast(AuthService, request.app.state.auth_service)


async def get_company_catalog(request: Request) -> CompanyCatalog:
    return cast(CompanyCatalog, request.app.state.company_catalog)


AuthDep = Annotated[AuthService, Depends(get_auth)]
CompanyCatalogDep = Annotated[CompanyCatalog, Depends(get_company_catalog)]
