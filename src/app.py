"""Application composition and resource lifecycle."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api.errors import register_error_handlers
from src.api.v1.router import router as api_v1_router
from src.auth.storage import SessionStore
from src.core.config import Settings
from src.core.observability import RequestLoggingMiddleware
from src.db import Database
from src.dependencies import AuthDep
from src.engine import SearchEngine
from src.enrichment.registry import Enrichment
from src.geography.catalog import default_catalog
from src.parsers.registry import build_sources
from src.runtime.config import RuntimeSettings
from src.services.auth import AuthService
from src.services.briefs import BriefService
from src.services.companies import CompanyCatalog
from src.services.geography import GeographyService
from src.services.lifecycle import LifecycleService
from src.services.runs import RunService
from src.services.sources import SourceService
from src.services.system import SystemService
from src.services.vacancies import VacancyService
from src.web.auth import router as auth_pages
from src.web.auth import signed_in

logger = logging.getLogger(__name__)
WEB_ROOT = Path(__file__).parent / "web"


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    shutdown: Callable[[], None] | None = None,
    instance_id: str | None = None,
    url: str | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as resources:
            # One geography catalog and one enrichment for briefs, API, sources and the engine.
            geography = default_catalog()
            enrichment = Enrichment(geography)
            sources = build_sources(settings, geography)
            database = Database(settings.database_path)
            resources.callback(database.close)
            client = httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                },
            )
            resources.push_async_callback(client.aclose)
            await client.__aenter__()
            auth_client = httpx.AsyncClient(transport=transport, follow_redirects=False)
            resources.push_async_callback(auth_client.aclose)
            await auth_client.__aenter__()
            auth = AuthService(
                settings.auth,
                SessionStore(settings.database_path.parent / "auth" / "session.json"),
                auth_client,
                url or RuntimeSettings().url,
            )
            app.state.auth_service = auth
            app.state.company_catalog = CompanyCatalog(auth)
            engine = SearchEngine(settings, database, sources, client, enrichment)
            resources.push_async_callback(engine.close)
            app.state.database = database
            app.state.engine = engine
            app.state.briefs_service = BriefService(
                database.briefs, database.vacancies, database.transaction, sources, geography, enrichment
            )
            app.state.vacancies_service = VacancyService(database.vacancies)
            app.state.runs_service = RunService(
                database.runs, database.vacancies, database.events, engine, app.state.briefs_service
            )
            app.state.sources_service = SourceService(sources, database.resources, database.events)
            app.state.geography_service = GeographyService(geography)
            app.state.system_service = SystemService(database.stats, sources)
            app.state.lifecycle_service = LifecycleService(engine, shutdown, instance_id, url)
            database.runs.recover_unfinished()
            logger.info("application.started")
            try:
                yield
            finally:
                logger.info("application.stopping")
        logger.info("application.stopped")

    app = FastAPI(title="Job Search Prototype", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    app.include_router(api_v1_router)
    app.include_router(auth_pages)
    register_error_handlers(app)
    app.mount("/assets", StaticFiles(directory=WEB_ROOT / "static"), name="assets")

    @app.middleware("http")
    async def local_origin(request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith(("/auth", "/api/v1/auth", "/api/v1/companies")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(service: AuthDep) -> Response:
        # The web app is closed behind the login screen; the REST API and MCP keep their own checks.
        if not await signed_in(service):
            return RedirectResponse(service.login_url, status_code=303)
        return HTMLResponse((WEB_ROOT / "index.html").read_text())

    return app
