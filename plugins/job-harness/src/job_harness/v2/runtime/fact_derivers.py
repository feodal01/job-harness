"""Pure versioned derivation of normalized selection facts."""

from __future__ import annotations

from job_harness.v2.contracts import FactDerivation
from job_harness.v2.geography import is_region_scope, normalize_source_geographies
from job_harness.v2.serialization import JsonObject


def derive_selection_facts(facts: JsonObject) -> tuple[FactDerivation, ...]:
    salary = _object(facts.get("salary"))
    location = _object(facts.get("location"))
    location_text = _text(location.get("text")) if location else None
    payload: JsonObject = {
        "grade": _text(facts.get("native_grade")),
        "salary_min": _integer(salary.get("salary_from")) if salary else None,
        "salary_max": _integer(salary.get("salary_to")) if salary else None,
        "salary_currency": _text(salary.get("currency")) if salary else None,
        "work_formats": [
            "office" if value == "onsite" else value
            for value in _strings(facts.get("work_formats"))
        ],
        "remote_scopes": list(_remote_scopes(facts.get("remote_scopes"))),
        "vacancy_geographies": list(_vacancy_geographies(location_text)),
    }
    return (
        FactDerivation(
            deriver_id="structured-selection-facts",
            deriver_version="1.0",
            output_schema_id="selection-facts.v1",
            payload=payload,
        ),
    )


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
