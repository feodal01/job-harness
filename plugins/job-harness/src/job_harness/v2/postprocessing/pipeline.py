"""Deterministic post-processing from raw evidence to a result table."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from job_harness.v2.contracts import SearchRequest
from job_harness.v2.postprocessing.application_channels import application_channels
from job_harness.v2.postprocessing.company_contacts import company_contacts
from job_harness.v2.postprocessing.criteria_plan import CriteriaProcessingPlanner
from job_harness.v2.postprocessing.filter_policy import (
    VacancyFilterCriteria,
    VacancyFilterFacts,
    decide_vacancy_filter,
)
from job_harness.v2.postprocessing.remote_scope import (
    country_text,
    listing_countries,
    listing_remote_scopes,
    remote_scope_text,
)
from job_harness.v2.postprocessing.work_format import listing_work_formats
from job_harness.v2.serialization import JsonObject, to_jsonable

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


class ProcessingPhase(StrEnum):
    PRE_ENRICHMENT = "pre_enrichment"
    FINAL = "final"


@dataclass(frozen=True)
class ProcessedResults:
    run_id: str
    append_sequence: int
    phase: ProcessingPhase
    raw_records_read: int
    result_count: int
    payload: JsonObject


class ResultTablePostProcessor:
    """Build the v2 presentation table from the append-only raw corpus."""

    def process(
        self,
        *,
        request: SearchRequest,
        run_id: str,
        append_sequence: int,
        phase: ProcessingPhase,
        raw_records: tuple[JsonObject, ...],
        source_attempts: tuple[JsonObject, ...],
        detail_summary: dict[str, object] | None = None,
        application_channel_summary: dict[str, object] | None = None,
    ) -> ProcessedResults:
        rows = _dedupe_rows(_listing_rows(raw_records))
        source_criteria_plan = CriteriaProcessingPlanner().build_plan(
            source_attempts=source_attempts,
            rows=rows,
        )
        kept_rows: list[dict[str, object]] = []
        filtered_rows: list[dict[str, object]] = []
        removed_counts: dict[str, int] = {}

        for row in rows:
            decision = decide_vacancy_filter(
                criteria=VacancyFilterCriteria.from_search_request(request),
                vacancy=_filter_facts(row),
            )
            if not decision.keep:
                for reason in decision.reasons:
                    removed_counts[reason] = removed_counts.get(reason, 0) + 1
                if decision.include_in_filtered_out:
                    filtered_rows.append({**row, "decision": "filtered_out", "decision_reasons": decision.reasons})
                continue
            kept_rows.append({**row, "decision": "kept", "decision_reasons": ("matches_requested_filters",)})

        payload = to_jsonable(
            {
                "schema_version": 1,
                "record_type": "processed_results",
                "phase": phase,
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
        )
        if detail_summary is not None:
            payload["detail_summary"] = to_jsonable(detail_summary)
        if application_channel_summary is not None:
            payload["application_channel_summary"] = to_jsonable(application_channel_summary)
        if not isinstance(payload, dict):
            raise TypeError("processed results payload must be a JSON object")
        return ProcessedResults(
            run_id=run_id,
            append_sequence=append_sequence,
            phase=phase,
            raw_records_read=len(raw_records),
            result_count=len(kept_rows),
            payload=payload,
        )


def _listing_rows(records: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for record in records:
        listing = record.get("listing")
        if not isinstance(listing, dict):
            raise ValueError("raw listing record is missing listing object")
        countries = listing_countries(listing)
        remote_scopes = listing_remote_scopes(listing, countries=countries)
        work_formats = listing_work_formats(listing)
        row: dict[str, object] = {
            "raw_record_id": _optional_int(record.get("raw_record_id")),
            "source": _text(record.get("source")),
            "query_variant": _text(record.get("query_variant")),
            "append_sequence": _int(record.get("append_sequence")),
            "source_listing_id": _optional_text(listing.get("source_listing_id")),
            "title": _text(listing.get("title")),
            "url": _text(listing.get("url")),
            "company": _optional_text(listing.get("company")),
            "country": country_text(countries),
            "countries": countries,
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
            "remote_scope": remote_scope_text(remote_scopes),
            "remote_scopes": remote_scopes,
            "work_formats": work_formats,
            "relocation": _optional_bool(listing.get("relocation")),
            "native_grade": _optional_text(listing.get("native_grade")),
            "display_experience": _display_experience(listing),
            "display_work_format": _display_work_format(listing),
            "description": _optional_text(listing.get("description")),
            "requirements": _optional_text(listing.get("requirements")),
            "additional_sections": _text_mapping(listing.get("additional_sections")),
            "skills": _text_tuple(listing.get("skills")),
            "application_channels": application_channels(listing),
            "company_contacts": company_contacts(listing),
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


def _filter_facts(row: dict[str, object]) -> VacancyFilterFacts:
    return VacancyFilterFacts(
        title=_text(row["title"]),
        company=_optional_text(row["company"]),
        description=_optional_text(row["description"]),
        requirements=_optional_text(row["requirements"]),
        additional_sections=_text_mapping(row["additional_sections"]),
        skills=_row_text_tuple(row["skills"]),
        raw_text=_optional_text(row["raw_text"]),
        native_grade=_optional_text(row["native_grade"]),
        salary_min=_optional_int(row["salary_min"]),
        salary_max=_optional_int(row["salary_max"]),
        posted_at=_optional_text(row["posted_at"]),
        work_formats=_row_text_tuple(row["work_formats"]),
        countries=_row_text_tuple(row["countries"]),
        remote_scopes=_row_text_tuple(row["remote_scopes"]) or ("unknown",),
        relocation=_optional_bool(row["relocation"]),
        city=_optional_text(row["city"]),
    )


def _row_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(_text(item) for item in value if _text(item))
    if isinstance(value, list):
        return tuple(_text(item) for item in value if _text(item))
    return ()


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
    work_formats = listing_work_formats(listing)
    if work_formats:
        return ", ".join(work_formats)
    if isinstance(raw, dict):
        source_work_format = _optional_text(raw.get("work_format"))
        if source_work_format:
            return source_work_format
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
    return {_text(key): _text(item) for key, item in value.items() if _text(key).strip() and _text(item).strip()}
