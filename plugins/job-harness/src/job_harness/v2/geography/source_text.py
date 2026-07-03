"""Helpers for source-provided location text."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

SOURCE_GEOGRAPHY_CANDIDATE_SEPARATORS = re.compile(r"[/(),;|]+")
SOURCE_GEOGRAPHY_PAIR_SEPARATORS = re.compile(r"[/,;|]+")
COUNTRY_NAME_SEPARATORS = re.compile(r"[_/(),;|-]+")
COUNTRY_WORD_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)
SOURCE_LOCATION_DESCRIPTOR_EDGE_PATTERN = re.compile(
    r"^(?:fully\s+remote|remote|hybrid|onsite|on-site|office)\s+"
    r"|\s+(?:fully\s+remote|remote|hybrid|onsite|on-site|office)(?:\s+\d+)?$",
    re.I,
)
SOURCE_US_PREFIX_PATTERN = re.compile(r"^(?:US|USA|United States)\s+-\s+", re.I)
SOURCE_US_CONTEXT_PATTERN = re.compile(r"\b(?:US|USA|United States)\b", re.I)
NON_COUNTRY_TOKENS = frozenset(
    ["anywhere", "apac", "cis", "emea", "global", "iberia", "latam", "remote", "worldwide"]
)
GENERIC_LOCATION_WORDS = frozenset(
    [
        "amer",
        "americas",
        "anywhere",
        "apac",
        "apj",
        "cis",
        "emea",
        "fully",
        "global",
        "locations",
        "multiple",
        "remote",
        "worldwide",
    ]
)
US_STATE_CODES = frozenset(
    [
        "AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI",
        "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN",
        "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH",
        "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA",
        "WI", "WV", "WY",
    ]
)
US_STATE_NAMES = frozenset(
    [
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
    ]
)


@lru_cache(maxsize=8192)
def geography_text_keys(value: str) -> tuple[str, ...]:
    folded = COUNTRY_NAME_SEPARATORS.sub(" ", value).casefold().strip()
    words = " ".join(COUNTRY_WORD_PATTERN.findall(folded))
    ascii_words = _strip_accents(words)
    return tuple(dict.fromkeys(key for key in (folded, words, ascii_words) if key))


@lru_cache(maxsize=8192)
def source_geography_candidates(value: str) -> tuple[str, ...]:
    text = value.strip()
    if not text:
        return ()
    has_us = has_us_context(text)
    parts = [text]
    parts.extend(part.strip() for part in SOURCE_GEOGRAPHY_CANDIDATE_SEPARATORS.split(text))
    candidates: list[str] = []
    for part in parts:
        if not part:
            continue
        if has_us and part.upper() in US_STATE_CODES:
            continue
        candidates.append(part)
        if SOURCE_US_PREFIX_PATTERN.search(part):
            candidates.append("US")
        cleaned = SOURCE_LOCATION_DESCRIPTOR_EDGE_PATTERN.sub("", part).strip()
        if cleaned and cleaned != part:
            candidates.append(cleaned)
    return tuple(dict.fromkeys(candidates))


@lru_cache(maxsize=8192)
def has_us_context(value: str) -> bool:
    if SOURCE_US_CONTEXT_PATTERN.search(value):
        return True
    keys = geography_text_keys(value)
    words = {word for key in keys for word in key.split()}
    return "us" in words or "usa" in words or "united states" in keys


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))
