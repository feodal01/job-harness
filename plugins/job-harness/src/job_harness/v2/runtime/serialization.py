"""JSON serialization helpers for strict contract records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, cast


def to_jsonable(value: object) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(cast(Any, value)))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, frozenset):
        return [to_jsonable(item) for item in sorted(value, key=_sort_key)]
    if isinstance(value, set):
        return [to_jsonable(item) for item in sorted(value, key=_sort_key)]
    if isinstance(value, tuple | list):
        return [to_jsonable(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _sort_key(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
