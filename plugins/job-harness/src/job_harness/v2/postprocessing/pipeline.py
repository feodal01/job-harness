"""Deterministic post-processing from raw evidence to a result table."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from job_harness.v2.contracts import SearchRequest, TextExclusion, TextExclusionMode
from job_harness.v2.postprocessing.criteria_plan import CriteriaProcessingPlanner
from job_harness.v2.runtime.serialization import to_jsonable

_TEXT_FIELDS = ("title", "description", "requirements", "additional_sections", "skills", "raw_text")
_HH_EXPERIENCE_TEXT = {
    "noExperience": "без опыта",
    "between1And3": "1–3 года",
    "between3And6": "3–6 лет",
    "moreThan6": "более 6 лет",
}
_HH_EMPLOYMENT_FORM_TEXT = {
    "FULL": "полная занятость",
    "PART": "частичная занятость",
    "PROJECT": "проектная работа",
    "VOLUNTEER": "волонтерство",
    "PROBATION": "стажировка",
}
_HH_WORK_FORMAT_TEXT = {
    "REMOTE": "удалённо",
    "ON_SITE": "на месте работодателя",
    "HYBRID": "гибрид",
    "FIELD_WORK": "разъездной",
}
_HH_WORK_SCHEDULE_TEXT = {
    "FIVE_ON_TWO_OFF": "5/2",
    "TWO_ON_TWO_OFF": "2/2",
    "SIX_ON_ONE_OFF": "6/1",
    "FLEXIBLE": "гибкий",
    "SHIFT": "сменный",
    "FLY_IN_FLY_OUT": "вахтовый",
}
_HH_WORKING_HOURS_TEXT = {
    "HOURS_2": "2",
    "HOURS_3": "3",
    "HOURS_4": "4",
    "HOURS_5": "5",
    "HOURS_6": "6",
    "HOURS_7": "7",
    "HOURS_8": "8",
    "HOURS_9": "9",
    "HOURS_10": "10",
    "HOURS_11": "11",
    "HOURS_12": "12",
    "HOURS_24": "24",
}
_SHORT_QUERY_TOKEN_LENGTH = 2


@dataclass(frozen=True)
class ProcessedResults:
    run_id: str
    append_sequence: int
    raw_records_read: int
    result_count: int
    output_path: Path


class ResultTablePostProcessor:
    """Build the v2 presentation table from the append-only raw corpus."""

    def process(
        self,
        *,
        request: SearchRequest,
        run_id: str,
        append_sequence: int,
        raw_listings_path: Path,
        source_attempts_path: Path,
        output_path: Path,
    ) -> ProcessedResults:
        raw_records = _read_jsonl_objects(raw_listings_path)
        source_attempts = _read_required_jsonl_objects(source_attempts_path)
        rows = _dedupe_rows(_listing_rows(raw_records))
        source_criteria_plan = CriteriaProcessingPlanner().build_plan(
            source_attempts=source_attempts,
            rows=rows,
        )
        native_query_attempts = _native_query_attempts(source_attempts)
        kept_rows: list[dict[str, object]] = []
        filtered_rows: list[dict[str, object]] = []
        removed_counts: dict[str, int] = {}

        for row in rows:
            reason = _removal_reason(row, request, native_query_attempts)
            if reason is not None:
                removed_counts[reason] = removed_counts.get(reason, 0) + 1
                filtered_rows.append({**row, "decision": "filtered_out", "decision_reasons": (reason,)})
                continue
            kept_rows.append({**row, "decision": "kept", "decision_reasons": ("matches_requested_filters",)})

        payload = {
            "schema_version": 1,
            "record_type": "processed_results",
            "run_id": run_id,
            "append_sequence": append_sequence,
            "search_request": to_jsonable(request),
            "raw_records_read": len(raw_records),
            "result_count": len(kept_rows),
            "removed_counts": removed_counts,
            "source_criteria_plan": source_criteria_plan,
            "results": kept_rows,
            "filtered_out_results": filtered_rows,
        }
        _write_json_atomic(output_path, payload)
        return ProcessedResults(
            run_id=run_id,
            append_sequence=append_sequence,
            raw_records_read=len(raw_records),
            result_count=len(kept_rows),
            output_path=output_path,
        )


def _read_jsonl_objects(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"raw corpus line is not a JSON object: {path}")
        records.append(value)
    return tuple(records)


def _read_required_jsonl_objects(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        raise FileNotFoundError(f"required JSONL artifact does not exist: {path}")
    return _read_jsonl_objects(path)


def _listing_rows(records: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for record in records:
        listing = record.get("listing")
        if not isinstance(listing, dict):
            raise ValueError("raw listing record is missing listing object")
        row = {
            "source": _text(record.get("source")),
            "query_variant": _text(record.get("query_variant")),
            "append_sequence": _int(record.get("append_sequence")),
            "source_listing_id": _optional_text(listing.get("source_listing_id")),
            "title": _text(listing.get("title")),
            "url": _text(listing.get("url")),
            "company": _optional_text(listing.get("company")),
            "country": _optional_text(listing.get("country")),
            "city": _optional_text(listing.get("city")),
            "location_text": _optional_text(listing.get("location_text")),
            "salary_text": _optional_text(listing.get("salary_text")),
            "salary_min": _optional_int(listing.get("salary_min")),
            "salary_max": _optional_int(listing.get("salary_max")),
            "salary_currency": _optional_text(listing.get("salary_currency")),
            "display_salary": _display_salary(listing),
            "posted_at": _optional_text(listing.get("posted_at")),
            "remote_in_country": _optional_bool(listing.get("remote_in_country")),
            "remote_global": _optional_bool(listing.get("remote_global")),
            "relocation": _optional_bool(listing.get("relocation")),
            "native_grade": _optional_text(listing.get("native_grade")),
            "display_experience": _display_experience(listing),
            "display_work_format": _display_work_format(listing),
            "description": _optional_text(listing.get("description")),
            "requirements": _optional_text(listing.get("requirements")),
            "additional_sections": _text_mapping(listing.get("additional_sections")),
            "skills": _text_tuple(listing.get("skills")),
            "source_facts": _source_facts(listing),
            "raw_text": _optional_text(listing.get("raw_text")),
            "description_availability": _optional_text(record.get("description_availability")),
            "detail_fetched": bool(record.get("detail_fetched")),
            "detail_parse_error": _optional_text(record.get("detail_parse_error")),
        }
        rows.append(row)
    return tuple(rows)


def _dedupe_rows(rows: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    seen: set[tuple[str, str]] = set()
    unique_rows: list[dict[str, object]] = []
    for row in rows:
        key = (_text(row["source"]), _text(row["source_listing_id"] or row["url"]))
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)
    return tuple(unique_rows)


def _source_facts(listing: dict[str, object]) -> tuple[dict[str, str], ...]:
    source = _text(listing.get("source"))
    raw = listing.get("raw")
    if source != "hh_ru" or not isinstance(raw, dict):
        return ()

    facts: list[dict[str, str]] = []
    _append_fact(facts, "Employment", _HH_EMPLOYMENT_FORM_TEXT.get(_text(raw.get("employmentForm"))))
    _append_fact(facts, "Contract", _hh_contract_text(raw))
    _append_fact(facts, "Schedule", _mapped_list(raw.get("workScheduleByDays"), _HH_WORK_SCHEDULE_TEXT))
    _append_fact(facts, "Working hours", _mapped_list(raw.get("workingHours"), _HH_WORKING_HOURS_TEXT))
    return tuple(facts)


def _display_salary(listing: dict[str, object]) -> str | None:
    salary_text = _optional_text(listing.get("salary_text"))
    if salary_text:
        return salary_text
    salary_min = _optional_int(listing.get("salary_min"))
    salary_max = _optional_int(listing.get("salary_max"))
    salary_currency = _optional_text(listing.get("salary_currency"))
    if salary_min is not None and salary_max is not None:
        return f"{salary_min} - {salary_max}{f' {salary_currency}' if salary_currency else ''}"
    if salary_min is not None:
        return f"from {salary_min}{f' {salary_currency}' if salary_currency else ''}"
    if salary_max is not None:
        return f"up to {salary_max}{f' {salary_currency}' if salary_currency else ''}"
    raw = listing.get("raw")
    if isinstance(raw, dict):
        compensation = _hh_compensation_text(raw.get("compensation"))
        if compensation:
            return compensation
    return "не указан"


def _display_experience(listing: dict[str, object]) -> str | None:
    raw = listing.get("raw")
    if isinstance(raw, dict):
        experience = _HH_EXPERIENCE_TEXT.get(_text(raw.get("workExperience")))
        if experience:
            return experience
    return _optional_text(listing.get("native_grade"))


def _display_work_format(listing: dict[str, object]) -> str | None:
    raw = listing.get("raw")
    if isinstance(raw, dict):
        work_format = _mapped_list(raw.get("workFormats"), _HH_WORK_FORMAT_TEXT)
        if work_format:
            return work_format
    remote_global = _optional_bool(listing.get("remote_global"))
    remote_in_country = _optional_bool(listing.get("remote_in_country"))
    relocation = _optional_bool(listing.get("relocation"))
    if remote_global is True:
        return "remote global"
    if remote_in_country is True:
        return "remote"
    if remote_in_country is False and remote_global is False:
        return "on-site or hybrid"
    if relocation is True:
        return "relocation"
    return None


def _append_fact(facts: list[dict[str, str]], label: str, value: str | None) -> None:
    if value:
        facts.append({"label": label, "value": value})


def _mapped_list(value: object, mapping: dict[str, str]) -> str | None:
    values = _text_tuple(value)
    mapped = tuple(mapping.get(item, item) for item in values)
    return ", ".join(mapped) or None


def _hh_contract_text(raw: dict[str, object]) -> str | None:
    contracts: list[str] = []
    if raw.get("acceptLaborContract") is True:
        contracts.append("трудовой договор")
    civil_contracts = raw.get("civilLawContracts")
    if isinstance(civil_contracts, list) and civil_contracts:
        contracts.append("ГПХ")
    return ", ".join(contracts) or None


def _hh_compensation_text(value: object) -> str | None:
    if isinstance(value, dict) and "noCompensation" in value:
        return "не указан"
    return None


def _native_query_attempts(source_attempts: tuple[dict[str, object], ...]) -> frozenset[tuple[str, str]]:
    native_attempts: set[tuple[str, str]] = set()
    for attempt in source_attempts:
        criteria = attempt.get("criteria")
        if not isinstance(criteria, dict):
            continue
        native_applied = criteria.get("native_applied")
        if not isinstance(native_applied, list) or "query" not in native_applied:
            continue
        native_attempts.add((_text(attempt.get("source")), _text(attempt.get("query_variant"))))
    return frozenset(native_attempts)


def _removal_reason(
    row: dict[str, object],
    request: SearchRequest,
    native_query_attempts: frozenset[tuple[str, str]],
) -> str | None:
    if _query_postprocess_required(row, native_query_attempts) and not _query_matches(row):
        return "query_mismatch"
    if request.exclude_companies and _company_excluded(row, request.exclude_companies):
        return "excluded_company"
    if request.exclude_text and _text_excluded(row, request.exclude_text):
        return "excluded_text"
    if request.grades and _text(row["native_grade"]) not in {grade.value for grade in request.grades}:
        return "grade_mismatch"
    if request.salary_from is not None and not _salary_matches(row, request.salary_from):
        return "salary_below_requested_minimum"
    if request.published_since is not None and not _published_since(row, request.published_since):
        return "published_before_requested_date"
    if (
        request.remote_in_country is not None
        and _optional_bool(row["remote_in_country"]) != request.remote_in_country
    ):
        return "remote_in_country_mismatch"
    if request.remote_global is not None and _optional_bool(row["remote_global"]) != request.remote_global:
        return "remote_global_mismatch"
    if request.relocation is not None and _optional_bool(row["relocation"]) != request.relocation:
        return "relocation_mismatch"
    if request.countries and _text(row["country"]).upper() not in request.countries:
        return "country_mismatch"
    if request.cities and _text(row["city"]).casefold() not in {city.casefold() for city in request.cities}:
        return "city_mismatch"
    return None


def _query_postprocess_required(
    row: dict[str, object],
    native_query_attempts: frozenset[tuple[str, str]],
) -> bool:
    key = (_text(row["source"]), _text(row["query_variant"]))
    return key not in native_query_attempts


def _query_matches(row: dict[str, object]) -> bool:
    query = _text(row["query_variant"]).strip()
    if not query:
        return True
    tokens = _query_tokens(query)
    fields = (
        ("title", "skills")
        if tokens and all(len(token) <= _SHORT_QUERY_TOKEN_LENGTH for token in tokens)
        else _TEXT_FIELDS
    )
    haystack = "\n".join(
        _field_text(row, field)
        for field in fields
    )
    return _query_text_matches(tokens=tokens, haystack=haystack)


def _query_text_matches(*, tokens: tuple[str, ...], haystack: str) -> bool:
    normalized_haystack = haystack.casefold()
    if not tokens:
        return True
    if len(tokens) == 1:
        return _token_matches(tokens[0], normalized_haystack)
    return all(_token_matches(token, normalized_haystack) for token in tokens)


def _query_tokens(query: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in re.findall(r"[\w+#.-]+", query) if token.strip())


def _token_matches(token: str, haystack: str) -> bool:
    if len(token) <= _SHORT_QUERY_TOKEN_LENGTH:
        pattern = rf"(?<![\w]){re.escape(token)}(?![\w])"
        return re.search(pattern, haystack) is not None
    return token in haystack


def _company_excluded(row: dict[str, object], excluded_companies: tuple[str, ...]) -> bool:
    company = _text(row["company"]).casefold()
    return bool(company) and any(excluded.casefold() in company for excluded in excluded_companies)


def _text_excluded(row: dict[str, object], exclusions: tuple[TextExclusion, ...]) -> bool:
    for exclusion in exclusions:
        fields = tuple(field.value for field in exclusion.fields) or _TEXT_FIELDS
        text = "\n".join(_field_text(row, field) for field in fields)
        if _pattern_matches(text, exclusion):
            return True
    return False


def _field_text(row: dict[str, object], field: str) -> str:
    value = row.get(field)
    if isinstance(value, tuple):
        return " ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    return _text(value)


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


def _salary_matches(row: dict[str, object], salary_from: int) -> bool:
    salary_min = _optional_int(row["salary_min"])
    salary_max = _optional_int(row["salary_max"])
    known_values = tuple(value for value in (salary_min, salary_max) if value is not None)
    return bool(known_values) and max(known_values) >= salary_from


def _published_since(row: dict[str, object], published_since: date) -> bool:
    raw = _text(row["posted_at"])
    if not raw:
        return False
    try:
        return date.fromisoformat(raw[:10]) >= published_since
    except ValueError:
        return False


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        encoded = (json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        os.write(tmp_fd, encoded)
        os.fsync(tmp_fd)
    finally:
        os.close(tmp_fd)
    os.replace(tmp_path, path)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    text = _text(value).strip()
    return text or None


def _int(value: object) -> int:
    if not isinstance(value, int):
        raise ValueError("expected integer field")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError("expected optional integer field")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("expected optional boolean field")
    return value


def _text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("expected skills list")
    return tuple(_text(item) for item in value if _text(item))


def _text_mapping(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("expected additional_sections object")
    return {
        _text(key): _text(item)
        for key, item in value.items()
        if _text(key).strip() and _text(item).strip()
    }
