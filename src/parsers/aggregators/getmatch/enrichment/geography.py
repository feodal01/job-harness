"""Getmatch place record → geography in the brief's vocabulary.

Getmatch gives a hierarchy `location_id` / `ancestors` (`kazan__tatarstan__russia`; ancestors end
with groups like `_cu-europe`) and Russian or English labels. Labels are resolved through CLDR and
GeoNames (the application's GeographyCatalog); the country slug of the hierarchy is the fallback for labels
outside CLDR ("Словацкая Республика" → `slovakia`). Nothing is guessed: ambiguous or unknown
parts stay None, the label is kept.
"""

from dataclasses import replace
from typing import Any

from src.enrichment.staged import PlaceGeography
from src.geography.catalog import GeographyCatalog

GROUP_PREFIX = "_cu-"
WORLD = "_cu-world"


def place_geography(entry: dict[str, Any], catalog: GeographyCatalog) -> PlaceGeography:
    location_id = entry.get("location_id") or ""
    city_label = entry.get("city") or None
    country_label = entry.get("country") or None
    base = PlaceGeography(
        None, label=city_label or country_label, city=city_label, worldwide=location_id == WORLD
    )
    if location_id == WORLD:
        return replace(base, scope="world")
    if location_id.startswith(GROUP_PREFIX):
        group = catalog.group(base.label) if base.label else None
        return replace(base, scope="group", group=group.value if group else None)
    country = _country(entry, catalog)
    if city_label is None:
        return replace(base, scope="country" if country else None, country=country)
    if country:
        city = catalog.city(country, city_label)
        if city.status == "found" and city.value is not None:
            region = catalog.city_region(city.value)
            return replace(base, scope="city", country=country, geoname_id=city.value, region=region)
        region_id = catalog.region(country, city_label)
        if region_id.status == "found" and region_id.value is not None:
            return replace(base, scope="region", country=country, region=catalog.region_name(region_id.value))
    # A city label we cannot identify (ambiguous, absent from GeoNames, or garbage).
    return replace(base, scope="city", country=country)


def _country(entry: dict[str, Any], catalog: GeographyCatalog) -> str | None:
    if label := entry.get("country"):
        found = catalog.country(label)
        if found.status == "found":
            return found.value
    slugs = [a for a in entry.get("ancestors") or [] if not a.startswith(GROUP_PREFIX)]
    if slugs:
        found = catalog.country(slugs[-1].replace("-", " "))
        if found.status == "found":
            return found.value
    return None
