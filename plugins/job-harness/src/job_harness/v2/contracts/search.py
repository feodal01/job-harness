"""Validated public search request contract."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from job_harness.v2.contracts.enums import (
    Grade,
    SearchCriterion,
    SourceType,
    TextExclusionMode,
    TextField,
    WorkFormat,
)
from job_harness.v2.geography import is_region_scope, normalize_request_geography


@dataclass(frozen=True)
class TextExclusion:
    pattern: str
    mode: TextExclusionMode = TextExclusionMode.SUBSTRING
    case_sensitive: bool = False
    fields: tuple[TextField, ...] = ()

    def __post_init__(self) -> None:
        pattern = self.pattern.strip()
        if not pattern:
            raise ValueError("TextExclusion.pattern must be non-empty")
        object.__setattr__(self, "pattern", pattern)
        object.__setattr__(self, "fields", _dedupe_tuple(self.fields, "fields"))


@dataclass(frozen=True)
class SearchRequest:
    query_variants: tuple[str, ...]
    grades: tuple[Grade, ...] = ()
    salary_from: int | None = None
    published_since: date | None = None
    exclude_companies: tuple[str, ...] = ()
    exclude_text: tuple[TextExclusion, ...] = ()
    relocation: bool | None = None
    work_formats: tuple[WorkFormat, ...] = ()
    remote_scopes: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    source_types: tuple[SourceType, ...] = ()
    append_to_run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "query_variants",
            _clean_string_tuple(self.query_variants, "query_variants", allow_empty=False),
        )
        object.__setattr__(self, "grades", _dedupe_tuple(self.grades, "grades"))
        object.__setattr__(
            self,
            "exclude_companies",
            _clean_string_tuple(
                self.exclude_companies,
                "exclude_companies",
                allow_empty=True,
            ),
        )
        object.__setattr__(self, "exclude_text", _dedupe_tuple(self.exclude_text, "exclude_text"))
        object.__setattr__(
            self,
            "work_formats",
            _normalize_work_formats(self.work_formats),
        )
        object.__setattr__(
            self,
            "remote_scopes",
            _normalize_scopes(self.remote_scopes, "remote_scopes", allow_city=False),
        )
        object.__setattr__(
            self,
            "vacancy_geographies",
            _normalize_scopes(self.vacancy_geographies, "vacancy_geographies", allow_city=True),
        )
        object.__setattr__(self, "sources", _clean_string_tuple(self.sources, "sources", allow_empty=True))
        object.__setattr__(self, "source_types", _dedupe_tuple(self.source_types, "source_types"))

        if self.salary_from is not None and self.salary_from < 1:
            raise ValueError("salary_from must be >= 1 when provided")
        if self.work_formats == (WorkFormat.UNKNOWN,):
            raise ValueError(
                "work_formats cannot contain only unknown; include a concrete work format or omit the filter"
            )
        if self.remote_scopes == ("unknown",):
            raise ValueError(
                "remote_scopes cannot contain only unknown; include a concrete remote scope or omit the filter"
            )
        if self.vacancy_geographies == ("unknown",):
            raise ValueError(
                "vacancy_geographies cannot contain only unknown; "
                "include a concrete vacancy geography or omit the filter"
            )
        if self.remote_scopes and WorkFormat.REMOTE not in self.work_formats:
            raise ValueError("remote_scopes require work_formats to include remote")
        if self.append_to_run_id is not None and not self.append_to_run_id.strip():
            raise ValueError("append_to_run_id must be non-empty when provided")

    @property
    def requested_criteria(self) -> frozenset[SearchCriterion]:
        criteria = {SearchCriterion.QUERY}
        if self.grades:
            criteria.add(SearchCriterion.GRADES)
        if self.salary_from is not None:
            criteria.add(SearchCriterion.SALARY_FROM)
        if self.published_since is not None:
            criteria.add(SearchCriterion.PUBLISHED_SINCE)
        if self.relocation is not None:
            criteria.add(SearchCriterion.RELOCATION)
        if self.work_formats:
            criteria.add(SearchCriterion.WORK_FORMATS)
        if self.remote_scopes:
            criteria.add(SearchCriterion.REMOTE_SCOPES)
        if self.vacancy_geographies:
            criteria.add(SearchCriterion.VACANCY_GEOGRAPHIES)
        return frozenset(criteria)


def _clean_string_tuple(
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


def _dedupe_tuple[T](values: Iterable[T], field_name: str) -> tuple[T, ...]:
    result: list[T] = []
    for value in values:
        if value in result:
            continue
        result.append(value)
    if any(value is None for value in result):
        raise ValueError(f"{field_name} must not contain None")
    return tuple(result)


def _normalize_work_formats(values: Iterable[WorkFormat | str]) -> tuple[WorkFormat, ...]:
    result: list[WorkFormat] = []
    for raw in values:
        value = raw if isinstance(raw, WorkFormat) else WorkFormat(str(raw).strip())
        if value not in result:
            result.append(value)
    return tuple(result)


def _normalize_scopes(values: Iterable[str], field_name: str, *, allow_city: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in _clean_string_tuple(values, field_name, allow_empty=True):
        scope = _normalize_scope(raw, field_name=field_name, allow_city=allow_city)
        if scope.casefold() in seen:
            continue
        seen.add(scope.casefold())
        normalized.append(scope)
    return tuple(normalized)


def _normalize_scope(value: str, *, field_name: str, allow_city: bool) -> str:
    if value == "unknown":
        return value
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
    if allow_city:
        expected = "unknown, country:<code>, region:<code>, or city:<name>"
    else:
        expected = "global, unknown, country:<code>, or region:<code>"
    raise ValueError(f"{field_name} must use {expected}")
