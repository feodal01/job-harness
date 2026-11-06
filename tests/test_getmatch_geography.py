import asyncio
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from geo_support import GETMATCH_FORM, confirmed, getmatch_form, with_geography

from src.app import create_app
from src.core.config import Settings

FIXTURES = Path(__file__).parent / "fixtures/getmatch"


def app_for(tmp_path, handler):
    return create_app(
        Settings(
            database_path=tmp_path / "test.sqlite",
            sources={
                "getmatch": {"interval_seconds": 0, "max_attempts": 1, "page_size": 10},
            },
        ),
        transport=httpx.MockTransport(with_geography(handler)),
    )


async def test_getmatch_region_choices_are_available_without_fake_countries(tmp_path):
    calls = []
    app = app_for(tmp_path, lambda r: calls.append(r) or httpx.Response(500))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            choices = [p.model_dump() for p in app.state.engine.sources["getmatch"].geography.options]
            assert {x["id"] for x in choices} == {"moscow", "saints_p", "regions_ru", "relocate", "remote"}
            assert next(x["name"] for x in choices if x["id"] == "moscow") == "Офис или гибрид в Москве"
            catalog = {s["id"]: s for s in (await c.get("/api/v1/sources")).json()["items"]}
            assert catalog["getmatch"]["geography"] == "regions"
            assert catalog["hh_ru"]["geography"] == "country_city"
            assert catalog["getmatch"]["capabilities"]["query_parameters"]["locations"] == "native"
            assert (await c.get("/api/v1/sources/getmatch/countries")).status_code == 404
            assert (await c.get("/api/v1/sources/hh_ru/regions")).status_code == 404
            assert calls == []


async def test_selected_regions_reach_offer_requests_pagination_and_logs(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        assert r.url.path == "/api/offers"
        assert "sp" not in r.url.params
        name = "qa_auto_page2" if r.url.params["offset"] == "10" else "qa_auto"
        return httpx.Response(200, text=(FIXTURES / f"{name}.json").read_text())

    app = app_for(tmp_path, handler)
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        engine.sources["getmatch"] = replace(engine.sources["getmatch"], vacancy_details=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            response = await c.post(
                "/api/v1/briefs",
                json={
                    "name": "Regions",
                    "queries": ["QA Auto"],
                    "filters": {
                        "locations": confirmed()["locations"],
                        "minimum_work_format": "office",
                    },
                },
            )
            assert response.status_code == 201, response.text
            brief = response.json()
            run = await c.post(
                "/api/v1/runs",
                json={
                    "brief_id": brief["id"],
                    "sources": ["getmatch"],
                    "limits": {"getmatch": {"max_items_per_query": 12}},
                },
            )
            assert run.status_code == 202, run.text
            await asyncio.gather(*engine.tasks.values())
            result = (await c.get("/api/v1/runs/" + run.json()["id"])).json()
            assert result["status"] == "complete"
            assert result["request"]["brief"]["filters"]["locations"][0]["city"] == "Moscow"
            offers = [r for r in calls if r.url.path == "/api/offers"]
            assert len(offers) == 2
            assert all(r.url.params.get_list("l") == ["moscow", "remote"] for r in offers)
            events = (await c.get("/api/v1/runs/" + run.json()["id"] + "/events")).json()
            mapped = [e["data"] for e in events if e["event"] == "search.mapping"]
            assert len(mapped) == 2 and all(e["parameters"]["l"] == ["moscow", "remote"] for e in mapped)
            assert result["metrics"]["pages"] == 2
            assert result["metrics"]["preparation_requests"] == 1
            plan = next(e["data"] for e in events if e["event"] == "search.plan")
            assert plan["application"]["locations"] == "local"
            [place] = plan["places"]
            assert place["place"]["geoname_id"] == 524901 and place["status"] == "broader"
            geography, work_format, remote = place["conditions"]
            assert (geography["condition"], geography["status"], geography["params"]) == (
                "locations",
                "exact",
                {"l": ["moscow"]},
            )
            assert geography["origin"].startswith("form https://getmatch.ru/vacancies ")
            assert work_format["condition"] == "minimum_work_format"
            assert work_format["accepted"] == ["office", "hybrid", "remote_country", "remote_worldwide"]
            assert (remote["status"], remote["params"]) == ("broader", {"l": ["remote"]})


@pytest.mark.parametrize(
    "location",
    [
        {"source": "getmatch", "country": {"id": "113", "name": "Россия"}},
        {"source": "getmatch", "options": [{"id": "113", "name": "Россия"}]},
        {"source": "getmatch", "options": [{"id": "remote", "name": "Из любой точки мира"}]},
        {"source": "getmatch", "options": []},
        {"source": "getmatch", "options": [{"id": "remote", "name": "Удалённо"}] * 2},
        {
            "source": "getmatch",
            "country": {"id": "113", "name": "Россия"},
            "options": [{"id": "remote", "name": "Удалённо"}],
        },
        {"source": "hh_ru", "options": [{"id": "remote", "name": "Удалённо"}]},
    ],
)
async def test_invalid_geography_is_rejected_before_http_or_brief_creation(tmp_path, location):
    calls = []
    app = app_for(tmp_path, lambda r: calls.append(r) or httpx.Response(500))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            r = await c.post(
                "/api/v1/briefs", json={"name": "Invalid", "queries": ["QA"], "location": location}
            )
            assert r.status_code == 422, r.text
            assert (await c.get("/api/v1/briefs")).json() == []
            assert calls == []


def test_each_native_region_survives_feed_pagination():
    from src.parsers.aggregators.getmatch.parser import GetmatchSearch
    from src.parsers.models import Location

    for key in ["moscow", "saints_p", "regions_ru", "relocate", "remote"]:
        search = GetmatchSearch(10)
        location = Location(source="getmatch", options=[{"id": key, "name": key}])
        offers = search.build("", location=location)
        next_page = search.parse((FIXTURES / "qa_auto.json").read_text(), offers).next_requests[0]
        assert parse_qs(urlsplit(offers.url).query)["l"] == [key]
        assert parse_qs(urlsplit(next_page.url).query)["l"] == [key]


def search(ttl=86400):
    from src.parsers.aggregators.getmatch.geography import GetmatchGeography
    from src.parsers.aggregators.getmatch.parser import GetmatchSearch

    return GetmatchSearch(10, GetmatchGeography(ttl))


def city(country, geoname_id, minimum="office"):
    return {
        "country": country,
        "geoname_id": geoname_id,
        "city": str(geoname_id),
        "minimum_work_format": minimum,
    }


def zones(plan):
    return [parse_qs(urlsplit(r.url).query).get("l", []) for r in plan.requests]


async def prepared(locations, fetch=getmatch_form, **options):
    from src.models.brief import BriefInput

    brief = BriefInput(name="Geo", filters={"locations": locations, **options.pop("filters", {})})
    return await search().prepare(brief, fetch, **options)


@pytest.mark.parametrize(
    "place,expected,office_status",
    [
        (
            {"country": "RU", "scope": "country", "minimum_work_format": "office"},
            ["moscow", "saints_p", "regions_ru", "remote"],
            "exact",
        ),
        (city("RU", 524901), ["moscow", "remote"], "exact"),  # Moscow
        (city("RU", 498817), ["saints_p", "remote"], "exact"),  # Saint Petersburg
        (city("RU", 551487), ["regions_ru", "remote"], "broader"),  # Kazan
        (city("RU", 463829), ["regions_ru", "remote"], "broader"),  # Zelenograd: not known to be `moscow`
        (city("RU", 548391), ["regions_ru", "remote"], "broader"),  # Kirovsk, Murmansk
        (city("RU", 548392), ["regions_ru", "remote"], "broader"),  # Kirovsk, Leningrad oblast
        (city("AM", 616052), ["relocate", "remote"], "broader"),  # Yerevan
        ({"country": "AM", "scope": "country"}, ["relocate", "remote"], "broader"),
    ],
)
async def test_place_maps_to_getmatch_zones_by_identity_not_spelling(place, expected, office_status):
    plan = await prepared([place])
    assert zones(plan) == [expected]
    [result] = plan.places
    geography, work_format, remote = result.conditions
    assert (geography.condition, geography.status) == ("locations", office_status)
    assert (work_format.condition, work_format.status) == ("minimum_work_format", "exact")
    assert (remote.condition, remote.params, remote.status) == ("locations", {"l": ("remote",)}, "broader")
    assert plan.application["locations"] == "local"


@pytest.mark.parametrize("minimum", ["remote_country", "remote_worldwide"])
async def test_remote_minimum_sends_only_remote(minimum):
    plan = await prepared([city("RU", 524901, minimum)])
    assert zones(plan) == [["remote"]]
    assert plan.application["minimum_work_format"] == "native"
    # Geography is prepared independently of the format; combining drops the unused zone.
    geography, work_format, remote = plan.places[0].conditions
    assert geography.params == {"l": ("moscow",)}
    assert (
        work_format.accepted
        == ("remote_country", "remote_worldwide")[("remote_country", "remote_worldwide").index(minimum) :]
    )
    assert work_format.status == ("broader" if minimum == "remote_worldwide" else "exact")


async def test_each_place_is_its_own_branch_and_same_zones_share_a_request():
    plan = await prepared(
        [
            {"country": "RU", "scope": "country"},
            city("RU", 524901),
            city("RU", 551487),  # Kazan
            city("RU", 499099),  # Samara: same zones as Kazan
            city("AM", 616052, "remote_worldwide"),
        ]
    )
    assert zones(plan) == [
        ["moscow", "saints_p", "regions_ru", "remote"],
        ["moscow", "remote"],
        ["regions_ru", "remote"],
        ["remote"],
    ]
    assert len(plan.places) == 5


async def test_second_chance_relaxes_format_but_keeps_place_zones():
    plan = await prepared([city("RU", 498817, "remote_country")], second_chance=True)
    assert zones(plan) == [["saints_p", "remote"]]


async def test_published_after_reaches_every_branch():
    from datetime import date, timedelta

    plan = await prepared(
        [city("RU", 524901), city("AM", 616052)],
        filters={"published_after": (date.today() - timedelta(days=2)).isoformat()},
    )
    assert [parse_qs(urlsplit(r.url).query)["pa"] for r in plan.requests] == [["3d"], ["3d"]]
    assert plan.application["published_after"] == "native"


async def test_form_check_is_cached_and_refreshed_after_ttl():
    from src.models.brief import BriefInput

    calls = []

    async def fetch(url):
        calls.append(url)
        return await getmatch_form(url)

    brief = BriefInput(name="Geo", filters={"locations": [city("RU", 524901)]})
    cached = search()
    await cached.prepare(brief, fetch)
    await cached.prepare(brief, fetch)
    assert len(calls) == 1
    expired = search(ttl=0)
    await expired.prepare(brief, fetch)
    await expired.prepare(brief, fetch)
    assert len(calls) == 3


async def test_zone_missing_from_form_fails_only_its_branch():
    form = GETMATCH_FORM.read_text().replace("_moscow", "_msk")

    async def fetch(url):
        return form

    plan = await prepared([city("RU", 524901), city("RU", 551487)], fetch)
    assert zones(plan) == [["regions_ru", "remote"]]
    moscow, kazan = plan.places
    assert moscow.status == "error" and "moscow" in (moscow.conditions[0].reason or "")
    assert kazan.status == "broader"


async def test_form_without_zone_values_is_a_preparation_error():
    from src.network.client import FetchError

    async def fetch(url):
        return "<html><body>redesigned page</body></html>"

    with pytest.raises(FetchError, match="no location values"):
        await prepared([city("RU", 524901)], fetch)


async def test_failed_branch_is_reported_and_other_places_still_run(tmp_path):
    form = GETMATCH_FORM.read_text().replace("_moscow", "_msk")
    calls = []

    def handler(r):
        if r.url.path == "/vacancies":
            return httpx.Response(200, text=form)
        calls.append(r)
        name = "qa_auto_page2" if r.url.params["offset"] == "10" else "qa_auto"
        return httpx.Response(200, text=(FIXTURES / f"{name}.json").read_text())

    app = create_app(
        Settings(
            database_path=tmp_path / "test.sqlite",
            sources={"getmatch": {"interval_seconds": 0, "max_attempts": 1, "page_size": 10}},
        ),
        transport=httpx.MockTransport(handler),
    )
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        engine.sources["getmatch"] = replace(engine.sources["getmatch"], vacancy_details=None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost") as c:
            places = confirmed(
                {"locations": [{"country": "RU", "city": "Москва"}, {"country": "AM", "city": "Ереван"}]}
            )
            brief = (await c.post("/api/v1/briefs", json={"name": "Geo", "filters": places})).json()
            run = (
                await c.post("/api/v1/runs", json={"brief_id": brief["id"], "sources": ["getmatch"]})
            ).json()
            await asyncio.gather(*engine.tasks.values())
            result = (await c.get("/api/v1/runs/" + run["id"])).json()
            events = (await c.get("/api/v1/runs/" + run["id"] + "/events")).json()
    assert {tuple(r.url.params.get_list("l")) for r in calls} == {("relocate", "remote")}
    assert result["sources"]["getmatch"]["status"] == "partial"
    assert any(e["event"] == "listing.error" and "moscow" in e["data"]["error"] for e in events)


async def test_hybrid_minimum_is_broader_because_office_and_hybrid_share_a_zone():
    plan = await prepared([city("RU", 551487, "hybrid")])
    assert zones(plan) == [["regions_ru", "remote"]]
    work_format = plan.places[0].conditions[1]
    assert (work_format.status, work_format.mode) == ("broader", "local")


async def test_second_chance_skips_soft_steps_in_the_base_class():
    from datetime import date, timedelta

    recent = {"published_after": (date.today() - timedelta(days=2)).isoformat()}
    plan = await prepared([city("RU", 498817, "remote_country")], filters=recent, second_chance=True)
    assert [parse_qs(urlsplit(r.url).query)["pa"] for r in plan.requests] == [["all"]]
    assert plan.places[0].minimum_work_format is None
    assert plan.places[0].conditions[1].accepted == ("office", "hybrid", "remote_country", "remote_worldwide")
    assert plan.application["published_after"] == "local"


def staged(params_by_condition):
    from src.parsers.interfaces import Prepared, SearchRequest, StagedSearch

    class Source(StagedSearch):
        fields = frozenset()
        query_notes = ""
        parameters = {}

        def _application(self):
            return {}

        async def _prepare_geography(self, fetch, place):
            return Prepared("locations", "native", params=params_by_condition["locations"])

        def _prepare_query(self, query):
            return Prepared("query", "native", params=params_by_condition["query"](query))

        def _request(self, query, params):
            return SearchRequest(
                "https://example.org/?" + "&".join(f"{k}={','.join(v)}" for k, v in params.items()), query
            )

        def build(self, query, location=None):
            raise NotImplementedError

        def parse(self, body, request):
            raise NotImplementedError

    return Source()


async def test_default_combine_merges_independent_steps_per_place_and_phrase():
    from src.models.brief import BriefInput

    source = staged({"locations": {"area": ("1",)}, "query": lambda q: {"text": (q,)}})
    brief = BriefInput(name="X", queries=["QA", "Python"], filters={"locations": [city("RU", 524901)]})
    plan = await source.prepare(brief, getmatch_form)
    assert [r.url for r in plan.requests] == [
        "https://example.org/?area=1&text=QA",
        "https://example.org/?area=1&text=Python",
    ]
    assert plan.application["locations"] == "native"


async def test_default_combine_rejects_clashing_parameters():
    from src.models.brief import BriefInput

    source = staged({"locations": {"text": ("Moscow",)}, "query": lambda q: {"text": (q,)}})
    brief = BriefInput(name="X", queries=["QA"], filters={"locations": [city("RU", 524901)]})
    with pytest.raises(ValueError, match="disagree"):
        await source.prepare(brief, getmatch_form)
