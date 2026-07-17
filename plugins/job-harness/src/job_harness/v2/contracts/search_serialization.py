"""Strict deserialization for persisted search intents."""

from __future__ import annotations

from datetime import date

from job_harness.v2.contracts.enums import (
    CompensationPeriod,
    Grade,
    SourceType,
    TextExclusionMode,
    TextField,
    WorkFormat,
)
from job_harness.v2.contracts.json_types import JsonObject
from job_harness.v2.contracts.search import (
    CompensationCriterion,
    SearchRequest,
    SearchScenario,
    TextExclusion,
)


def search_request_from_json(payload: JsonObject) -> SearchRequest:
    compensation_payload = _optional_object(payload, "compensation")
    compensation = None
    if compensation_payload is not None:
        compensation = CompensationCriterion(
            minimum=_integer(compensation_payload, "minimum"),
            currency=_text(compensation_payload, "currency"),
            period=CompensationPeriod(_text(compensation_payload, "period")),
            gross=_optional_boolean(compensation_payload, "gross"),
        )
    published_since_value = payload.get("published_since")
    if published_since_value is not None and not isinstance(published_since_value, str):
        raise ValueError("published_since must be an ISO date string or null")
    exclusions = tuple(
        TextExclusion(
            pattern=_text(item, "pattern"),
            mode=TextExclusionMode(_text(item, "mode")),
            case_sensitive=_boolean(item, "case_sensitive"),
            fields=tuple(TextField(value) for value in _strings(item, "fields")),
        )
        for item in _objects(payload, "exclude_text")
    )
    scenarios = tuple(
        SearchScenario(
            relocation=_optional_boolean(item, "relocation"),
            work_formats=tuple(
                WorkFormat(value) for value in _strings(item, "work_formats")
            ),
            remote_scopes=_strings(item, "remote_scopes"),
            vacancy_geographies=_strings(item, "vacancy_geographies"),
            employer_geographies=_strings(item, "employer_geographies"),
        )
        for item in _objects(payload, "scenarios")
    )
    append_to_run_id = payload.get("append_to_run_id")
    if append_to_run_id is not None and not isinstance(append_to_run_id, str):
        raise ValueError("append_to_run_id must be a string or null")
    return SearchRequest(
        query_variants=_strings(payload, "query_variants"),
        grades=tuple(Grade(value) for value in _strings(payload, "grades")),
        compensation=compensation,
        published_since=(
            None if published_since_value is None else date.fromisoformat(published_since_value)
        ),
        exclude_companies=_strings(payload, "exclude_companies"),
        exclude_text=exclusions,
        relocation=_optional_boolean(payload, "relocation"),
        work_formats=tuple(
            WorkFormat(value) for value in _strings(payload, "work_formats")
        ),
        remote_scopes=_strings(payload, "remote_scopes"),
        vacancy_geographies=_strings(payload, "vacancy_geographies"),
        employer_geographies=_strings(payload, "employer_geographies"),
        scenarios=scenarios,
        sources=_strings(payload, "sources"),
        source_types=tuple(
            SourceType(value) for value in _strings(payload, "source_types")
        ),
        append_to_run_id=append_to_run_id,
    )


def _strings(payload: JsonObject, key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a string list")
    return tuple(value)


def _objects(payload: JsonObject, key: str) -> tuple[JsonObject, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be an object list")
    return tuple(value)


def _optional_object(payload: JsonObject, key: str) -> JsonObject | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object or null")
    return value


def _text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _integer(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _boolean(payload: JsonObject, key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_boolean(payload: JsonObject, key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean or null")
    return value
