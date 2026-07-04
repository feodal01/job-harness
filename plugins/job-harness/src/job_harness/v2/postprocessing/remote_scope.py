"""Remote and geography policy helpers for processed result rows."""

from __future__ import annotations

from job_harness.v2.geography import (
    geography_text_keys,
    is_region_scope,
    listing_country_codes,
    normalize_source_geographies,
)
from job_harness.v2.postprocessing.work_format import REMOTE_FORMAT, listing_work_formats

_GLOBAL_REMOTE_MARKERS = frozenset({"anywhere", "global", "worldwide"})
_REMOTE_MARKERS = frozenset({"remote", "удаленно", "удалённо"})

def listing_countries(listing: dict[str, object]) -> tuple[str, ...]:
    return listing_country_codes(listing)


def country_text(countries: tuple[str, ...]) -> str | None:
    return ", ".join(countries) if countries else None


def listing_remote_scopes(listing: dict[str, object]) -> tuple[str, ...]:
    remote_global = _optional_bool(listing.get("remote_global"))
    remote_in_country = _optional_bool(listing.get("remote_in_country"))
    work_formats = listing_work_formats(listing)
    if REMOTE_FORMAT not in work_formats:
        return ("unknown",)

    if remote_global is True:
        return ("global",)

    if remote_in_country is True:
        return _limited_remote_scopes(listing) or ("unknown",)

    if _raw_mentions_global_remote(listing):
        return ("global",)
    if _raw_mentions_remote(listing) or REMOTE_FORMAT in work_formats:
        return _limited_remote_scopes(listing) or ("unknown",)
    return ("unknown",)


def remote_scope_text(scopes: tuple[str, ...]) -> str:
    return ", ".join(scopes) if scopes else "unknown"


def _limited_remote_scopes(listing: dict[str, object]) -> tuple[str, ...]:
    scopes: list[str] = []
    for candidates in _remote_scope_candidate_groups(listing):
        for candidate in candidates:
            for scope in _scopes_from_geography(candidate):
                if scope not in scopes:
                    scopes.append(scope)
        if scopes:
            return tuple(scopes)
    return tuple(scopes)


def _remote_scope_candidate_groups(listing: dict[str, object]) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    raw = listing.get("raw")
    if isinstance(raw, dict):
        explicit_values: list[str] = []
        for key in ("remote_restrictions", "remote_locations", "eligible_locations"):
            _append_geography_candidate(explicit_values, raw.get(key))
        if explicit_values:
            groups.append(tuple(explicit_values))
        raw_scope_values: list[str] = []
        for key in ("regions", "remote_type"):
            _append_geography_candidate(raw_scope_values, raw.get(key))
        if raw_scope_values:
            groups.append(tuple(raw_scope_values))
    listing_values: list[str] = []
    for key in ("location_text", "country", "city"):
        text = _optional_text(listing.get(key))
        if text:
            listing_values.append(text)
    if listing_values:
        groups.append(tuple(listing_values))
    return tuple(groups)


def _append_geography_candidate(values: list[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        values.append(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            _append_geography_candidate(values, item)
    if isinstance(value, dict):
        for key in ("code", "name", "name_en", "country", "country_text"):
            _append_geography_candidate(values, value.get(key))


def _scopes_from_geography(value: str) -> tuple[str, ...]:
    scopes: list[str] = []
    for geography in normalize_source_geographies(value):
        if is_region_scope(geography):
            scopes.append(f"region:{geography}")
        else:
            scopes.append(f"country:{geography}")
    return tuple(scopes)


def _raw_mentions_global_remote(listing: dict[str, object]) -> bool:
    return bool(_raw_remote_tokens(listing) & _GLOBAL_REMOTE_MARKERS)


def _raw_mentions_remote(listing: dict[str, object]) -> bool:
    return bool(_raw_remote_tokens(listing) & (_REMOTE_MARKERS | _GLOBAL_REMOTE_MARKERS))


def _raw_remote_tokens(listing: dict[str, object]) -> frozenset[str]:
    raw = listing.get("raw")
    values: list[str] = []
    if isinstance(raw, dict):
        for key in ("remote_type", "work_format", "workFormats", "work_format"):
            _append_raw_text_values(values, raw.get(key))
    _append_raw_text_values(values, listing.get("location_text"))
    tokens: list[str] = []
    for value in values:
        for key in geography_text_keys(value):
            tokens.append(key)
            tokens.extend(part for part in key.split() if part)
    return frozenset(tokens)


def _append_raw_text_values(values: list[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        values.append(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            _append_raw_text_values(values, item)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("expected optional boolean field")
    return value


def _optional_text(value: object) -> str | None:
    return _text(value).strip() or None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
