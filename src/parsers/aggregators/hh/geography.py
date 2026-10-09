"""HH geography lookups. Calls use the source's shared HTTP budget."""

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlencode

from src.geography.directory import uniquely_identifies_city
from src.geography.names import normalize, region_matches, split_city
from src.models.filters import SearchPlace
from src.network.client import FetchError
from src.parsers.interfaces import CountryCityGeography, Fetch
from src.parsers.models import Place


class HHGeography(CountryCityGeography):
    mode: Literal["country_city"] = "country_city"

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self.cache: OrderedDict[str, tuple[float, list[Place]]] = OrderedDict()
        self.lock = asyncio.Lock()

    async def _load(
        self, fetch: Fetch, path: str, params: dict[str, str], parse: Callable[[Any], list[Place]]
    ) -> list[Place]:
        # host defaults to hh.ru, matching the HTML search source. locale controls labels.
        url = "https://api.hh.ru" + path + "?" + urlencode({"locale": "RU", **params})
        async with self.lock:
            cached = self.cache.get(url)
            if cached and cached[0] > time.monotonic():
                self.cache.move_to_end(url)
                return cached[1]
            try:
                result = parse(json.loads(await fetch(url)))
            except (KeyError, TypeError, ValueError) as exc:
                raise FetchError("parse_error", f"Invalid HH geography response: {exc}") from exc
            self.cache[url] = (time.monotonic() + self.ttl, result)
            self.cache.move_to_end(url)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return result

    async def countries(self, fetch: Fetch) -> list[Place]:
        def parse(data: Any) -> list[Place]:
            if not isinstance(data, list) or not data:
                raise ValueError("Country list is missing")
            return [Place(id=item["id"], name=item["name"]) for item in data]

        return await self._load(fetch, "/areas/countries", {}, parse)

    async def cities(self, fetch: Fetch, country_id: str, text: str) -> list[Place]:
        text = text.strip()
        if len(text) < 2:
            raise ValueError("Введите хотя бы два символа города")
        if country_id not in {country.id for country in await self.countries(fetch)}:
            raise ValueError("Выберите страну из списка")

        def parse(data: Any) -> list[Place]:
            items = data["items"]
            if not isinstance(items, list):
                raise ValueError("City suggestions are missing")
            result = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Invalid suggestion")
                parents, parent_ids = [], []
                parent = item.get("parent")
                while parent:
                    if not isinstance(parent, dict):
                        raise ValueError("Invalid parent region")
                    parents.append(parent["text"])
                    parent_ids.append(parent["id"])
                    parent = parent.get("parent")
                if country_id in parent_ids:
                    result.append(Place(id=item["id"], name=item["text"], path=list(reversed(parents))))
            return result

        return await self._load(fetch, "/suggests/area_leaves", {"area_id": country_id, "text": text}, parse)


def matches_place(native: Place, wanted: SearchPlace) -> bool:
    """Translate common labels to HH labels; ambiguous results must still fail in prepare."""
    if not wanted.city:
        return False
    name, qualifier = split_city(native.name)
    # Existing briefs may contain the complete HH label, including its qualifier.
    if normalize(wanted.city) not in {normalize(native.name), normalize(name)}:
        return False
    if not wanted.region:
        return True
    regions = [*native.path, *([qualifier] if qualifier else [])]
    return any(region_matches(wanted.region, r) for r in regions)


def resolve_city(choices: list[Place], wanted: SearchPlace) -> Place:
    matches = [c for c in choices if matches_place(c, wanted)]
    # Both directories must agree that the city name is unique. This handles
    # different admin hierarchies/spellings without choosing between homonyms.
    same_name = [
        c for c in choices if wanted.city and normalize(split_city(c.name)[0]) == normalize(wanted.city)
    ]
    if (
        not matches
        and len(same_name) == 1
        and normalize(same_name[0].name) == normalize(wanted.city or "")
        and wanted.city
        and uniquely_identifies_city(wanted.country, wanted.city, wanted.region)
    ):
        matches = same_name
    if len(matches) != 1:
        raise ValueError(
            f"HH: не удалось однозначно сопоставить город {wanted.city}, "
            f"регион: {wanted.region or 'не указан'}"
        )
    return matches[0]
