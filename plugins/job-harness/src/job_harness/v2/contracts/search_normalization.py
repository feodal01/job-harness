"""Pure normalization helpers for public search contracts."""

from __future__ import annotations

from collections.abc import Iterable

from job_harness.v2.contracts.enums import WorkFormat
from job_harness.v2.geography import is_region_scope, normalize_request_geography


def validate_location_filters(
    *,
    work_formats: tuple[WorkFormat, ...],
    remote_scopes: tuple[str, ...],
    vacancy_geographies: tuple[str, ...],
    employer_geographies: tuple[str, ...],
) -> None:
    if WorkFormat.UNKNOWN in work_formats:
        raise ValueError("work_formats must not contain unknown")
    for field_name, values in (
        ("remote_scopes", remote_scopes),
        ("vacancy_geographies", vacancy_geographies),
        ("employer_geographies", employer_geographies),
    ):
        if "unknown" in values:
            raise ValueError(f"{field_name} must not contain unknown")
    if remote_scopes and WorkFormat.REMOTE not in work_formats:
        raise ValueError("remote_scopes require work_formats to include remote")


def clean_string_tuple(
    values: Iterable[str],
    field_name: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    if not cleaned and not allow_empty:
        raise ValueError(f"{field_name} must contain at least one non-empty value")
    return tuple(cleaned)


def dedupe_tuple[T](values: Iterable[T], field_name: str) -> tuple[T, ...]:
    result: list[T] = []
    for value in values:
        if value not in result:
            result.append(value)
    if any(value is None for value in result):
        raise ValueError(f"{field_name} must not contain None")
    return tuple(result)


def normalize_work_formats(values: Iterable[WorkFormat | str]) -> tuple[WorkFormat, ...]:
    result: list[WorkFormat] = []
    for raw in values:
        value = raw if isinstance(raw, WorkFormat) else WorkFormat(str(raw).strip())
        if value not in result:
            result.append(value)
    return tuple(result)


def normalize_scopes(values: Iterable[str], field_name: str, *, allow_city: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in clean_string_tuple(values, field_name, allow_empty=True):
        scope = _normalize_scope(raw, field_name=field_name, allow_city=allow_city)
        if scope.casefold() not in seen:
            seen.add(scope.casefold())
            normalized.append(scope)
    return tuple(normalized)


def _normalize_scope(value: str, *, field_name: str, allow_city: bool) -> str:
    if value == "unknown":
        raise ValueError(f"{field_name} must not contain unknown")
    if value == "global" and field_name == "remote_scopes":
        return value
    if allow_city and value.startswith("city:"):
        city = value.removeprefix("city:").strip()
        if city:
            return f"city:{city}"
        raise ValueError(f"{field_name} city scope must be non-empty")
    if value.startswith("country:"):
        geography = normalize_request_geography(value.removeprefix("country:"))
        if is_region_scope(geography):
            raise ValueError(f"{field_name} country scope must contain a country")
        return f"country:{geography}"
    if value.startswith("region:"):
        geography = normalize_request_geography(value.removeprefix("region:"))
        if not is_region_scope(geography):
            raise ValueError(f"{field_name} region scope must contain a region")
        return f"region:{geography}"
    expected = (
        "country:<code>, region:<code>, or city:<name>"
        if allow_city
        else "global, country:<code>, or region:<code>"
    )
    raise ValueError(f"{field_name} must use {expected}")
