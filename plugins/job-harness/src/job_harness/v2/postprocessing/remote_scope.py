"""Remote and geography policy helpers for processed result rows."""

from __future__ import annotations

from job_harness.v2.contracts import RemoteMode
from job_harness.v2.geography import (
    geography_matches_any,
    geography_text_keys,
    is_region_scope,
    listing_country_codes,
    normalize_source_geographies,
)
from job_harness.v2.postprocessing.work_format import HYBRID_FORMAT, OFFICE_FORMAT, REMOTE_FORMAT, listing_work_formats

_GLOBAL_REMOTE_MARKERS = frozenset({"anywhere", "global", "worldwide"})
_ONSITE_MARKERS = frozenset({"office", "on site", "on-site", "onsite", "офис"})
_REMOTE_MARKERS = frozenset({"remote", "удаленно", "удалённо"})

def listing_countries(listing: dict[str, object]) -> tuple[str, ...]:
    return listing_country_codes(listing)


def country_text(countries: tuple[str, ...]) -> str | None:
    return ", ".join(countries) if countries else None


def listing_remote_scopes(listing: dict[str, object], *, countries: tuple[str, ...] | None = None) -> tuple[str, ...]:
    remote_global = _optional_bool(listing.get("remote_global"))
    remote_in_country = _optional_bool(listing.get("remote_in_country"))
    work_formats = listing_work_formats(listing)
    if REMOTE_FORMAT not in work_formats:
        physical_scopes = tuple(
            scope
            for work_format, scope in ((HYBRID_FORMAT, "hybrid"), (OFFICE_FORMAT, "onsite"))
            if work_format in work_formats
        )
        if physical_scopes:
            return physical_scopes

    if remote_global is True:
        return ("global",)

    if remote_in_country is True:
        return _limited_remote_scopes(listing) or ("unknown",)

    if _raw_mentions_global_remote(listing):
        return ("global",)
    if _raw_mentions_remote(listing) or REMOTE_FORMAT in work_formats:
        return _limited_remote_scopes(listing) or ("unknown",)
    if _raw_mentions_onsite(listing):
        return ("onsite",)
    if HYBRID_FORMAT in work_formats:
        return ("hybrid",)
    if remote_in_country is False and remote_global is False:
        return ("onsite",)
    if (countries if countries is not None else listing_country_codes(listing)) and not _has_remote_scope_hint(listing):
        return ("onsite",)
    return ("unknown",)


def remote_scope_text(scopes: tuple[str, ...]) -> str:
    return ", ".join(scopes) if scopes else "unknown"


def remote_filter_reasons(
    *,
    remote_mode: RemoteMode | None,
    remote_scopes: tuple[str, ...],
    work_from_geographies: tuple[str, ...],
) -> tuple[str, ...]:
    if remote_mode is None or remote_mode == RemoteMode.ANY:
        return ()

    if remote_mode == RemoteMode.COMPATIBLE_REMOTE:
        return _compatible_remote_filter_reasons(
            remote_scopes=remote_scopes,
            work_from_geographies=work_from_geographies,
        )

    if remote_mode == RemoteMode.GLOBAL_REMOTE_ONLY:
        if "global" in remote_scopes:
            return ()
        if remote_scopes == ("unknown",):
            return ()
        return ("remote_global_mismatch",)

    if remote_mode == RemoteMode.NON_REMOTE_ONLY:
        if remote_scopes == ("onsite",):
            return ()
        if remote_scopes == ("unknown",):
            return ()
        return ("remote_mismatch",)

    return ()


def vacancy_geography_reasons(
    countries: tuple[str, ...],
    requested_geographies: tuple[str, ...],
    *,
    remote_mode: RemoteMode | None,
    remote_scopes: tuple[str, ...],
) -> tuple[str, ...]:
    if not requested_geographies:
        return ()
    if remote_mode in {RemoteMode.COMPATIBLE_REMOTE, RemoteMode.GLOBAL_REMOTE_ONLY} and "global" in remote_scopes:
        return ()
    if not countries:
        return ()
    if any(geography_matches_any(country, requested_geographies) for country in countries):
        return ()
    return ("vacancy_geography_mismatch",)


def row_remote_scopes(row: dict[str, object]) -> tuple[str, ...]:
    value = row.get("remote_scopes")
    if isinstance(value, tuple):
        return tuple(_text(item) for item in value if _text(item)) or ("unknown",)
    if isinstance(value, list):
        return tuple(_text(item) for item in value if _text(item)) or ("unknown",)
    return ("unknown",)


def row_countries(row: dict[str, object]) -> tuple[str, ...]:
    value = row.get("countries")
    if isinstance(value, tuple):
        return tuple(_text(item) for item in value if _text(item))
    if isinstance(value, list):
        return tuple(_text(item) for item in value if _text(item))
    return ()


def _remote_scopes_match_work_from(scopes: tuple[str, ...], work_from_geographies: tuple[str, ...]) -> bool:
    if "global" in scopes:
        return True
    return any(_scope_matches_geography(scope, work_from_geographies) for scope in scopes)


def _compatible_remote_filter_reasons(
    *,
    remote_scopes: tuple[str, ...],
    work_from_geographies: tuple[str, ...],
) -> tuple[str, ...]:
    if "global" in remote_scopes:
        return ()
    if _remote_scopes_match_work_from(remote_scopes, work_from_geographies):
        return ()
    return _remote_eligibility_failure(remote_scopes)


def _remote_eligibility_failure(remote_scopes: tuple[str, ...]) -> tuple[str, ...]:
    if remote_scopes == ("unknown",):
        return ()
    return ("remote_eligibility_mismatch",)


def _scope_matches_geography(scope: str, requested_geographies: tuple[str, ...]) -> bool:
    if scope.startswith("country:"):
        return geography_matches_any(scope.removeprefix("country:"), requested_geographies)
    if scope.startswith("region:"):
        return geography_matches_any(scope.removeprefix("region:"), requested_geographies)
    return False


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


def _raw_mentions_onsite(listing: dict[str, object]) -> bool:
    tokens = _raw_remote_tokens(listing)
    return bool(tokens & _ONSITE_MARKERS)


def _raw_mentions_remote(listing: dict[str, object]) -> bool:
    return bool(_raw_remote_tokens(listing) & (_REMOTE_MARKERS | _GLOBAL_REMOTE_MARKERS))


def _has_remote_scope_hint(listing: dict[str, object]) -> bool:
    raw = listing.get("raw")
    values: list[str] = []
    if isinstance(raw, dict):
        for key in ("remote_restrictions", "remote_locations", "eligible_locations", "remote_type"):
            _append_raw_text_values(values, raw.get(key))
    return bool(values)


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
