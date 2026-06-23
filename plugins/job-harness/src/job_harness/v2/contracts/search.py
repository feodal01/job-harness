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
)


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
    remote_in_country: bool | None = None
    remote_global: bool | None = None
    countries: tuple[str, ...] = ()
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
        object.__setattr__(
            self,
            "countries",
            tuple(
                item.upper()
                for item in _clean_string_tuple(self.countries, "countries", allow_empty=True)
            ),
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
        if self.remote_in_country is not None:
            criteria.add(SearchCriterion.REMOTE_IN_COUNTRY)
        if self.remote_global is not None:
            criteria.add(SearchCriterion.REMOTE_GLOBAL)
        if self.countries:
            criteria.add(SearchCriterion.COUNTRIES)
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
