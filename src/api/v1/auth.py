from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from src.api.openapi import error_responses
from src.dependencies import AuthDep
from src.schemas.auth import AuthProviders, AuthStatus, LoginRedirect, StartLoginInput

router = APIRouter(prefix="/auth", tags=["auth"])


def browser_action(request: Request, header: str | None) -> None:
    origin = f"{request.url.scheme}://{request.headers['host']}"
    if header != "1" or request.headers.get("origin") != origin:
        raise HTTPException(403, "Откройте страницу входа в этом браузере.")


@router.get("", response_model=AuthStatus, responses=error_responses(409, 502, 503))
async def auth_status(service: AuthDep) -> AuthStatus:
    """Общая сессия браузера и MCP; токены в ответ не включаются."""
    return await service.status()


@router.post("/authorize", response_model=AuthStatus, responses=error_responses(409, 502, 503))
async def authorize(service: AuthDep) -> AuthStatus:
    """Вернуть действующую сессию или постоянную ссылку на выбор способа входа."""
    return await service.status()


@router.get("/providers", response_model=AuthProviders, responses=error_responses(502))
async def providers(service: AuthDep) -> AuthProviders:
    """Включённые в Supabase и поддерживаемые приложением способы входа."""
    return await service.providers()


@router.post("/start", response_model=LoginRedirect, responses=error_responses(403, 422, 429, 502))
async def start_login(
    body: StartLoginInput,
    request: Request,
    service: AuthDep,
    action: Annotated[str | None, Header(alias="X-Job-Search-Auth")] = None,
) -> LoginRedirect:
    """Начать OAuth только после выбора провайдера на странице входа."""
    browser_action(request, action)
    browser_id = request.cookies.get(service.cookie_name)
    if not browser_id:
        raise HTTPException(403, "Обновите страницу входа и повторите попытку.")
    return LoginRedirect(url=await service.start_login(body.provider, browser_id))


@router.post("/logout", response_model=AuthStatus, responses=error_responses(403, 502))
async def logout(
    request: Request,
    service: AuthDep,
    action: Annotated[str | None, Header(alias="X-Job-Search-Auth")] = None,
) -> AuthStatus:
    """Удалить локальную сессию и завершить её в Supabase, сохранив брифы и результаты."""
    browser_action(request, action)
    return await service.logout()
