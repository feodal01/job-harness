import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.dependencies import AuthDep
from src.services.auth import AuthError, AuthService

router = APIRouter(include_in_schema=False)
PAGE = Path(__file__).parent / "auth.html"


async def signed_in(service: AuthService) -> bool:
    try:
        return (await service.status()).authenticated
    except AuthError:
        # The login page shows the reason and a retry; the app stays closed meanwhile.
        return False


@router.get("/auth")
async def login_page(request: Request, service: AuthDep) -> Response:
    if str(request.base_url).rstrip("/") != service.base_url:
        return RedirectResponse(service.login_url, status_code=303)
    if await signed_in(service):
        return RedirectResponse(service.base_url + "/", status_code=303)
    response = HTMLResponse(PAGE.read_text())
    browser_id = request.cookies.get(service.cookie_name, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser_id):
        browser_id = secrets.token_urlsafe(32)
    response.set_cookie(
        service.cookie_name,
        browser_id,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


@router.get("/auth/callback")
async def callback(request: Request, service: AuthDep) -> RedirectResponse:
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    browser_id = request.cookies.get(service.cookie_name, "")
    outcome = "failed"
    if request.query_params.get("error_code") == "bad_oauth_state":
        outcome = "expired"
    elif request.query_params.get("error") == "access_denied":
        outcome = "cancelled"
    elif code and state and browser_id:
        try:
            await service.finish_login(browser_id, state, code)
        except AuthError:
            outcome = "failed"
        else:
            outcome = "success"
    response = RedirectResponse(service.login_url + "?result=" + outcome, status_code=303)
    response.delete_cookie(service.cookie_name)
    return response
