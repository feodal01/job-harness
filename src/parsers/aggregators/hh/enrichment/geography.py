"""HH `area` → geography in the brief's vocabulary.

The area id is looked up in HH's own directory (areas.py): country → region → city. The country is
HH's `countryIsoCode` (cards) or the HH country name resolved through CLDR; the region and city are
resolved through GeoNames. A city name may carry its region in brackets («Химки (Московская область)»,
«Троицк (Москва)»): that qualifier names the region before HH's parent. Nothing is guessed.
"""

from dataclasses import replace
from typing import Any

from src.enrichment.staged import PlaceGeography
from src.geography.catalog import GeographyCatalog
from src.geography.names import split_city
from src.parsers.aggregators.hh.enrichment.areas import Area, HHAreas


def area_id(area: dict[str, Any]) -> str | None:
    # Cards carry `id`, the listing `@id`.
    value = area.get("id", area.get("@id"))
    return str(value) if value is not None else None


def place_geography(area: dict[str, Any], areas: HHAreas, catalog: GeographyCatalog) -> PlaceGeography:
    label = area.get("name") if isinstance(area.get("name"), str) else None
    chain = areas.chain(area_id(area) or "")
    country = _country(area, chain, catalog)
    base = PlaceGeography(None, country=country, label=label, city=label)
    if not chain:
        # An area missing from the snapshot: only HH's country and the label are known.
        return replace(base, scope="city", geoname_id=_city(catalog, country, label, None))
    node = chain[-1]
    if country is not None and catalog.country(node.name).value == country:
        return replace(base, scope="country", city=None)
    region = chain[-2] if len(chain) >= 3 else None  # [country, region, city]
    if node.has_children:
        return replace(base, scope="region", city=None, region=_region(catalog, country, node.name))
    geoname_id = _city(catalog, country, node.name, region)
    if geoname_id is not None:
        return replace(base, scope="city", geoname_id=geoname_id, region=catalog.city_region(geoname_id))
    return replace(base, scope="city", region=_region(catalog, country, region.name) if region else None)


def _country(area: dict[str, Any], chain: list[Area], catalog: GeographyCatalog) -> str | None:
    code = area.get("countryIsoCode")
    if isinstance(code, str) and catalog.country(code).status == "found":
        return catalog.country(code).value
    # The top of the chain is a country, or «Другие регионы» holding countries.
    for node in chain[:2]:
        found = catalog.country(node.name)
        if found.status == "found":
            return found.value
    return None


def _region(catalog: GeographyCatalog, country: str | None, label: str) -> str | None:
    if country is None:
        return None
    found = catalog.region(country, label)
    return catalog.region_name(found.value) if found.status == "found" and found.value is not None else None


def _city(
    catalog: GeographyCatalog, country: str | None, label: str | None, region: Area | None
) -> int | None:
    if country is None or not label:
        return None
    name, qualifier = split_city(label)
    found = catalog.city(country, name, qualifier or (region.name if region else None))
    return found.value if found.status == "found" else None
