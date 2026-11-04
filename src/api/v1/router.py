from fastapi import APIRouter

from src.api.v1 import auth, briefs, companies, geography, runs, service, sources, system

router = APIRouter(prefix="/api/v1")
router.include_router(briefs.router)
router.include_router(runs.router)
router.include_router(system.router)
router.include_router(sources.router)
router.include_router(geography.router)
router.include_router(service.router)
router.include_router(auth.router)
router.include_router(companies.router)
