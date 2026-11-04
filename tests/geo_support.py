"""Confirmed geography and native geography responses for search-engine fixtures."""

import inspect
from contextlib import closing
from pathlib import Path

import httpx

from src.geography.catalog import default_catalog


def confirmed(filters=None):
    result = dict(filters or {})
    locations = result.get("locations") or [{"country": "RU", "city": "Москва"}]
    selected = []
    lookup = default_catalog()
    for place in locations:
        if place.get("geoname_id"):
            selected.append(place)
            continue
        country = place["country"]
        if place.get("city"):
            cities = lookup.search(country, place["city"])
            city = cities[0]
        else:
            with closing(lookup.connect()) as c:
                row = c.execute(
                    "SELECT geoname_id,ascii_name FROM cities WHERE country=? "
                    "ORDER BY population DESC LIMIT 1",
                    (country,),
                ).fetchone()
            city = lookup.resolve(country, row["geoname_id"], row["ascii_name"])
        selected.append(
            {"country": country, "geoname_id": city["geoname_id"], "city_display_name": city["name"]}
        )
    result["locations"] = selected
    return result


GETMATCH_FORM = Path(__file__).parent / "fixtures/getmatch/vacancies_form.html"


async def getmatch_form(url):
    """Preparation fetch for Getmatch: only the vacancy form may be requested."""
    assert url == "https://getmatch.ru/vacancies", url
    return GETMATCH_FORM.read_text()


def _hh_countries() -> list[tuple[str, str, str]]:
    """A fake HH country list: (HH id, ISO code, Russian CLDR name); Russia keeps HH's real id 113."""
    names = default_catalog().country_names("ru")
    return [
        ("113" if code == "RU" else str(1000 + i), code, name) for i, (code, name) in enumerate(names.items())
    ]


def native_geography(request):
    if request.url.host == "getmatch.ru" and request.url.path == "/vacancies":
        return httpx.Response(200, text=GETMATCH_FORM.read_text())
    if request.url.host != "api.hh.ru":
        return None
    countries = _hh_countries()
    if request.url.path == "/areas/countries":
        return httpx.Response(200, json=[{"id": hh_id, "name": name} for hh_id, _, name in countries])
    if request.url.path == "/suggests/area_leaves":
        area = request.url.params["area_id"]
        code, name = next((code, name) for hh_id, code, name in countries if hh_id == area)
        cities = default_catalog().search(code, request.url.params["text"])
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": str(city["geoname_id"]),
                        "text": city["name"],
                        "parent": {"id": area, "text": name},
                    }
                    for city in cities
                ]
            },
        )
    return None


def with_geography(handler):
    async def wrapped(request):
        geography = native_geography(request)
        if geography is not None:
            return geography
        result = handler(request)
        return await result if inspect.isawaitable(result) else result

    return wrapped


def scheduling_sources(app):
    """Keep scheduler fixtures to one feed per phrase; real native geography has separate contract tests."""
    from dataclasses import fields

    from src.parsers.interfaces import SourceAdapter

    class SearchFixture:
        def __init__(self, search):
            self.search = search
            self.fields = search.fields
            self.query_notes = search.query_notes

        @property
        def parameters(self):
            return {**self.search.parameters, "locations": "none"}

        def build(self, query, location=None):
            return self.search.build(query, location)

        def parse(self, body, request):
            return self.search.parse(body, request)

        async def prepare(self, brief, fetch, *, second_chance=False):
            request = brief.model_copy(deep=True)
            if request.filters.locations:
                minimum, relocation = request.filters.location_conditions(request.filters.locations[0])
                request.filters.minimum_work_format = minimum
                request.filters.relocation_support = relocation
            request.filters.locations = []
            return await self.search.prepare(request, fetch, second_chance=second_chance)

    class FixtureSource(SourceAdapter):
        @property
        def capabilities(self):
            caps = super().capabilities
            return caps.model_copy(
                update={"query_parameters": {**caps.query_parameters, "locations": "none"}}
            )

    for name, adapter in list(app.state.engine.sources.items()):
        attributes = {f.name: getattr(adapter, f.name) for f in fields(adapter)}
        attributes["search"] = SearchFixture(adapter.search)
        app.state.engine.sources[name] = FixtureSource(**attributes)
