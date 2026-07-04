"""Single-vacancy filtering policy for v2 processed results."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from job_harness.v2.contracts import SearchRequest, TextExclusion, TextExclusionMode
from job_harness.v2.matching import FuzzyBounds, fuzzy_tokens_match
from job_harness.v2.postprocessing.filter_ast import (
    EMPTY_FILTER,
    FilterExpression,
    FilterFacts,
    evaluate_filter_ast,
    filter_ast_from_parameters,
    filter_ast_from_search_request,
)

DEFAULT_EXCLUSION_FIELDS = ("title", "description", "requirements", "additional_sections", "skills", "raw_text")
QUERY_FUZZY_BOUNDS = FuzzyBounds(token_score=0.78, short_token_score=0.78)


@dataclass(frozen=True)
class VacancyFilterCriteria:
    queries: tuple[str, ...]
    exclude_companies: tuple[str, ...] = ()
    exclude_text: tuple[TextExclusion, ...] = ()
    grades: tuple[str, ...] = ()
    salary_from: int | None = None
    published_since: date | None = None
    relocation: bool | None = None
    work_formats: tuple[str, ...] = ()
    remote_scopes: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    filter_ast: FilterExpression | None = None

    def __post_init__(self) -> None:
        if self.filter_ast is None:
            object.__setattr__(
                self,
                "filter_ast",
                filter_ast_from_parameters(
                    work_formats=self.work_formats,
                    remote_scopes=self.remote_scopes,
                    vacancy_geographies=self.vacancy_geographies,
                ),
            )

    @classmethod
    def from_search_request(cls, request: SearchRequest) -> VacancyFilterCriteria:
        return cls(
            queries=request.query_variants,
            exclude_companies=request.exclude_companies,
            exclude_text=request.exclude_text,
            grades=tuple(grade.value for grade in request.grades),
            salary_from=request.salary_from,
            published_since=request.published_since,
            relocation=request.relocation,
            work_formats=tuple(item.value for item in request.work_formats),
            remote_scopes=request.remote_scopes,
            vacancy_geographies=request.vacancy_geographies,
            filter_ast=filter_ast_from_search_request(request),
        )


@dataclass(frozen=True)
class VacancyFilterFacts:
    title: str
    company: str | None = None
    description: str | None = None
    requirements: str | None = None
    additional_sections: Mapping[str, str] | None = None
    skills: tuple[str, ...] = ()
    raw_text: str | None = None
    native_grade: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: str | None = None
    work_formats: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    remote_scopes: tuple[str, ...] = ("unknown",)
    vacancy_geographies: tuple[str, ...] = ("unknown",)
    relocation: bool | None = None
    city: str | None = None

    def __post_init__(self) -> None:
        if self.vacancy_geographies == ("unknown",):
            object.__setattr__(self, "vacancy_geographies", _vacancy_geographies(self.countries, self.city))


@dataclass(frozen=True)
class VacancyFilterDecision:
    keep: bool
    title_matches: bool
    include_in_filtered_out: bool
    reasons: tuple[str, ...]


def decide_vacancy_filter(
    *,
    criteria: VacancyFilterCriteria,
    vacancy: VacancyFilterFacts,
) -> VacancyFilterDecision:
    """Return the keep/remove decision for one normalized vacancy."""

    reasons: list[str] = []
    title_matches = _title_matches_any_query(criteria.queries, vacancy.title)

    if not title_matches:
        reasons.append("query_mismatch")
    if criteria.exclude_companies and _company_excluded(vacancy.company, criteria.exclude_companies):
        reasons.append("excluded_company")
    if criteria.exclude_text and _text_excluded(vacancy, criteria.exclude_text):
        reasons.append("excluded_text")
    if criteria.grades and vacancy.native_grade and vacancy.native_grade not in criteria.grades:
        reasons.append("grade_mismatch")
    if criteria.salary_from is not None and not _salary_matches(vacancy, criteria.salary_from):
        reasons.append("salary_below_requested_minimum")
    if criteria.published_since is not None and not _published_since(vacancy.posted_at, criteria.published_since):
        reasons.append("published_before_requested_date")

    filter_ast = criteria.filter_ast if criteria.filter_ast is not None else EMPTY_FILTER
    ast_evaluation = evaluate_filter_ast(
        filter_ast,
        FilterFacts(
            work_formats=vacancy.work_formats,
            remote_scopes=vacancy.remote_scopes,
            vacancy_geographies=vacancy.vacancy_geographies,
        ),
    )
    reasons.extend(ast_evaluation.reasons)

    if criteria.relocation is not None and vacancy.relocation is not None and vacancy.relocation != criteria.relocation:
        reasons.append("relocation_mismatch")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return VacancyFilterDecision(
        keep=not unique_reasons,
        title_matches=title_matches,
        include_in_filtered_out=bool(unique_reasons) and title_matches,
        reasons=unique_reasons,
    )


def _title_matches_any_query(queries: tuple[str, ...], title: str) -> bool:
    cleaned = tuple(query.strip() for query in queries if query.strip())
    return not cleaned or any(_query_text_matches(tokens=_query_tokens(query), haystack=title) for query in cleaned)


def _query_text_matches(*, tokens: tuple[str, ...], haystack: str) -> bool:
    if not tokens:
        return True
    return fuzzy_tokens_match(" ".join(tokens), haystack, bounds=QUERY_FUZZY_BOUNDS)


def _query_tokens(query: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in re.findall(r"[\w+#.-]+", query) if token.strip())


def _vacancy_geographies(countries: tuple[str, ...], city: str | None) -> tuple[str, ...]:
    geographies: list[str] = []
    for country in countries:
        scope = f"country:{country}"
        if scope not in geographies:
            geographies.append(scope)
    if city:
        scope = f"city:{city}"
        if scope not in geographies:
            geographies.append(scope)
    return tuple(geographies) or ("unknown",)


def _company_excluded(company: str | None, excluded_companies: tuple[str, ...]) -> bool:
    company_text = (company or "").casefold()
    return bool(company_text) and any(excluded.casefold() in company_text for excluded in excluded_companies)


def _text_excluded(vacancy: VacancyFilterFacts, exclusions: tuple[TextExclusion, ...]) -> bool:
    for exclusion in exclusions:
        fields = tuple(field.value for field in exclusion.fields) or DEFAULT_EXCLUSION_FIELDS
        text = "\n".join(_fact_text(vacancy, field) for field in fields)
        if _pattern_matches(text, exclusion):
            return True
    return False


def _fact_text(vacancy: VacancyFilterFacts, field: str) -> str:
    value = getattr(vacancy, field, None)
    if isinstance(value, tuple):
        return " ".join(item for item in value if item)
    if isinstance(value, Mapping):
        return " ".join(item for item in value.values() if item)
    return value if isinstance(value, str) else ""


def _pattern_matches(text: str, exclusion: TextExclusion) -> bool:
    if exclusion.mode == TextExclusionMode.SUBSTRING:
        haystack = text if exclusion.case_sensitive else text.casefold()
        needle = exclusion.pattern if exclusion.case_sensitive else exclusion.pattern.casefold()
        return needle in haystack
    flags = 0 if exclusion.case_sensitive else re.IGNORECASE
    try:
        return re.search(exclusion.pattern, text, flags=flags) is not None
    except re.error as exc:
        raise ValueError(f"invalid exclude_text regex: {exclusion.pattern}") from exc


def _salary_matches(vacancy: VacancyFilterFacts, salary_from: int) -> bool:
    known_values = tuple(value for value in (vacancy.salary_min, vacancy.salary_max) if value is not None)
    return not known_values or max(known_values) >= salary_from


def _published_since(posted_at: str | None, published_since: date) -> bool:
    if not posted_at:
        return True
    try:
        return date.fromisoformat(posted_at[:10]) >= published_since
    except ValueError:
        return True
