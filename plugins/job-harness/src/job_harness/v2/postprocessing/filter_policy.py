"""Single-vacancy filtering policy for v2 processed results."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from job_harness.v2.contracts import (
    CompensationCriterion,
    CompensationFact,
    CriterionEvaluation,
    CriterionState,
    SearchRequest,
    SelectionOutcome,
    TextExclusion,
    TextExclusionMode,
)
from job_harness.v2.matching import RoleMatcher
from job_harness.v2.postprocessing.filter_ast import (
    EMPTY_FILTER,
    FilterEvaluation,
    FilterExpression,
    FilterFacts,
    evaluate_filter_ast,
    filter_ast_from_parameters,
    filter_ast_from_search_request,
)

DEFAULT_EXCLUSION_FIELDS = ("title", "description", "requirements", "additional_sections", "skills", "raw_text")


@dataclass(frozen=True)
class VacancyFilterCriteria:
    queries: tuple[str, ...]
    exclude_companies: tuple[str, ...] = ()
    exclude_text: tuple[TextExclusion, ...] = ()
    grades: tuple[str, ...] = ()
    compensation: CompensationCriterion | None = None
    published_since: date | None = None
    relocation: bool | None = None
    work_formats: tuple[str, ...] = ()
    remote_scopes: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    employer_geographies: tuple[str, ...] = ()
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
                    employer_geographies=self.employer_geographies,
                    relocation=self.relocation,
                ),
            )

    @classmethod
    def from_search_request(cls, request: SearchRequest) -> VacancyFilterCriteria:
        return cls(
            queries=request.query_variants,
            exclude_companies=request.exclude_companies,
            exclude_text=request.exclude_text,
            grades=tuple(grade.value for grade in request.grades),
            compensation=request.compensation,
            published_since=request.published_since,
            relocation=request.relocation,
            work_formats=tuple(item.value for item in request.work_formats),
            remote_scopes=request.remote_scopes,
            vacancy_geographies=request.vacancy_geographies,
            employer_geographies=request.employer_geographies,
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
    grades: tuple[str, ...] = ()
    compensation: CompensationFact | None = None
    posted_at: str | None = None
    work_formats: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    remote_scopes: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    employer_geographies: tuple[str, ...] = ()
    relocation: bool | None = None
    city: str | None = None

    def __post_init__(self) -> None:
        if not self.vacancy_geographies:
            object.__setattr__(self, "vacancy_geographies", _vacancy_geographies(self.countries, self.city))


@dataclass(frozen=True)
class VacancyFilterDecision:
    outcome: SelectionOutcome
    title_matches: bool
    include_in_filtered_out: bool
    reasons: tuple[str, ...]
    criteria: tuple[CriterionEvaluation, ...]

    @property
    def keep(self) -> bool:
        return self.outcome == SelectionOutcome.KEEP

    @property
    def can_enrich(self) -> bool:
        return self.outcome != SelectionOutcome.REJECT


def decide_vacancy_filter(
    *,
    criteria: VacancyFilterCriteria,
    vacancy: VacancyFilterFacts,
) -> VacancyFilterDecision:
    """Return the keep/remove decision for one normalized vacancy."""

    role_match = RoleMatcher(criteria.queries).match(vacancy.title)
    title_matches = not criteria.queries or role_match.matched
    evaluations: tuple[CriterionEvaluation, ...] = (
        CriterionEvaluation(
            "query",
            CriterionState.MATCH if title_matches else CriterionState.MISMATCH,
            None if title_matches else "query_mismatch",
        ),
        _exclusion_evaluation(
            criterion="exclude_companies",
            excluded=bool(
                criteria.exclude_companies
                and _company_excluded(vacancy.company, criteria.exclude_companies)
            ),
            reason="excluded_company",
        ),
        _exclusion_evaluation(
            criterion="exclude_text",
            excluded=bool(
                criteria.exclude_text
                and _text_excluded(vacancy, criteria.exclude_text)
            ),
            reason="excluded_text",
        ),
        _grade_evaluation(criteria.grades, vacancy.grades),
        _compensation_evaluation(criteria.compensation, vacancy.compensation),
        _published_since_evaluation(criteria.published_since, vacancy.posted_at),
    )

    filter_ast = criteria.filter_ast if criteria.filter_ast is not None else EMPTY_FILTER
    ast_evaluation = evaluate_filter_ast(
        filter_ast,
        FilterFacts(
            work_formats=vacancy.work_formats,
            remote_scopes=vacancy.remote_scopes,
            vacancy_geographies=vacancy.vacancy_geographies,
            employer_geographies=vacancy.employer_geographies,
            relocation=vacancy.relocation,
        ),
    )
    evaluations += _ast_criterion_evaluations(ast_evaluation)
    outcome = _selection_outcome(evaluations)
    reasons = tuple(
        dict.fromkeys(
            evaluation.reason
            for evaluation in evaluations
            if evaluation.reason is not None
        )
    )
    return VacancyFilterDecision(
        outcome=outcome,
        title_matches=title_matches,
        include_in_filtered_out=(
            title_matches and outcome != SelectionOutcome.KEEP
        ),
        reasons=reasons,
        criteria=evaluations,
    )


def _grade_evaluation(
    requested: tuple[str, ...],
    actual: tuple[str, ...],
) -> CriterionEvaluation:
    if not requested:
        return CriterionEvaluation("grades", CriterionState.MATCH)
    if not actual:
        return CriterionEvaluation(
            "grades",
            CriterionState.UNKNOWN,
            "insufficient_evidence:grades",
        )
    if set(requested) & set(actual):
        return CriterionEvaluation("grades", CriterionState.MATCH)
    return CriterionEvaluation("grades", CriterionState.MISMATCH, "grade_mismatch")


def _compensation_evaluation(
    requested: CompensationCriterion | None,
    actual: CompensationFact | None,
) -> CriterionEvaluation:
    if requested is None:
        return CriterionEvaluation("compensation", CriterionState.MATCH)
    if (
        actual is None
        or actual.minimum is None
        or actual.currency is None
        or actual.period is None
        or actual.currency != requested.currency
        or actual.period != requested.period
        or (requested.gross is not None and actual.gross is None)
    ):
        return CriterionEvaluation(
            "compensation",
            CriterionState.UNKNOWN,
            "insufficient_evidence:compensation",
        )
    if requested.gross is not None and actual.gross != requested.gross:
        return CriterionEvaluation(
            "compensation",
            CriterionState.MISMATCH,
            "compensation_gross_mismatch",
        )
    if actual.minimum >= requested.minimum:
        return CriterionEvaluation("compensation", CriterionState.MATCH)
    return CriterionEvaluation(
        "compensation",
        CriterionState.MISMATCH,
        "compensation_below_requested_minimum",
    )


def _published_since_evaluation(
    requested: date | None,
    posted_at: str | None,
) -> CriterionEvaluation:
    if requested is None:
        return CriterionEvaluation("published_since", CriterionState.MATCH)
    if not posted_at:
        return CriterionEvaluation(
            "published_since",
            CriterionState.UNKNOWN,
            "insufficient_evidence:published_since",
        )
    try:
        actual = date.fromisoformat(posted_at[:10])
    except ValueError:
        return CriterionEvaluation(
            "published_since",
            CriterionState.UNKNOWN,
            "insufficient_evidence:published_since",
        )
    if actual >= requested:
        return CriterionEvaluation("published_since", CriterionState.MATCH)
    return CriterionEvaluation(
        "published_since",
        CriterionState.MISMATCH,
        "published_before_requested_date",
    )


def _exclusion_evaluation(
    *,
    criterion: str,
    excluded: bool,
    reason: str,
) -> CriterionEvaluation:
    return CriterionEvaluation(
        criterion,
        CriterionState.MISMATCH if excluded else CriterionState.MATCH,
        reason if excluded else None,
    )


def _ast_criterion_evaluations(
    evaluation: FilterEvaluation,
) -> tuple[CriterionEvaluation, ...]:
    if evaluation.state == CriterionState.MATCH:
        return ()
    return tuple(
        CriterionEvaluation(
            _criterion_for_reason(reason),
            evaluation.state,
            reason,
        )
        for reason in evaluation.reasons
    )


def _criterion_for_reason(reason: str) -> str:
    if reason.startswith("insufficient_evidence:"):
        return reason.removeprefix("insufficient_evidence:")
    return {
        "work_format_mismatch": "work_formats",
        "remote_scope_mismatch": "remote_scopes",
        "vacancy_geography_mismatch": "vacancy_geographies",
        "employer_geography_mismatch": "employer_geographies",
        "relocation_mismatch": "relocation",
    }[reason]


def _selection_outcome(
    criteria: tuple[CriterionEvaluation, ...],
) -> SelectionOutcome:
    if any(item.state == CriterionState.MISMATCH for item in criteria):
        return SelectionOutcome.REJECT
    if any(item.state == CriterionState.UNKNOWN for item in criteria):
        return SelectionOutcome.NEEDS_EVIDENCE
    return SelectionOutcome.KEEP


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
    return tuple(geographies)


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
