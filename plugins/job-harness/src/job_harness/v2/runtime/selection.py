"""Selection policy adapter for clean graph fact payloads."""

from __future__ import annotations

from job_harness.v2.contracts import (
    CompensationFact,
    CompensationPeriod,
    SearchRequest,
    SelectionDecision,
    SelectionOutcome,
)
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
        return self._evaluate(facts)

    def evaluate_preliminary(self, facts: JsonObject) -> SelectionDecision:
        return self._evaluate(facts)

    def _evaluate(self, facts: JsonObject) -> SelectionDecision:
        decision = decide_vacancy_filter(
            criteria=self._criteria,
            vacancy=_filter_facts(facts),
        )
        return SelectionDecision(
            outcome=decision.outcome,
            reasons=decision.reasons,
            criteria=decision.criteria,
        )


def keep_all(_facts: JsonObject) -> SelectionDecision:
    return SelectionDecision(outcome=SelectionOutcome.KEEP, reasons=())


def _filter_facts(facts: JsonObject) -> VacancyFilterFacts:
    company = _object(facts.get("company"))
    derived = _selection_facts(facts)
    location = _object(derived.get("location")) or {}
    workplace = _object(derived.get("workplace")) or {}
    grade = _object(derived.get("grade")) or {}
    compensation = _object(derived.get("compensation")) or {}
    relocation = _object(derived.get("relocation")) or {}
    cities = _strings(location.get("cities"))
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
        grades=_strings(grade.get("resolved")),
        compensation=_compensation_fact(compensation),
        posted_at=_text(facts.get("posted_at")),
        work_formats=_strings(workplace.get("formats")),
        countries=_strings(location.get("countries")),
        remote_scopes=_strings(workplace.get("remote_scopes")),
        vacancy_geographies=_canonical_vacancy_geographies(location),
        employer_geographies=_strings(derived.get("employer_geographies")),
        relocation=_boolean(relocation.get("supported")),
        city=cities[0] if cities else None,
    )


def _selection_facts(facts: JsonObject) -> JsonObject:
    derived = _object(facts.get("derived_facts"))
    if derived is None:
        return {}
    return _object(derived.get("structured-selection-facts")) or {}


def _canonical_vacancy_geographies(location: JsonObject) -> tuple[str, ...]:
    values = (
        *(f"country:{value}" for value in _strings(location.get("countries"))),
        *(f"region:{value}" for value in _strings(location.get("regions"))),
        *(f"city:{value}" for value in _strings(location.get("cities"))),
    )
    return tuple(dict.fromkeys(values))


def _compensation_fact(value: JsonObject) -> CompensationFact:
    raw_period = _text(value.get("period"))
    period = CompensationPeriod(raw_period) if raw_period else None
    return CompensationFact(
        minimum=_integer(value.get("minimum")),
        maximum=_integer(value.get("maximum")),
        currency=_text(value.get("currency")),
        period=period,
        gross=_boolean(value.get("gross")),
        evidence=_strings(value.get("evidence")),
    )


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
