"""Structured filter AST for normalized vacancy rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from job_harness.v2.contracts import SearchRequest
from job_harness.v2.geography import geography_matches_any
from job_harness.v2.matching import fuzzy_any_match

FilterField = Literal["work_format", "remote_scope", "vacancy_geography"]
FilterOperator = Literal["any_of", "none_of", "intersects"]

REMOTE_WORK_FORMAT = "remote"
HYBRID_WORK_FORMAT = "hybrid"
OFFICE_WORK_FORMAT = "office"
UNKNOWN_VALUE = "unknown"


@dataclass(frozen=True)
class FieldFilter:
    field: FilterField
    op: FilterOperator
    values: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class AllFilter:
    filters: tuple[FilterExpression, ...]
    short_circuit_on_first_failure: bool = False


@dataclass(frozen=True)
class AnyFilter:
    filters: tuple[FilterExpression, ...]


@dataclass(frozen=True)
class NotFilter:
    filter: FilterExpression
    reason: str


FilterExpression = FieldFilter | AllFilter | AnyFilter | NotFilter


@dataclass(frozen=True)
class FilterEvaluation:
    keep: bool
    reasons: tuple[str, ...]
    failed_conditions: int = 0
    passed_conditions: int = 0


@dataclass(frozen=True)
class FilterFacts:
    work_formats: tuple[str, ...] = ()
    remote_scopes: tuple[str, ...] = (UNKNOWN_VALUE,)
    vacancy_geographies: tuple[str, ...] = (UNKNOWN_VALUE,)


EMPTY_FILTER = AllFilter(filters=())


def filter_ast_from_search_request(request: SearchRequest) -> FilterExpression:
    return filter_ast_from_parameters(
        work_formats=tuple(item.value for item in request.work_formats),
        remote_scopes=request.remote_scopes,
        vacancy_geographies=request.vacancy_geographies,
    )


def filter_ast_from_parameters(
    *,
    work_formats: tuple[str, ...],
    remote_scopes: tuple[str, ...],
    vacancy_geographies: tuple[str, ...],
) -> FilterExpression:
    filters: list[FilterExpression] = []
    work_filter = _workplace_filter(work_formats=work_formats, remote_scopes=remote_scopes)
    if work_filter != EMPTY_FILTER:
        filters.append(work_filter)
    if vacancy_geographies:
        filters.append(
            FieldFilter(
                field="vacancy_geography",
                op="intersects",
                values=vacancy_geographies,
                reason="vacancy_geography_mismatch",
            )
        )
    return AllFilter(filters=tuple(filters))


def evaluate_filter_ast(expression: FilterExpression, facts: FilterFacts) -> FilterEvaluation:
    if isinstance(expression, FieldFilter):
        keep = _evaluate_field_filter(expression, facts)
        return FilterEvaluation(
            keep=keep,
            reasons=() if keep else (expression.reason,),
            failed_conditions=0 if keep else 1,
            passed_conditions=1 if keep else 0,
        )
    if isinstance(expression, AllFilter):
        reasons: list[str] = []
        failed_conditions = 0
        passed_conditions = 0
        for child in expression.filters:
            evaluation = evaluate_filter_ast(child, facts)
            failed_conditions += evaluation.failed_conditions
            passed_conditions += evaluation.passed_conditions
            if not evaluation.keep:
                reasons.extend(evaluation.reasons)
                if expression.short_circuit_on_first_failure:
                    break
        return FilterEvaluation(
            keep=not reasons,
            reasons=_dedupe(reasons),
            failed_conditions=failed_conditions,
            passed_conditions=passed_conditions,
        )
    if isinstance(expression, AnyFilter):
        if not expression.filters:
            return FilterEvaluation(keep=True, reasons=())
        failed: list[FilterEvaluation] = []
        for child in expression.filters:
            evaluation = evaluate_filter_ast(child, facts)
            if evaluation.keep:
                return evaluation
            failed.append(evaluation)
        return min(failed, key=lambda item: (item.failed_conditions, -item.passed_conditions, len(item.reasons)))
    if isinstance(expression, NotFilter):
        evaluation = evaluate_filter_ast(expression.filter, facts)
        return FilterEvaluation(
            keep=not evaluation.keep,
            reasons=() if not evaluation.keep else (expression.reason,),
            failed_conditions=0 if not evaluation.keep else 1,
            passed_conditions=1 if not evaluation.keep else 0,
        )
    raise TypeError(f"unsupported filter expression: {type(expression).__name__}")


def _workplace_filter(*, work_formats: tuple[str, ...], remote_scopes: tuple[str, ...]) -> FilterExpression:
    if not work_formats:
        return EMPTY_FILTER
    physical_formats = tuple(
        work_format
        for work_format in work_formats
        if work_format in {HYBRID_WORK_FORMAT, OFFICE_WORK_FORMAT, UNKNOWN_VALUE}
    )
    if REMOTE_WORK_FORMAT not in work_formats:
        return FieldFilter(
            field="work_format",
            op="any_of",
            values=physical_formats,
            reason="work_format_mismatch",
        )

    remote_filters: list[FilterExpression] = [
        FieldFilter(
            field="work_format",
            op="any_of",
            values=(REMOTE_WORK_FORMAT,),
            reason="work_format_mismatch",
        )
    ]
    if remote_scopes:
        remote_filters.append(
            FieldFilter(
                field="remote_scope",
                op=_remote_scope_operator(remote_scopes),
                values=remote_scopes,
                reason="remote_scope_mismatch",
            )
        )
    remote_filter = AllFilter(filters=tuple(remote_filters), short_circuit_on_first_failure=True)
    if not physical_formats:
        return remote_filter
    return AnyFilter(
        filters=(
            remote_filter,
            FieldFilter(
                field="work_format",
                op="any_of",
                values=physical_formats,
                reason="work_format_mismatch",
            ),
        )
    )


def _evaluate_field_filter(condition: FieldFilter, facts: FilterFacts) -> bool:
    values = _field_values(condition.field, facts)
    if condition.op == "any_of":
        return bool(set(values) & set(condition.values))
    if condition.op == "none_of":
        return not (set(values) & set(condition.values))
    if condition.op == "intersects":
        return _values_intersect_geographies(values, condition.values)
    raise ValueError(f"unsupported filter operator: {condition.op}")


def _remote_scope_operator(remote_scopes: tuple[str, ...]) -> FilterOperator:
    exact_only_scopes = {"global", UNKNOWN_VALUE}
    return "any_of" if all(scope in exact_only_scopes for scope in remote_scopes) else "intersects"


def _field_values(field: FilterField, facts: FilterFacts) -> tuple[str, ...]:
    if field == "work_format":
        return facts.work_formats or (UNKNOWN_VALUE,)
    if field == "remote_scope":
        return facts.remote_scopes or (UNKNOWN_VALUE,)
    if field == "vacancy_geography":
        return facts.vacancy_geographies or (UNKNOWN_VALUE,)
    raise ValueError(f"unsupported filter field: {field}")


def _values_intersect_geographies(values: tuple[str, ...], requested_geographies: tuple[str, ...]) -> bool:
    if not requested_geographies:
        return False
    requested_exact = {value.casefold() for value in requested_geographies}
    requested_country_region = tuple(
        normalized
        for value in requested_geographies
        if (normalized := _country_or_region_geography(value)) is not None
    )
    for value in values:
        if value.casefold() in requested_exact:
            return True
        if value == UNKNOWN_VALUE:
            continue
        if value == "global" and requested_country_region:
            return True
        if value.startswith("city:") and _city_geography_matches(value, requested_exact):
            return True
        geography = _country_or_region_geography(value)
        if geography is not None and geography_matches_any(geography, requested_country_region):
            return True
    return False


def _country_or_region_geography(value: str) -> str | None:
    if value.startswith("country:"):
        return value.removeprefix("country:")
    if value.startswith("region:"):
        return value.removeprefix("region:")
    if value.startswith("city:") or value in {UNKNOWN_VALUE, "global"}:
        return None
    return value


def _city_geography_matches(value: str, requested_exact: set[str]) -> bool:
    city = value.removeprefix("city:")
    requested_cities = tuple(
        item.removeprefix("city:")
        for item in requested_exact
        if item.startswith("city:")
    )
    return (
        value.casefold() in requested_exact
        or city.casefold() in requested_exact
        or fuzzy_any_match(requested_cities, city)
    )


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
