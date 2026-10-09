"""Compare spelling and explicit administrative qualifiers without guessing places."""

import re
import unicodedata


def normalize(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold().strip()) if not unicodedata.combining(c)
    )


def split_city(value: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", value.strip())
    return (match[1].strip(), match[2].strip()) if match else (value.strip(), None)


def region_key(value: str) -> frozenset[str]:
    words = re.findall(r"\w+", normalize(value))
    return frozenset(w for w in words if w not in {"область", "обл", "республика", "край"})


def region_matches(wanted: str, actual: str) -> bool:
    key = region_key(wanted)
    return bool(key) and any(key == region_key(part) for part in [actual, *actual.split(",")])


def city_matches(wanted: str, actual: str, region: str | None = None) -> bool | None:
    wanted_name, wanted_region = split_city(wanted)
    actual_name, actual_region = split_city(actual)
    if normalize(wanted_name) != normalize(actual_name):
        return False
    qualifier = wanted_region or region
    if qualifier:
        return region_matches(qualifier, actual_region) if actual_region else None
    return True
