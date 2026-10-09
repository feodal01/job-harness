"""Контекст операций и безопасные журналы HTTP-запросов."""

import asyncio
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTEXT_FIELDS = ("request_id", "run_id", "source_id", "stage")
_context: ContextVar[dict[str, str] | None] = ContextVar("log_context", default=None)
_request_id_pattern = re.compile(rb"[A-Za-z0-9._-]{1,64}")
_logger = logging.getLogger(__name__)


@contextmanager
def log_context(**fields: str) -> Iterator[None]:
    """Добавляет поля операции и восстанавливает предыдущий контекст при выходе."""
    unknown = fields.keys() - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown log context fields: {', '.join(sorted(unknown))}")
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = _context.get() or {}
        for field in CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, context.get(field, "-"))
        return True


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_ids = [value for key, value in scope.get("headers", []) if key.lower() == b"x-request-id"]
        request_id = (
            incoming_ids[0].decode("ascii")
            if len(incoming_ids) == 1 and _request_id_pattern.fullmatch(incoming_ids[0])
            else uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        response_status: int | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                message = {**message, "headers": [*headers, (b"x-request-id", request_id.encode("ascii"))]}
            await send(message)

        def fields(default_status: int) -> dict[str, str | int | float]:
            return {
                "method": scope["method"],
                "path": scope["path"],
                "status": response_status if response_status is not None else default_status,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            }

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except asyncio.CancelledError:
                _logger.info("request.cancelled", extra=fields(499))
                raise
            except Exception:
                _logger.exception("request.failed", extra=fields(500))
                raise
            else:
                _logger.info("request.finished", extra=fields(500))
