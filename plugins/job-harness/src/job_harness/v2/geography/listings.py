"""Listing-level geography evidence extraction."""

from __future__ import annotations

from job_harness.v2.geography.countries import has_specific_location_hint, normalize_source_geographies


def listing_country_codes(listing: dict[str, object]) -> tuple[str, ...]:
    countries: list[str] = []
    for candidate in _listing_country_candidates(listing):
        for country_code in normalize_source_geographies(candidate):
            if country_code not in countries:
                countries.append(country_code)
    return tuple(countries)


def _listing_country_candidates(listing: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("location_text", "country"):
        text = _optional_text(listing.get(key))
        if text:
            values.append(text)
    if not _optional_text(listing.get("country")):
        text = _optional_text(listing.get("city"))
        if text:
            values.append(text)
    raw = listing.get("raw")
    if isinstance(raw, dict):
        has_listing_geography = any(normalize_source_geographies(value) for value in values)
        has_specific_listing_location = any(has_specific_location_hint(value) for value in values)
        if has_specific_listing_location:
            _append_geography_candidate(values, raw.get("lever_country"))
        if not has_listing_geography:
            for key in ("country", "country_text", "eligible_locations", "location", "locations", "offices"):
                _append_geography_candidate(values, raw.get(key))
        for key in (
            "region",
            "regions",
            "remote_locations",
            "remote_restrictions",
            "remote_type",
        ):
            _append_geography_candidate(values, raw.get(key))
    return tuple(values)


def _append_geography_candidate(values: list[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        values.append(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            _append_geography_candidate(values, item)
    if isinstance(value, dict):
        for key in ("code", "name", "name_en", "country", "country_text"):
            _append_geography_candidate(values, value.get(key))


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
