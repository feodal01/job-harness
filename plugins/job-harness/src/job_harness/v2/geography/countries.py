"""Country and region normalization for v2 search policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from babel import Locale, localedata
from babel.core import get_global

from job_harness.v2.geography.cities import city_country_code_for_keys
from job_harness.v2.geography.source_text import (
    GENERIC_LOCATION_WORDS,
    NON_COUNTRY_TOKENS,
    SOURCE_GEOGRAPHY_PAIR_SEPARATORS,
    US_STATE_CODES,
    US_STATE_NAMES,
    geography_text_keys,
    has_us_context,
    source_geography_candidates,
)

COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
EXPLICIT_LOCATION_PAIR_PARTS = 2
NON_COUNTRY_CODES = frozenset({"EU", "EZ", "QO", "UN", "ZZ"})
REGION_SCOPE_ALIASES = {
    "eu": "EU",
    "europe": "EU",
    "european union": "EU",
}
def _codes(value: str) -> frozenset[str]:
    return frozenset(value.split())


_EU_COUNTRIES = _codes("AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK")
REGION_SCOPE_COUNTRIES = {
    "eu": _EU_COUNTRIES,
    "europe": _EU_COUNTRIES,
}


@dataclass(frozen=True)
class _GeographyLookup:
    names: dict[str, str]
    codes: frozenset[str]
    aliases: dict[str, str]


def normalize_request_geography(value: str) -> str:
    geography = normalize_source_geography(value)
    if geography is None:
        raise ValueError(f"unsupported geography: {value}")
    return geography


def normalize_source_geography(value: str) -> str | None:
    geographies = normalize_source_geographies(value)
    return geographies[0] if geographies else None


def normalize_source_geographies(value: str) -> tuple[str, ...]:
    paired_geographies = _paired_explicit_geographies(value)
    if paired_geographies is not None:
        return paired_geographies

    geographies: list[str] = []
    has_us = has_us_context(value)
    for candidate in source_geography_candidates(value):
        geography = _normalize_source_geography_candidate(candidate, has_us_context=has_us)
        if geography and geography not in geographies:
            geographies.append(geography)
    return tuple(geographies)


def has_specific_location_hint(value: str) -> bool:
    for candidate in source_geography_candidates(value):
        keys = geography_text_keys(candidate)
        words = {word for key in keys for word in key.split()}
        if not keys or words & GENERIC_LOCATION_WORDS:
            continue
        if any(is_region_scope(geography) for geography in normalize_source_geographies(candidate)):
            continue
        return True
    return False


def _normalize_source_geography_candidate(value: str, *, has_us_context: bool) -> str | None:
    geography = _normalize_direct_source_geography_candidate(value, has_us_context=has_us_context)
    if geography:
        return geography

    keys = geography_text_keys(value)
    if any(key in NON_COUNTRY_TOKENS for key in keys):
        return None

    country_code = city_country_code_for_keys(keys)
    if country_code:
        return country_code
    return None


def _normalize_direct_source_geography_candidate(value: str, *, has_us_context: bool = False) -> str | None:
    keys = geography_text_keys(value)
    for key in keys:
        region = REGION_SCOPE_ALIASES.get(key)
        if region:
            return region
    if any(key in NON_COUNTRY_TOKENS for key in keys):
        return None
    if has_us_context and any(key in US_STATE_NAMES for key in keys):
        return None

    text = value.strip()
    upper = text.upper()
    lookup = _country_lookup()
    if upper in lookup.aliases:
        return lookup.aliases[upper]
    if upper in lookup.codes:
        return upper
    for key in keys:
        country_code = lookup.names.get(key)
        if country_code:
            return country_code
    if not SOURCE_GEOGRAPHY_PAIR_SEPARATORS.search(value):
        for key in keys:
            geography = _direct_geography_from_key_suffix(key, lookup, has_us_context=has_us_context)
            if geography:
                return geography
    return None


def _paired_explicit_geographies(value: str) -> tuple[str, ...] | None:
    parts = tuple(part.strip() for part in SOURCE_GEOGRAPHY_PAIR_SEPARATORS.split(value) if part.strip())
    if len(parts) != EXPLICIT_LOCATION_PAIR_PARTS:
        return None
    first_part_direct = _direct_geographies_for_text(parts[0])
    second_part_direct = _direct_geographies_for_text(parts[1])
    if first_part_direct or not second_part_direct:
        return None
    return second_part_direct


def _direct_geographies_for_text(value: str) -> tuple[str, ...]:
    geographies: list[str] = []
    for candidate in source_geography_candidates(value):
        geography = _normalize_direct_source_geography_candidate(candidate)
        if geography and geography not in geographies:
            geographies.append(geography)
    return tuple(geographies)


def _direct_geography_from_key_suffix(
    key: str,
    lookup: _GeographyLookup,
    *,
    has_us_context: bool,
) -> str | None:
    if has_us_context and key in US_STATE_NAMES:
        return None
    words = key.split()
    for start in range(max(len(words) - 3, 0), len(words)):
        suffix = " ".join(words[start:])
        if not suffix or suffix in NON_COUNTRY_TOKENS:
            continue
        if has_us_context and any(state_name.endswith(suffix) for state_name in US_STATE_NAMES):
            continue
        region = REGION_SCOPE_ALIASES.get(suffix)
        if region:
            return region
        upper = suffix.upper()
        if has_us_context and upper in US_STATE_CODES:
            continue
        if upper in lookup.aliases:
            return lookup.aliases[upper]
        if upper in lookup.codes:
            return upper
        country_code = lookup.names.get(suffix)
        if country_code:
            return country_code
    return None


def geography_matches_any(evidence: str, requested_geographies: tuple[str, ...]) -> bool:
    evidence_countries = geography_countries(evidence)
    if not evidence_countries:
        return False
    return any(bool(evidence_countries & geography_countries(requested)) for requested in requested_geographies)


def geography_countries(geography: str) -> frozenset[str]:
    if not geography:
        return frozenset()
    region_countries = REGION_SCOPE_COUNTRIES.get(geography.casefold())
    if region_countries is not None:
        return region_countries
    if COUNTRY_CODE_PATTERN.fullmatch(geography.upper()):
        return frozenset({geography.upper()})
    return frozenset()


def is_region_scope(geography: str) -> bool:
    return geography.casefold() in REGION_SCOPE_COUNTRIES


@lru_cache(maxsize=1)
def _country_lookup() -> _GeographyLookup:
    name_codes: dict[str, set[str]] = {}
    codes: set[str] = set()
    for locale_name in localedata.locale_identifiers():
        locale = Locale.parse(locale_name)
        for code, name in locale.territories.items():
            if not isinstance(code, str) or not isinstance(name, str) or not _valid_country_code(code):
                continue
            codes.add(code)
            for country_key in geography_text_keys(name):
                if country_key not in NON_COUNTRY_TOKENS:
                    name_codes.setdefault(country_key, set()).add(code)
    names = {
        country_key: next(iter(country_codes))
        for country_key, country_codes in name_codes.items()
        if len(country_codes) == 1
    }
    aliases = _country_code_aliases(frozenset(codes))
    return _GeographyLookup(names=names, codes=frozenset(codes), aliases=aliases)


def _country_code_aliases(codes: frozenset[str]) -> dict[str, str]:
    aliases = {"THE NETHERLANDS": "NL", "UK": "GB"}
    territory_aliases = get_global("territory_aliases")
    for alias, replacements in territory_aliases.items():
        if not isinstance(alias, str) or not COUNTRY_CODE_PATTERN.fullmatch(alias):
            continue
        if not isinstance(replacements, list) or len(replacements) != 1:
            continue
        replacement = replacements[0]
        if isinstance(replacement, str) and replacement in codes:
            aliases[alias] = replacement
    return aliases


def _valid_country_code(code: str) -> bool:
    return bool(COUNTRY_CODE_PATTERN.fullmatch(code)) and code not in NON_COUNTRY_CODES
