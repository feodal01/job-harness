"""Единый формат логов: stdout для встраивания, ротируемый файл для сервиса."""

import json
import logging
import logging.config
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from src.core.config import Settings
from src.core.observability import CONTEXT_FIELDS, ContextFilter

_OPERATION_FIELDS = ("method", "path", "status", "duration_ms")


class SafeFormatter(logging.Formatter):
    def formatException(  # noqa: N802
        self,
        ei: tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None],
    ) -> str:
        """Пишет стек и тип ошибки без сообщения, которое может содержать входные данные."""
        exception_type, exception, tb = ei
        if exception is None:
            return exception_type.__name__ if exception_type is not None else "UnknownError"
        return self._exception_stack(exception, tb, set())

    def _exception_stack(self, exception: BaseException, tb: TracebackType | None, seen: set[int]) -> str:
        if id(exception) in seen:
            return "[exception already shown]"
        seen.add(id(exception))
        parts: list[str] = []
        cause = exception.__cause__
        context = exception.__context__
        if cause is not None:
            parts.extend([self._exception_stack(cause, cause.__traceback__, seen), "Caused by:"])
        elif context is not None and not exception.__suppress_context__:
            parts.extend([self._exception_stack(context, context.__traceback__, seen), "During handling:"])
        parts.append("Traceback (most recent call last):")
        parts.extend(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}'
            for frame in traceback.extract_tb(tb)
        )
        parts.append(type(exception).__name__)
        if isinstance(exception, BaseExceptionGroup):
            for index, child in enumerate(exception.exceptions, 1):
                parts.extend(
                    [
                        f"Group exception {index}:",
                        self._exception_stack(child, child.__traceback__, seen),
                    ]
                )
        return "\n".join(parts)


class TextFormatter(SafeFormatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        fields = {
            field: getattr(record, field)
            for field in (*CONTEXT_FIELDS, *_OPERATION_FIELDS)
            if hasattr(record, field)
        }
        details = " ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
        result = f"{timestamp} {record.levelname} {record.name} {record.getMessage()} {details}"
        if record.exc_info:
            result += "\n" + self.formatException(record.exc_info)
        return result


class JsonFormatter(SafeFormatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (*CONTEXT_FIELDS, *_OPERATION_FIELDS):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings, logfile: Path | None = None) -> None:
    destination = (
        {"class": "logging.StreamHandler", "stream": "ext://sys.stdout"}
        if logfile is None
        else {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(logfile),
            "maxBytes": settings.log_max_bytes,
            "backupCount": settings.log_backup_count,
            "encoding": "utf-8",
        }
    )
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"context": {"()": ContextFilter}},
            "formatters": {
                "application": {"()": JsonFormatter if settings.log_format == "json" else TextFormatter}
            },
            "handlers": {
                "application": {
                    **destination,
                    "formatter": "application",
                    "filters": ["context"],
                    "level": settings.log_level,
                },
                "discard": {"class": "logging.NullHandler"},
            },
            "root": {"handlers": ["application"], "level": settings.log_level},
            "loggers": {
                "uvicorn": {"handlers": [], "level": settings.log_level, "propagate": True},
                "uvicorn.error": {"handlers": [], "level": settings.log_level, "propagate": True},
                # Middleware emits one access record, without URL query parameters.
                "uvicorn.access": {"handlers": ["discard"], "propagate": False},
                "httpx": {"handlers": [], "level": "WARNING", "propagate": True},
                "httpcore": {"handlers": [], "level": "WARNING", "propagate": True},
            },
        }
    )
