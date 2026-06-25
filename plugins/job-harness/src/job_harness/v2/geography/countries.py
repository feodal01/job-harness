"""Country and region normalization for v2 search policy."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from babel import Locale, localedata
from babel.core import get_global

from job_harness.v2.geography.cities import city_country_code_for_keys

COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
SOURCE_GEOGRAPHY_CANDIDATE_SEPARATORS = re.compile(r"[/(),;|]+")
COUNTRY_NAME_SEPARATORS = re.compile(r"[_/(),;|-]+")
COUNTRY_WORD_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)
NON_COUNTRY_TOKENS = frozenset(
    {
        "anywhere",
        "apac",
        "cis",
        "emea",
        "global",
        "latam",
        "remote",
        "worldwide",
    }
)
NON_COUNTRY_CODES = frozenset({"EU", "EZ", "QO", "UN", "ZZ"})
REGION_SCOPE_ALIASES = {
    "eu": "EU",
    "europe": "europe",
    "european union": "EU",
}
REGION_SCOPE_COUNTRIES = {
    "eu": frozenset(
        {
            "AT",
            "BE",
            "BG",
            "CY",
            "CZ",
            "DE",
            "DK",
            "EE",
            "ES",
            "FI",
            "FR",
            "GR",
            "HR",
            "HU",
            "IE",
            "IT",
            "LT",
            "LU",
            "LV",
            "MT",
            "NL",
            "PL",
            "PT",
            "RO",
            "SE",
            "SI",
            "SK",
        }
    ),
    "europe": frozenset(
        {
            "AD",
            "AL",
            "AT",
            "AX",
            "BA",
            "BE",
            "BG",
            "BY",
            "CH",
            "CY",
            "CZ",
            "DE",
            "DK",
            "EE",
            "ES",
            "FI",
            "FO",
            "FR",
            "GG",
            "GI",
            "GR",
            "HR",
            "HU",
            "IE",
            "IM",
            "IS",
            "IT",
            "JE",
            "LI",
            "LT",
            "LU",
            "LV",
            "MC",
            "MD",
            "ME",
            "MK",
            "MT",
            "NL",
            "NO",
            "PL",
            "PT",
            "RO",
            "RS",
            "SE",
            "SI",
            "SJ",
            "SK",
            "SM",
            "UA",
            "VA",
        }
    ),
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
    geographies: list[str] = []
    for candidate in _source_geography_candidates(value):
        geography = _normalize_source_geography_candidate(candidate)
        if geography and geography not in geographies:
            geographies.append(geography)
    return tuple(geographies)


def _normalize_source_geography_candidate(value: str) -> str | None:
    keys = geography_text_keys(value)
    for key in keys:
        region = REGION_SCOPE_ALIASES.get(key)
        if region:
            return region
    if any(key in NON_COUNTRY_TOKENS for key in keys):
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
    country_code = city_country_code_for_keys(keys)
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


def geography_text_keys(value: str) -> tuple[str, ...]:
    folded = COUNTRY_NAME_SEPARATORS.sub(" ", value).casefold().strip()
    words = " ".join(COUNTRY_WORD_PATTERN.findall(folded))
    ascii_words = _strip_accents(words)
    return tuple(dict.fromkeys(key for key in (folded, words, ascii_words) if key))


def _source_geography_candidates(value: str) -> tuple[str, ...]:
    text = value.strip()
    if not text:
        return ()
    parts = [text]
    parts.extend(part.strip() for part in SOURCE_GEOGRAPHY_CANDIDATE_SEPARATORS.split(text))
    return tuple(dict.fromkeys(part for part in parts if part))


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
    aliases = {"UK": "GB"}
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


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _valid_country_code(code: str) -> bool:
    return bool(COUNTRY_CODE_PATTERN.fullmatch(code)) and code not in NON_COUNTRY_CODES
