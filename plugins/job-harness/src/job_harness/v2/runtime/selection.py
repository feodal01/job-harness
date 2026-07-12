"""Selection policy adapter for clean graph fact payloads."""

from __future__ import annotations

from job_harness.v2.contracts import SearchRequest, SelectionDecision
from job_harness.v2.geography import is_region_scope, normalize_source_geographies
from job_harness.v2.postprocessing.filter_policy import (
    VacancyFilterCriteria,
    VacancyFilterFacts,
    decide_vacancy_filter,
)
from job_harness.v2.serialization import JsonObject


class GraphVacancySelector:
    def __init__(self, request: SearchRequest) -> None:
        self._criteria = VacancyFilterCriteria.from_search_request(request)

    def evaluate(self, facts: JsonObject) -> SelectionDecision:
        decision = decide_vacancy_filter(
            criteria=self._criteria,
            vacancy=_filter_facts(facts),
        )
        return SelectionDecision(keep=decision.keep, reasons=decision.reasons)


def keep_all(_facts: JsonObject) -> SelectionDecision:
    return SelectionDecision(keep=True, reasons=())


def _filter_facts(facts: JsonObject) -> VacancyFilterFacts:
    company = _object(facts.get("company"))
    salary = _object(facts.get("salary"))
    location = _object(facts.get("location"))
    derived = _selection_facts(facts)
    work_formats = _strings(derived.get("work_formats")) or tuple(
        "office" if value == "onsite" else value for value in _strings(facts.get("work_formats"))
    )
    location_text = _text(location.get("text")) if location else None
    return VacancyFilterFacts(
        title=_text(facts.get("title")) or "",
        company=_text(company.get("name")) if company else None,
        description=_text(facts.get("description")) or _text(facts.get("summary")),
        requirements="\n".join(_strings(facts.get("requirements"))) or None,
        skills=_strings(facts.get("skills")),
        raw_text="\n".join(
            value
            for value in (
                _text(facts.get("title")),
                _text(facts.get("summary")),
                _text(facts.get("description")),
            )
            if value
        ),
        native_grade=_text(derived.get("grade")) or _text(facts.get("native_grade")),
        salary_min=_integer(derived.get("salary_min")) or (
            _integer(salary.get("salary_from")) if salary else None
        ),
        salary_max=_integer(derived.get("salary_max")) or (
            _integer(salary.get("salary_to")) if salary else None
        ),
        posted_at=_text(facts.get("posted_at")),
        work_formats=work_formats,
        remote_scopes=_strings(derived.get("remote_scopes")) or _remote_scopes(facts.get("remote_scopes")),
        vacancy_geographies=_strings(derived.get("vacancy_geographies"))
        or _vacancy_geographies(location_text),
        relocation=_boolean(facts.get("relocation")),
        city=location_text,
    )


def _selection_facts(facts: JsonObject) -> JsonObject:
    derived = _object(facts.get("derived_facts"))
    if derived is None:
        return {}
    return _object(derived.get("structured-selection-facts")) or {}


def _remote_scopes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ("unknown",)
    scopes: list[str] = []
    for item in value:
        scope = _object(item)
        if scope is None:
            continue
        kind = _text(scope.get("kind"))
        code = _text(scope.get("code"))
        if kind == "worldwide":
            scopes.append("global")
        elif kind in {"country", "region"} and code:
            scopes.append(f"{kind}:{code}")
    return tuple(dict.fromkeys(scopes)) or ("unknown",)


def _vacancy_geographies(location_text: str | None) -> tuple[str, ...]:
    if not location_text:
        return ("unknown",)
    normalized = normalize_source_geographies(location_text)
    return tuple(
        f"region:{value}" if is_region_scope(value) else f"country:{value}"
        for value in normalized
    ) or ("unknown",)


def _object(value: object) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _boolean(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
