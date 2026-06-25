"""Validated public search request contract."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from job_harness.v2.contracts.enums import (
    Grade,
    RemoteMode,
    SearchCriterion,
    SourceType,
    TextExclusionMode,
    TextField,
)
from job_harness.v2.geography import normalize_request_geography


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
    remote_mode: RemoteMode | None = None
    hybrid_ok: bool = False
    office_ok: bool = False
    work_from_geographies: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
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
        remote_mode = _normalize_remote_mode(self.remote_mode)
        object.__setattr__(self, "remote_mode", remote_mode)
        object.__setattr__(
            self,
            "work_from_geographies",
            _normalize_geographies(self.work_from_geographies, "work_from_geographies"),
        )
        object.__setattr__(
            self,
            "vacancy_geographies",
            _normalize_geographies(self.vacancy_geographies, "vacancy_geographies"),
        )
        object.__setattr__(
            self,
            "cities",
            _clean_string_tuple(self.cities, "cities", allow_empty=True),
        )
        object.__setattr__(self, "sources", _clean_string_tuple(self.sources, "sources", allow_empty=True))
        object.__setattr__(self, "source_types", _dedupe_tuple(self.source_types, "source_types"))

        if self.salary_from is not None and self.salary_from < 1:
            raise ValueError("salary_from must be >= 1 when provided")
        if not isinstance(self.hybrid_ok, bool):
            raise ValueError("hybrid_ok must be a boolean")
        if not isinstance(self.office_ok, bool):
            raise ValueError("office_ok must be a boolean")
        if remote_mode == RemoteMode.GLOBAL_REMOTE_ONLY and (self.hybrid_ok or self.office_ok):
            raise ValueError("hybrid_ok and office_ok cannot be used with global_remote_only")
        if self.remote_mode == RemoteMode.COMPATIBLE_REMOTE and not self.work_from_geographies:
            raise ValueError("work_from_geographies are required for compatible_remote")
        if self.remote_mode != RemoteMode.COMPATIBLE_REMOTE and self.work_from_geographies:
            raise ValueError("work_from_geographies are only valid with compatible_remote")
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
        if self.remote_mode is not None and self.remote_mode != RemoteMode.ANY:
            criteria.add(SearchCriterion.REMOTE_MODE)
        if self.hybrid_ok or self.office_ok:
            criteria.add(SearchCriterion.REMOTE_MODE)
        if self.work_from_geographies:
            criteria.add(SearchCriterion.WORK_FROM_GEOGRAPHIES)
        if self.vacancy_geographies:
            criteria.add(SearchCriterion.VACANCY_GEOGRAPHIES)
        if self.cities:
            criteria.add(SearchCriterion.CITIES)
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


def _normalize_remote_mode(value: RemoteMode | str | None) -> RemoteMode | None:
    if value is None or isinstance(value, RemoteMode):
        return value
    return RemoteMode(str(value))


def _normalize_geographies(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in _clean_string_tuple(values, field_name, allow_empty=True):
        geography = normalize_request_geography(raw)
        if geography.casefold() in seen:
            continue
        seen.add(geography.casefold())
        normalized.append(geography)
    return tuple(normalized)
