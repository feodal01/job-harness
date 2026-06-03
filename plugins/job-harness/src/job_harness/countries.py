"""CIS country directory and normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Country:
    code: str
    name_ru: str
    name_en: str
    status: str
    aliases: tuple[str, ...]


CIS_COUNTRIES: tuple[Country, ...] = (
    Country("AM", "Армения", "Armenia", "member", ("armenia", "армения", "am")),
    Country("AZ", "Азербайджан", "Azerbaijan", "member", ("azerbaijan", "азербайджан", "az")),
    Country("BY", "Беларусь", "Belarus", "member", ("belarus", "беларусь", "белоруссия", "by")),
    Country("KZ", "Казахстан", "Kazakhstan", "member", ("kazakhstan", "казахстан", "kz")),
    Country("KG", "Кыргызстан", "Kyrgyzstan", "member", ("kyrgyzstan", "киргизия", "кыргызстан", "kg")),
    Country("MD", "Молдова", "Moldova", "member", ("moldova", "молдова", "md")),
    Country("RU", "Россия", "Russia", "member", ("russia", "россия", "рф", "ru")),
    Country("TJ", "Таджикистан", "Tajikistan", "member", ("tajikistan", "таджикистан", "tj")),
    Country("UZ", "Узбекистан", "Uzbekistan", "member", ("uzbekistan", "узбекистан", "uz")),
    Country("TM", "Туркменистан", "Turkmenistan", "associate", ("turkmenistan", "туркменистан", "tm")),
    Country("GE", "Грузия", "Georgia", "former", ("georgia", "грузия", "ge")),
    Country("UA", "Украина", "Ukraine", "former", ("ukraine", "украина", "ua")),
)

CIS_COUNTRY_CODES: tuple[str, ...] = tuple(country.code for country in CIS_COUNTRIES)

_COUNTRIES_BY_CODE = {country.code: country for country in CIS_COUNTRIES}
_ALIASES = {
    alias.casefold(): country.code
    for country in CIS_COUNTRIES
    for alias in (country.code, country.name_ru, country.name_en, *country.aliases)
}


def normalize_country_code(value: str | None) -> str | None:
    """Return a CIS country code for user input, or raise for unsupported input."""
    if value is None:
        return None

    normalized = value.strip().casefold()
    if not normalized:
        return None
    if normalized not in _ALIASES:
        available = ", ".join(country.code for country in CIS_COUNTRIES)
        raise ValueError(f"Unknown CIS country: {value}. Available country codes: {available}")
    return _ALIASES[normalized]


def get_country(code: str) -> Country:
    normalized = normalize_country_code(code)
    if normalized is None:
        raise ValueError("Country code is required")
    return _COUNTRIES_BY_CODE[normalized]


def format_country_codes(codes: tuple[str, ...] | list[str]) -> str:
    return ", ".join(code for code in codes)
