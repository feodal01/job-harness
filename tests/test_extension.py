import asyncio
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.db import Database
from src.models.brief import BriefInput
from src.models.company import Company
from src.models.run import RunInput
from src.models.vacancy import Vacancy
from src.parsers.models import Location, Place


def test_nested_source_settings_merge_environment_and_constructor(monkeypatch, tmp_path):
    config = tmp_path / "settings.toml"
    config.write_text("[sources.hh_ru]\ninterval_seconds = 0.27\nmax_attempts = 2\n")

    class TestSettings(Settings):
        model_config = {**Settings.model_config, "toml_file": config}

    monkeypatch.setenv("JOB_SEARCH_SOURCES__HH_RU__CONCURRENCY", "4")
    settings = TestSettings(sources={"hh_ru": {"max_attempts": 3}})
    assert settings.sources.hh_ru.concurrency == 4
    assert settings.sources.hh_ru.max_attempts == 3
    assert settings.sources.hh_ru.interval_seconds == 0.27
    assert TestSettings(sources={"hh_ru": {"concurrency": 1}}).sources.hh_ru.concurrency == 1


@pytest.mark.parametrize(
    "values",
    [
        {"sources": {"hh_ru": {"concurrency": 0}}},
        {"sources": {"hh_ru": {"max_attempts": 0}}},
        {"sources": {"hh_ru": {"unknown": True}}},
        {"sources": {"unknown": {}}},
    ],
)
def test_invalid_source_settings_are_rejected(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_hh_uses_source_location_id_without_static_city_mapping():
    from src.parsers.aggregators.hh import parser

    for area in ["113", "1", "2", "17607"]:
        params = parse_qs(
            urlsplit(
                parser.HHSearch()
                .build("аналитик", location=Location(source="hh_ru", country=Place(id=area, name="Place")))
                .url
            ).query
        )
        assert params["area"] == [area]
    assert "area" not in parse_qs(urlsplit(parser.HHSearch().build("аналитик").url).query)
    with pytest.raises(ValueError):
        parser.HHSearch().build(
            "аналитик", location=Location(source="hh_ru", country=Place(id="wrong", name="Place"))
        )


async def test_source_network_settings_and_breakers_are_independent(tmp_path):
    from src.network.client import FetchError, Network
    from src.parsers.interfaces import RequestSettings
    from src.parsers.registry import build_sources

    settings = Settings(sources={"hh_ru": {"interval_seconds": 0, "max_attempts": 1}})
    hh = build_sources(settings)["hh_ru"]
    other = replace(
        hh,
        id="other",
        resource="example.org",
        allowed_hosts=("example.org",),
        settings=RequestSettings(interval_seconds=0, max_attempts=2, retry_base_seconds=0),
    )
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "hh.ru":
            return httpx.Response(403)
        return httpx.Response(503 if calls.count("example.org") == 1 else 200, text="ok")

    store = Database(tmp_path / "test.sqlite")
    try:
        brief = store.briefs.save_brief(BriefInput(name="Сети", queries=["QA"]))
        run = store.runs.create_run(brief["id"], {"sources": ["hh_ru"]}, {})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(FetchError, match="blocked"):
                await Network(hh, store.resources, store.events, client).get(
                    run["id"], "https://hh.ru/", "listing"
                )
            assert (
                await Network(other, store.resources, store.events, client).get(
                    run["id"], "https://example.org/", "listing"
                )
                == "ok"
            )
        assert calls == ["hh.ru", "example.org", "example.org"]
        assert store.resources.resource("hh.ru")["open_until"] > 0
        assert store.resources.resource("example.org")["open_until"] == 0
    finally:
        store.close()


def test_source_ids_do_not_collide_and_embedded_companies_do_not_populate_directory(tmp_path):
    store = Database(tmp_path / "test.sqlite")
    try:
        brief = store.briefs.save_brief(BriefInput(name="Источники", queries=["QA"]))
        for source in ("hh_ru", "other"):
            run = store.runs.create_run(brief["id"], {"sources": [source]}, {})
            vacancy = Vacancy(
                id="1",
                title=source,
                url=f"https://{source}.example/job/1",
                company=Company(id="1", name=source, profile_url=f"https://{source}.example/1"),
            )
            store.vacancies.save_vacancy(run["id"], brief["id"], vacancy, source=source)
            assert store.vacancies.results(run["id"])[0]["is_new"] is True
            assert store.vacancies.results(run["id"])[0]["company"]["name"] == source
        assert store.stats()["vacancies"] == 2
        assert (
            store._connection.db.execute("SELECT name FROM sqlite_master WHERE name='companies'").fetchone()
            is None
        )
    finally:
        store.close()


async def test_engine_uses_selected_parser_and_request_policy(tmp_path):
    from types import SimpleNamespace

    from src.engine import SearchEngine
    from src.parsers.interfaces import RequestSettings, SearchPage, SearchRequest, SourceAdapter

    vacancy = Vacancy(id="1", title="Listing", url="https://example.org/jobs/1")

    async def prepare(brief, fetch, *, second_chance=False):
        from src.parsers.interfaces import SearchPlan

        return SearchPlan(tuple(search.build(q) for q in brief.queries), search.parameters)

    search = SimpleNamespace(
        prepare=prepare,
        parameters={"query": "native", "locations": "none"},
        query_notes="",
        fields=frozenset({"title"}),
        build=lambda query, location_id=None: SearchRequest(
            f"https://example.org/search?q={query}&page=0", query
        ),
        parse=lambda body, request: SearchPage(vacancies=[vacancy], total=1),
    )
    details = SimpleNamespace(
        fields=frozenset({"title"}),
        url=lambda row: row.url,
        parse=lambda body, row: row.model_copy(update={"title": "Detail"}),
    )
    source = SourceAdapter(
        id="other",
        name="Other",
        type="career_site",
        resource="example.org",
        allowed_hosts=("example.org",),
        search=search,
        vacancy_details=details,
        settings=RequestSettings(concurrency=1, interval_seconds=0, request_timeout_seconds=0.45),
    )
    calls = []
    store = Database(tmp_path / "test.sqlite")
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
        ) as client:
            engine = SearchEngine(Settings(), store, {source.id: source}, client)
            brief = store.briefs.save_brief(
                BriefInput(
                    name="Other",
                    queries=["hello"],
                    filters={"locations": [{"country": "RU", "city": "Москва"}]},
                )
            )
            # A condition the source cannot apply is skipped, not rejected (spec 4).
            run = engine.start(
                RunInput(brief_id=brief["id"], company_profiles=False),
                {s: engine.sources[s] for s in ["other"]},
            )
            await asyncio.gather(*engine.tasks.values())
            assert store.runs.run(run["id"])["status"] == "complete"
            assert store.vacancies.results(run["id"])[0]["title"] == "Detail"
            assert store.vacancies.results(run["id"])[0]["source"] == "other"
            assert len(calls) == 2
            assert dict(calls[0].url.params) == {"q": "hello", "page": "0"}
            assert all(r.extensions["timeout"]["read"] == 0.45 for r in calls)
            await engine.close()
    finally:
        store.close()


async def test_two_source_entries_for_same_resource_share_request_budget(tmp_path):
    from src.engine import SearchEngine
    from src.parsers.registry import build_sources

    settings = Settings(sources={"hh_ru": {"concurrency": 1, "interval_seconds": 0}})
    hh = build_sources(settings)["hh_ru"]
    alias = replace(hh, id="alias")
    active, peak = 0, 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200)

    store = Database(tmp_path / "test.sqlite")
    try:
        brief = store.briefs.save_brief(BriefInput(name="Shared resource", queries=["hello"]))
        run = store.runs.create_run(brief["id"], {"sources": ["hh_ru"]}, {})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            engine = SearchEngine(settings, store, {hh.id: hh, alias.id: alias}, client)
            await asyncio.gather(
                *(net.get(run["id"], "https://hh.ru/", "listing") for net in engine.networks.values())
            )
            assert peak == 1
            conflicting = replace(alias, settings=hh.settings.model_copy(update={"concurrency": 2}))
            with pytest.raises(ValueError, match="Conflicting policies"):
                SearchEngine(settings, store, {hh.id: hh, alias.id: conflicting}, client)
    finally:
        store.close()
