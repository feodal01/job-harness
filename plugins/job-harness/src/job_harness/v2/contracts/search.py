"""Validated public search request contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from job_harness.v2.contracts.enums import (
    CompensationPeriod,
    Grade,
    SearchCriterion,
    SourceType,
    TextExclusionMode,
    TextField,
    WorkFormat,
)
from job_harness.v2.contracts.search_normalization import (
    clean_string_tuple,
    dedupe_tuple,
    normalize_scopes,
    normalize_work_formats,
    validate_location_filters,
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
        object.__setattr__(self, "fields", dedupe_tuple(self.fields, "fields"))


@dataclass(frozen=True)
class CompensationCriterion:
    minimum: int
    currency: str
    period: CompensationPeriod
    gross: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.minimum, bool) or not isinstance(self.minimum, int) or self.minimum < 1:
            raise ValueError("compensation minimum must be >= 1")
        if not isinstance(self.currency, str):
            raise ValueError("compensation currency must be an ISO 4217 alpha-3 code")
        currency = self.currency.strip().upper()
        if currency == "RUR":
            currency = "RUB"
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("compensation currency must be an ISO 4217 alpha-3 code")
        period = self.period
        if not isinstance(period, CompensationPeriod):
            try:
                period = CompensationPeriod(period)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid compensation period") from exc
        if self.gross is not None and not isinstance(self.gross, bool):
            raise ValueError("compensation gross must be boolean when provided")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "period", period)


@dataclass(frozen=True)
class SearchScenario:
    relocation: bool | None = None
    work_formats: tuple[WorkFormat, ...] = ()
    remote_scopes: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    employer_geographies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_formats", normalize_work_formats(self.work_formats))
        object.__setattr__(
            self,
            "remote_scopes",
            normalize_scopes(self.remote_scopes, "remote_scopes", allow_city=False),
        )
        object.__setattr__(
            self,
            "vacancy_geographies",
            normalize_scopes(self.vacancy_geographies, "vacancy_geographies", allow_city=True),
        )
        object.__setattr__(
            self,
            "employer_geographies",
            normalize_scopes(self.employer_geographies, "employer_geographies", allow_city=True),
        )
        validate_location_filters(
            work_formats=self.work_formats,
            remote_scopes=self.remote_scopes,
            vacancy_geographies=self.vacancy_geographies,
            employer_geographies=self.employer_geographies,
        )
        if not any(
            (
                self.relocation is not None,
                self.work_formats,
                self.remote_scopes,
                self.vacancy_geographies,
                self.employer_geographies,
            )
        ):
            raise ValueError("scenario must contain at least one location or work filter")

    @property
    def requested_criteria(self) -> frozenset[SearchCriterion]:
        candidates = (
            (self.relocation is not None, SearchCriterion.RELOCATION),
            (bool(self.work_formats), SearchCriterion.WORK_FORMATS),
            (bool(self.remote_scopes), SearchCriterion.REMOTE_SCOPES),
            (bool(self.vacancy_geographies), SearchCriterion.VACANCY_GEOGRAPHIES),
            (bool(self.employer_geographies), SearchCriterion.EMPLOYER_GEOGRAPHIES),
        )
        return frozenset(criterion for requested, criterion in candidates if requested)


@dataclass(frozen=True)
class SearchRequest:
    query_variants: tuple[str, ...]
    grades: tuple[Grade, ...] = ()
    compensation: CompensationCriterion | None = None
    published_since: date | None = None
    exclude_companies: tuple[str, ...] = ()
    exclude_text: tuple[TextExclusion, ...] = ()
    relocation: bool | None = None
    work_formats: tuple[WorkFormat, ...] = ()
    remote_scopes: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    employer_geographies: tuple[str, ...] = ()
    scenarios: tuple[SearchScenario, ...] = ()
    sources: tuple[str, ...] = ()
    source_types: tuple[SourceType, ...] = ()
    append_to_run_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "query_variants",
            clean_string_tuple(self.query_variants, "query_variants", allow_empty=False),
        )
        object.__setattr__(self, "grades", dedupe_tuple(self.grades, "grades"))
        object.__setattr__(
            self,
            "exclude_companies",
            clean_string_tuple(
                self.exclude_companies,
                "exclude_companies",
                allow_empty=True,
            ),
        )
        object.__setattr__(self, "exclude_text", dedupe_tuple(self.exclude_text, "exclude_text"))
        object.__setattr__(
            self,
            "work_formats",
            normalize_work_formats(self.work_formats),
        )
        object.__setattr__(
            self,
            "remote_scopes",
            normalize_scopes(self.remote_scopes, "remote_scopes", allow_city=False),
        )
        object.__setattr__(
            self,
            "vacancy_geographies",
            normalize_scopes(self.vacancy_geographies, "vacancy_geographies", allow_city=True),
        )
        object.__setattr__(
            self,
            "employer_geographies",
            normalize_scopes(self.employer_geographies, "employer_geographies", allow_city=True),
        )
        object.__setattr__(self, "scenarios", dedupe_tuple(self.scenarios, "scenarios"))
        object.__setattr__(self, "sources", clean_string_tuple(self.sources, "sources", allow_empty=True))
        object.__setattr__(self, "source_types", dedupe_tuple(self.source_types, "source_types"))

        validate_location_filters(
            work_formats=self.work_formats,
            remote_scopes=self.remote_scopes,
            vacancy_geographies=self.vacancy_geographies,
            employer_geographies=self.employer_geographies,
        )
        if self.scenarios and any(
            (
                self.relocation is not None,
                self.work_formats,
                self.remote_scopes,
                self.vacancy_geographies,
                self.employer_geographies,
            )
        ):
            raise ValueError("scenarios cannot be combined with flat location or work filters")
        if self.append_to_run_id is not None and not self.append_to_run_id.strip():
            raise ValueError("append_to_run_id must be non-empty when provided")

    @property
    def requested_criteria(self) -> frozenset[SearchCriterion]:
        candidates = (
            (bool(self.grades), SearchCriterion.GRADES),
            (self.compensation is not None, SearchCriterion.COMPENSATION),
            (self.published_since is not None, SearchCriterion.PUBLISHED_SINCE),
            (self.relocation is not None, SearchCriterion.RELOCATION),
            (bool(self.work_formats), SearchCriterion.WORK_FORMATS),
            (bool(self.remote_scopes), SearchCriterion.REMOTE_SCOPES),
            (bool(self.vacancy_geographies), SearchCriterion.VACANCY_GEOGRAPHIES),
            (bool(self.employer_geographies), SearchCriterion.EMPLOYER_GEOGRAPHIES),
        )
        criteria = {SearchCriterion.QUERY}
        criteria.update(criterion for requested, criterion in candidates if requested)
        for scenario in self.scenarios:
            criteria.update(scenario.requested_criteria)
        return frozenset(criteria)
