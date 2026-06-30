"""Deserialize stored raw records for application-channel enrichment."""

from __future__ import annotations

from job_harness.v2.contracts import RawListing
from job_harness.v2.serialization import JsonObject


def listing_from_record(record: JsonObject) -> RawListing:
    listing = record.get("listing")
    if not isinstance(listing, dict):
        raise ValueError("raw record is missing listing object")
    return RawListing(
        source_listing_id=_optional_text(listing, "source_listing_id"),
        title=_required_text(listing, "title"),
        url=_required_text(listing, "url"),
        source=_required_text(listing, "source"),
        company=_optional_text(listing, "company"),
        country=_optional_text(listing, "country"),
        city=_optional_text(listing, "city"),
        location_text=_optional_text(listing, "location_text"),
        salary_text=_optional_text(listing, "salary_text"),
        salary_min=_optional_int(listing, "salary_min"),
        salary_max=_optional_int(listing, "salary_max"),
        salary_currency=_optional_text(listing, "salary_currency"),
        posted_at=_optional_text(listing, "posted_at"),
        remote_in_country=_optional_bool(listing, "remote_in_country"),
        remote_global=_optional_bool(listing, "remote_global"),
        relocation=_optional_bool(listing, "relocation"),
        native_grade=_optional_text(listing, "native_grade"),
        description=_optional_text(listing, "description"),
        requirements=_optional_text(listing, "requirements"),
        additional_sections=_text_mapping(listing, "additional_sections"),
        skills=_text_tuple(listing, "skills"),
        raw_text=_optional_text(listing, "raw_text"),
        raw=_object_mapping(listing, "raw"),
    )


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _optional_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _optional_bool(payload: dict[str, object], key: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean or null")
    return value


def _text_mapping(payload: dict[str, object], key: str) -> dict[str, str]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    parsed: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise ValueError(f"{key} must map strings to strings")
        parsed[raw_key] = raw_value
    return parsed


def _text_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _object_mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return dict(value)
