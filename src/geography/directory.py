"""Read-only, in-process city directory; no source IDs or external requests."""

import gzip
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.core.errors import InvalidSearchError
from src.geography.countries import COUNTRIES
from src.geography.names import city_matches, normalize, region_matches, split_city


@dataclass(frozen=True)
class City:
    name: str
    region: str | None
    population: int
    aliases: tuple[str, ...]


@lru_cache(maxsize=1)
def directory() -> dict[str, tuple[City, ...]]:
    data = json.loads(
        gzip.decompress(Path(__file__).with_name("data").joinpath("cities.json.gz").read_bytes())
    )
    countries: dict[str, list[City]] = {code: [] for code in COUNTRIES}
    for country, name, region, population, aliases in data:
        countries[country].append(City(name, region, population, tuple({normalize(a) for a in aliases})))
    return {code: tuple(cities) for code, cities in countries.items()}


class CityDirectory:
    def __init__(self) -> None:
        self._countries = directory()

    def search(self, country: str, text: str) -> list[dict]:
        if country not in COUNTRIES:
            raise InvalidSearchError("Choose a country from the catalog")
        query = normalize(text)
        if not 2 <= len(query) <= 100:
            raise InvalidSearchError("Введите от 2 до 100 символов города")
        found = [c for c in self._countries[country] if any(query in a for a in c.aliases)]
        found.sort(
            key=lambda c: (
                0 if query in c.aliases else 1 if any(a.startswith(query) for a in c.aliases) else 2,
                -c.population,
                c.name,
                c.region or "",
            )
        )
        result: list[dict] = []
        seen: set[tuple[str, str | None]] = set()
        for city in found:
            key = (city.name, city.region)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "name": city.name,
                    "region": city.region,
                    "path": [COUNTRIES[country]] + ([city.region] if city.region else []),
                }
            )
            if len(result) == 20:
                break
        return result


@lru_cache(maxsize=1024)
def uniquely_identifies_city(country: str, name: str, region: str | None) -> bool:
    """The chosen label exists and denotes one name/region pair within its country."""
    cities = [c for c in directory().get(country, ()) if normalize(c.name) == normalize(name)]
    regions = {c.region for c in cities}
    return len(regions) == 1 and any(
        region is None or (c.region is not None and region_matches(region, c.region)) for c in cities
    )


def match_city(country: str, wanted: str, actual: str, region: str | None = None) -> bool | None:
    match = city_matches(wanted, actual, region)
    if match is not None:
        return match
    name, qualifier = split_city(wanted)
    return True if uniquely_identifies_city(country, name, qualifier or region) else None
