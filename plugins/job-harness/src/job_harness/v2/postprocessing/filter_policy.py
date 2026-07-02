"""Single-vacancy filtering policy for v2 processed results."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from job_harness.v2.contracts import RemoteMode, SearchRequest, TextExclusion, TextExclusionMode
from job_harness.v2.matching import FuzzyBounds, fuzzy_any_match, fuzzy_tokens_match
from job_harness.v2.postprocessing.remote_scope import remote_filter_reasons, vacancy_geography_reasons
from job_harness.v2.postprocessing.work_format import work_format_policy_outcome

DEFAULT_EXCLUSION_FIELDS = ("title", "description", "requirements", "additional_sections", "skills", "raw_text")
QUERY_FUZZY_BOUNDS = FuzzyBounds(token_score=0.78, short_token_score=0.78)
CITY_FUZZY_BOUNDS = FuzzyBounds(token_score=0.78, short_token_score=0.9)


@dataclass(frozen=True)
class VacancyFilterCriteria:
    query: str
    exclude_companies: tuple[str, ...] = ()
    exclude_text: tuple[TextExclusion, ...] = ()
    grades: tuple[str, ...] = ()
    salary_from: int | None = None
    published_since: date | None = None
    relocation: bool | None = None
    remote_mode: RemoteMode | None = None
    hybrid_ok: bool = False
    office_ok: bool = False
    work_from_geographies: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()

    @classmethod
    def from_search_request(cls, request: SearchRequest, *, query: str) -> VacancyFilterCriteria:
        return cls(
            query=query,
            exclude_companies=request.exclude_companies,
            exclude_text=request.exclude_text,
            grades=tuple(grade.value for grade in request.grades),
            salary_from=request.salary_from,
            published_since=request.published_since,
            relocation=request.relocation,
            remote_mode=request.remote_mode,
            hybrid_ok=request.hybrid_ok,
            office_ok=request.office_ok,
            work_from_geographies=request.work_from_geographies,
            vacancy_geographies=request.vacancy_geographies,
            cities=request.cities,
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
    relocation: bool | None = None
    city: str | None = None


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
    title_matches = _title_matches_query(criteria.query, vacancy.title)

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

    work_format_outcome = work_format_policy_outcome(
        remote_mode=criteria.remote_mode,
        hybrid_ok=criteria.hybrid_ok,
        office_ok=criteria.office_ok,
        work_from_geographies=criteria.work_from_geographies,
        vacancy_geographies=criteria.vacancy_geographies,
        work_formats=vacancy.work_formats,
        countries=vacancy.countries,
    )
    reasons.extend(work_format_outcome.reasons)

    if not work_format_outcome.handles_remote_filter:
        reasons.extend(
            remote_filter_reasons(
                remote_mode=criteria.remote_mode,
                remote_scopes=vacancy.remote_scopes,
                work_from_geographies=criteria.work_from_geographies,
            )
        )

    if criteria.relocation is not None and vacancy.relocation is not None and vacancy.relocation != criteria.relocation:
        reasons.append("relocation_mismatch")

    reasons.extend(
        vacancy_geography_reasons(
            vacancy.countries,
            criteria.vacancy_geographies,
            remote_mode=criteria.remote_mode,
            remote_scopes=vacancy.remote_scopes,
        )
    )

    if criteria.cities and vacancy.city and not fuzzy_any_match(
        criteria.cities,
        vacancy.city,
        bounds=CITY_FUZZY_BOUNDS,
    ):
        reasons.append("city_mismatch")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return VacancyFilterDecision(
        keep=not unique_reasons,
        title_matches=title_matches,
        include_in_filtered_out=bool(unique_reasons) and title_matches,
        reasons=unique_reasons,
    )


def _title_matches_query(query: str, title: str) -> bool:
    query = query.strip()
    return not query or _query_text_matches(tokens=_query_tokens(query), haystack=title)


def _query_text_matches(*, tokens: tuple[str, ...], haystack: str) -> bool:
    if not tokens:
        return True
    return fuzzy_tokens_match(" ".join(tokens), haystack, bounds=QUERY_FUZZY_BOUNDS)


def _query_tokens(query: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in re.findall(r"[\w+#.-]+", query) if token.strip())


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
