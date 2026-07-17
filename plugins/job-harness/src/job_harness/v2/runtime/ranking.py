"""Deterministic relevance ranking for final graph vacancies."""

from __future__ import annotations

from job_harness.v2.contracts import SearchRequest
from job_harness.v2.matching import RoleMatcher
from job_harness.v2.serialization import JsonObject


class GraphVacancyRanker:
    def __init__(self, request: SearchRequest) -> None:
        self._matcher = RoleMatcher(request.query_variants)
        self._grades = frozenset(grade.value for grade in request.grades)

    def score(self, facts: JsonObject) -> float:
        match = self._matcher.match(_text(facts.get("title")))
        if not match.matched:
            return 0.0
        relevance = match.strength * 75.0
        resolved_grades = frozenset(_canonical_grade_values(facts))
        if self._grades and self._grades & resolved_grades:
            relevance += 20.0
        if _text(facts.get("description")) or _text(facts.get("summary")):
            relevance += 2.0
        return round(min(relevance, 100.0), 2)


def _canonical_grade_values(facts: JsonObject) -> tuple[str, ...]:
    derived = facts.get("derived_facts")
    if not isinstance(derived, dict):
        return ()
    selection = derived.get("structured-selection-facts")
    if not isinstance(selection, dict):
        return ()
    grade = selection.get("grade")
    if not isinstance(grade, dict):
        return ()
    resolved = grade.get("resolved")
    if not isinstance(resolved, list | tuple):
        return ()
    return tuple(value for value in resolved if isinstance(value, str) and value)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
