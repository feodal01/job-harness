"""Translate expected application failures at the HTTP boundary."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core.errors import ConflictError, InvalidSearchError, NotFoundError
from src.services.auth import AuthError


async def missing(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


async def conflict(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=409)


async def invalid_search(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


async def auth_failure(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AuthError)
    return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)


async def unexpected(request: Request, exc: Exception) -> JSONResponse:
    # The request middleware logs the traceback; internal exceptions stay server-side.
    request_id = getattr(request.state, "request_id", None)
    headers = {"X-Request-Id": request_id} if request_id else None
    return JSONResponse({"detail": "Internal server error"}, status_code=500, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NotFoundError, missing)
    app.add_exception_handler(ConflictError, conflict)
    app.add_exception_handler(InvalidSearchError, invalid_search)
    app.add_exception_handler(AuthError, auth_failure)
    app.add_exception_handler(Exception, unexpected)
