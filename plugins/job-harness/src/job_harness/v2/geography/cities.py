"""Dataset-backed city geography lookup for v2 post-processing."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import geonamescache  # type: ignore[import-untyped]

_COUNTRY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
_COUNTRY_NAME_SEPARATORS = re.compile(r"[_/(),;|-]+")
_COUNTRY_WORD_PATTERN = re.compile(r"[\w'-]+", re.UNICODE)
_NON_COUNTRY_TOKENS = frozenset(
    {
        "anywhere",
        "apac",
        "cis",
        "emea",
        "global",
        "iberia",
        "latam",
        "remote",
        "worldwide",
    }
)
_NON_COUNTRY_CODES = frozenset({"EU", "EZ", "QO", "UN", "ZZ"})
_CITY_DOMINANT_POPULATION_RATIO = 10.0
_CITY_DOMINANT_MIN_POPULATION = 100_000
_MIN_CITY_COUNTRY_CANDIDATES = 2


def city_country_code_for_keys(keys: tuple[str, ...]) -> str | None:
    city_lookup = _city_country_lookup()
    for key in keys:
        country_code = city_lookup.get(key)
        if country_code:
            return country_code
    return None


@lru_cache(maxsize=1)
def _city_country_lookup() -> dict[str, str]:
    city_country_populations: dict[str, dict[str, int]] = {}
    for city in geonamescache.GeonamesCache().get_cities().values():
        if not isinstance(city, dict):
            continue
        country_code = city.get("countrycode")
        if not isinstance(country_code, str) or not _valid_country_code(country_code):
            continue
        population = city.get("population")
        city_population = population if isinstance(population, int) else 0
        for city_key in _city_name_keys(city):
            country_populations = city_country_populations.setdefault(city_key, {})
            country_populations[country_code] = max(country_populations.get(country_code, 0), city_population)
    return {
        city_key: country_code
        for city_key, country_populations in city_country_populations.items()
        for country_code in (_dominant_city_country(country_populations),)
        if country_code is not None
    }


def _city_name_keys(city: dict[object, object]) -> tuple[str, ...]:
    names: list[str] = []
    name = city.get("name")
    if isinstance(name, str):
        names.append(name)
    alternate_names = city.get("alternatenames")
    if isinstance(alternate_names, list):
        names.extend(name for name in alternate_names if isinstance(name, str))
    keys: list[str] = []
    for name in names:
        for key in _geography_text_keys(name):
            if key not in _NON_COUNTRY_TOKENS and key not in keys:
                keys.append(key)
    return tuple(keys)


def _dominant_city_country(country_populations: dict[str, int]) -> str | None:
    if len(country_populations) == 1:
        return next(iter(country_populations))
    ordered = sorted(country_populations.items(), key=lambda item: item[1], reverse=True)
    if len(ordered) < _MIN_CITY_COUNTRY_CANDIDATES:
        return None
    dominant_country, dominant_population = ordered[0]
    second_population = ordered[1][1]
    if dominant_population < _CITY_DOMINANT_MIN_POPULATION:
        return None
    if second_population <= 0:
        return dominant_country
    if dominant_population / second_population >= _CITY_DOMINANT_POPULATION_RATIO:
        return dominant_country
    return None


def _geography_text_keys(value: str) -> tuple[str, ...]:
    folded = _COUNTRY_NAME_SEPARATORS.sub(" ", value).casefold().strip()
    words = " ".join(_COUNTRY_WORD_PATTERN.findall(folded))
    ascii_words = _strip_accents(words)
    return tuple(dict.fromkeys(key for key in (folded, words, ascii_words) if key))


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _valid_country_code(code: str) -> bool:
    return bool(_COUNTRY_CODE_PATTERN.fullmatch(code)) and code not in _NON_COUNTRY_CODES
