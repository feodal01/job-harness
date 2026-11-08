"""Source rules in the brief, resolved at run start (docs/brief-parameters/source-rules.md)."""

import asyncio

import pytest
from pydantic import ValidationError
from test_board_runs import AIRSLATE, HIDDEN, OTHER, Boards, catalog, fast_boards
from test_engine_runs import brief, service
from test_source_service import ABBYY_1, ABBYY_2, BROKEN, LEVER, WEB, Catalog

from src.core.config import Settings
from src.core.errors import InvalidSearchError
from src.db import Database
from src.geography.catalog import default_catalog
from src.models.brief import SearchOptions
from src.models.run import RunInput
from src.models.sources import MAX_RULE_ITEMS, SourceRules
from src.parsers.registry import SourceRegistry
from src.services.auth import AuthError, LoginRequired
from src.services.sources import CATALOG_SKIP, NO_LOGIN, SourceService

LOGIN = "http://127.0.0.1:8765/auth"
GONE = "00000000-0000-4000-8000-0000000000ff"


# --- The model --------------------------------------------------------------------------------


def test_defaults_are_every_source_by_the_brief_places():
    rules = SourceRules()
    assert (rules.mode, rules.groups, rules.include, rules.exclude) == ("auto", None, [], [])
    assert SearchOptions().source_rules is None


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"mode": "selected"}, "at least one source"),  # case 6
        ({"groups": []}, "at least one source"),  # case 5
        ({"mode": "selected", "groups": ["aggregator"], "include": ["hh_ru"]}, "mode=auto only"),  # 7
        ({"include": ["hh_ru"], "exclude": ["hh_ru"]}, "Both included and excluded: hh_ru"),  # 8
        ({"include": ["company:not-a-uuid"]}, "catalog UUID"),  # 9
        ({"include": ["company:"]}, "catalog UUID"),
        ({"include": [""]}, "without spaces"),
        ({"include": ["  "]}, "without spaces"),
        ({"include": ["ats:lever:a b"]}, "without spaces"),
        ({"include": ["x" * 501]}, "without spaces"),
        ({"include": ["hh_ru"] * (MAX_RULE_ITEMS + 1)}, "at most 1000"),  # case 21
        ({"groups": ["newspapers"]}, "groups"),
        ({"mode": "manual"}, "mode"),
        ({"sources": ["hh_ru"]}, "Extra inputs"),
    ],
)
def test_rules_that_cannot_be_searched_are_refused(rules, message):
    with pytest.raises(ValidationError, match=message):
        SourceRules.model_validate(rules)


def test_items_are_trimmed_deduplicated_and_company_ids_normalized():
    rules = SourceRules(
        include=[" hh_ru ", "hh_ru", f"company:{ABBYY_1.upper()}", f"company:{ABBYY_1}"],
        groups=["aggregator", "aggregator"],
    )
    assert rules.include == ["hh_ru", f"company:{ABBYY_1}"]  # case 22
    assert rules.groups == ["aggregator"]
    # An empty group list is fine when something is included explicitly.
    assert SourceRules(groups=[], include=["getmatch"]).groups == []


def test_the_old_source_list_is_refused_in_briefs_and_runs():  # case 19
    with pytest.raises(ValidationError, match="Extra inputs"):
        SearchOptions.model_validate({"sources": ["hh_ru"]})
    with pytest.raises(ValidationError, match="Extra inputs"):
        RunInput.model_validate({"brief_id": "b", "sources": ["hh_ru"]})


# --- Resolution -------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "rules.sqlite")
    yield database
    database.close()


def sources(store, rows_catalog=None, countries=("Россия", "Казахстан")) -> SourceService:
    registry = SourceRegistry(Settings(), default_catalog())

    async def hh(url: str) -> str:
        return str([{"id": str(i), "name": n} for i, n in enumerate(countries)]).replace("'", '"')

    return SourceService(
        registry, store.resources, store.events, rows_catalog or Catalog(), lambda source: hh
    )


async def resolve(service: SourceService, countries=("RU",), **rules):
    return await service.resolve_rules(SourceRules(**rules), countries)


async def test_auto_takes_every_supported_source_covering_the_places(store):  # case 1
    resolved = await resolve(sources(store))
    assert list(resolved.sources) == ["hh_ru", "getmatch", "ats:greenhouse:abbyy", "ats:lever:acme"]
    assert resolved.countries == ["RU"] and resolved.catalog.status == "ok"
    assert resolved.skipped == {} and resolved.excluded == []
    # The merged board keeps every company it belongs to.
    assert resolved.sources["ats:greenhouse:abbyy"].company_ids == (ABBYY_1, ABBYY_2)


async def test_a_place_the_aggregator_does_not_cover_leaves_it_out(store):  # case 2
    resolved = await resolve(sources(store), countries=("de",))
    assert "hh_ru" not in resolved.sources
    assert {"getmatch", "ats:lever:acme"} <= set(resolved.sources)
    assert resolved.countries == ["DE"]


async def test_an_unknown_country_list_keeps_the_source(store):  # case 3
    registry = SourceRegistry(Settings(), default_catalog())

    async def down(url: str) -> str:
        raise RuntimeError("HH is down")

    service = SourceService(registry, store.resources, store.events, Catalog(), lambda source: down)
    assert "hh_ru" in (await resolve(service, countries=("DE",))).sources


async def test_no_places_means_no_country_filter(store):
    resolved = await resolve(sources(store, countries=()), countries=())
    assert "hh_ru" in resolved.sources and resolved.countries == []


async def test_groups_narrow_the_types_and_skip_the_catalog(store):  # case 4
    rows = Catalog()
    resolved = await resolve(sources(store, rows), groups=["aggregator"])
    assert list(resolved.sources) == ["hh_ru"]
    assert rows.calls == [] and resolved.catalog.status == "not_requested"


async def test_included_company_brings_every_supported_board_it_has(store):  # case 11
    two_boards = Catalog(
        [
            *Catalog().rows,
            Catalog().rows[2].model_copy(update={"url": "https://jobs.lever.co/acme-eu"}),
        ]
    )
    resolved = await resolve(
        sources(store, two_boards), mode="selected", include=["hh_ru", f"company:{LEVER}"]
    )
    assert list(resolved.sources) == ["hh_ru", "ats:lever:acme", "ats:lever:acme-eu"]
    assert two_boards.calls[-1]["ids"] == [LEVER]


@pytest.mark.parametrize(
    ("company", "reason"),
    [
        (GONE, "больше нет в каталоге"),  # case 12
        (WEB, "нет поддерживаемой доски: Общего парсера"),  # case 13
        (BROKEN, "нет поддерживаемой доски: Адрес не похож"),
    ],
)
async def test_included_companies_without_a_board_are_skipped_with_the_reason(store, company, reason):
    resolved = await resolve(sources(store), mode="selected", include=["getmatch", f"company:{company}"])
    assert list(resolved.sources) == ["getmatch"]
    assert reason in resolved.skipped[f"company:{company}"]


async def test_excluded_sources_and_companies_leave_after_the_rules(store):  # case 14
    resolved = await resolve(sources(store), exclude=["getmatch", f"company:{ABBYY_2}"])
    assert list(resolved.sources) == ["hh_ru", "ats:lever:acme"]
    # A board shared by two companies leaves when either is excluded.
    assert resolved.excluded == ["getmatch", "ats:greenhouse:abbyy"]


async def test_an_excluded_company_removes_a_board_it_shares_with_an_included_one(store):
    resolved = await resolve(
        sources(store), groups=["aggregator"], include=[f"company:{ABBYY_1}"], exclude=[f"company:{ABBYY_2}"]
    )
    assert list(resolved.sources) == ["hh_ru"] and resolved.excluded == ["ats:greenhouse:abbyy"]


async def test_an_excluded_board_that_is_not_in_the_result_changes_nothing(store):
    resolved = await resolve(sources(store), groups=["aggregator"], exclude=["ats:lever:acme"])
    assert list(resolved.sources) == ["hh_ru"] and resolved.excluded == []


async def test_without_login_auto_still_searches_and_says_why_company_sites_are_missing(store):  # 15
    resolved = await resolve(sources(store, Catalog(error=LoginRequired(LOGIN))))
    assert list(resolved.sources) == ["hh_ru", "getmatch"]
    assert resolved.catalog.status == "login_required"
    assert resolved.skipped == {CATALOG_SKIP: NO_LOGIN}


async def test_without_login_selected_companies_are_skipped_with_the_reason(store):  # case 16
    resolved = await resolve(
        sources(store, Catalog(error=LoginRequired(LOGIN))),
        mode="selected",
        include=[f"company:{LEVER}", "ats:lever:acme"],
    )
    assert resolved.sources == {}
    assert resolved.skipped == {f"company:{LEVER}": NO_LOGIN, "ats:lever:acme": NO_LOGIN}


async def test_a_catalog_failure_wins_over_ok_in_the_run_summary(store):
    failing = Catalog(error=AuthError("Не удалось связаться с Supabase."))
    resolved = await resolve(sources(store, failing), include=[f"company:{LEVER}"])
    assert resolved.catalog.status == "unavailable"
    assert resolved.skipped[CATALOG_SKIP] == "Не удалось связаться с Supabase."


@pytest.mark.parametrize("item", ["nope", "ats:lever", "ats:manatal:acme", "habr_career", "web"])
async def test_unknown_sources_in_rules_are_refused(store, item):  # case 10
    service = sources(store)
    for rules in ({"include": [item]}, {"exclude": [item]}):
        with pytest.raises(InvalidSearchError, match=f"Unknown source: {item}"):
            await service.resolve_rules(SourceRules(**rules), ["RU"])


# --- Briefs and runs through the API ----------------------------------------------------------


def selected(*ids: str) -> dict:
    return {"mode": "selected", "include": list(ids)}


async def start(app, client, body):
    response = await client.post("/api/v1/runs", json=body)
    assert response.status_code == 202, response.text
    await asyncio.gather(*app.state.engine.tasks.values())
    return (await client.get(f"/api/v1/runs/{response.json()['id']}")).json()


async def test_saving_a_brief_checks_rule_items_without_asking_the_catalog(tmp_path):
    async with service(tmp_path, Boards()) as (app, client):
        app.state.sources_service.catalog = catalog(error=AssertionError("saving must not read"))
        b = await brief(client, ["QA"])
        body = {"name": "B", "queries": ["QA"], "filters": b["filters"]}
        for rules, status in [
            ({"include": ["ats:nope:acme"]}, 422),
            ({"exclude": ["ats:lever"]}, 422),
            (selected("company:" + GONE), 200),  # companies are checked at run start
            ({"exclude": ["ats:lever:anything"]}, 200),
        ]:
            response = await client.put(
                f"/api/v1/briefs/{b['id']}", json={**body, "search": {"source_rules": rules}}
            )
            assert response.status_code == status, (rules, response.text)


async def test_the_run_uses_the_brief_rules_and_keeps_the_whole_brief(tmp_path):
    fake = Boards()
    async with service(tmp_path, fake) as (app, client):
        app.state.sources_service.catalog = catalog()
        fast_boards(app)
        b = await brief(client, ["QA"])
        rules = {"exclude": ["getmatch", f"company:{OTHER}"]}
        saved = await client.put(
            f"/api/v1/briefs/{b['id']}",
            json={
                "name": "Бриф",
                "queries": ["QA"],
                "filters": b["filters"],
                "search": {"source_rules": rules},
            },
        )
        assert saved.status_code == 200, saved.text
        result = await start(app, client, {"brief_id": b["id"], "company_profiles": False})
        request = result["request"]
        assert request["sources"] == ["hh_ru", "ats:lever:airslate", "ats:lever:hidden"]
        assert request["source_rules"] == {"mode": "auto", "groups": None, **rules, "include": []}
        assert request["resolution"] == {
            "countries": ["RU"],
            "catalog": {"status": "ok"},
            "matched": 5,
            "excluded": ["getmatch", "ats:lever:other"],
        }
        # Every brief parameter the run searched with, as it was then.
        assert request["brief"]["name"] == "Бриф" and request["brief"]["queries"] == ["QA"]
        assert request["brief"]["filters"]["locations"] == b["filters"]["locations"]
        assert request["brief"]["search"]["source_rules"]["exclude"] == rules["exclude"]
        # A later edit does not change what the run remembers.
        await client.put(
            f"/api/v1/briefs/{b['id']}", json={"name": "Другой", "queries": ["AQA"], "filters": b["filters"]}
        )
        again = (await client.get(f"/api/v1/runs/{result['id']}")).json()
        assert again["request"]["brief"]["name"] == "Бриф"
        assert fake.lever and not any(path.endswith("/other") for path in fake.lever)
        assert AIRSLATE and HIDDEN  # catalog rows used by this test


async def test_run_rules_replace_the_brief_rules_for_that_run(tmp_path):  # case 18
    async with service(tmp_path, Boards()) as (app, client):
        app.state.sources_service.catalog = catalog(error=AssertionError("selected HH needs no catalog"))
        b = await brief(client, ["QA"])
        result = await start(
            app, client, {"brief_id": b["id"], "source_rules": selected("hh_ru"), "company_profiles": False}
        )
        assert result["request"]["sources"] == ["hh_ru"]
        assert result["request"]["source_rules"]["include"] == ["hh_ru"]
        assert result["request"]["resolution"]["catalog"] == {"status": "not_requested"}


async def test_without_login_the_default_run_searches_and_shows_company_sites_skipped(tmp_path):
    async with service(tmp_path, Boards()) as (app, client):
        app.state.sources_service.catalog = catalog(error=LoginRequired(LOGIN))
        b = await brief(client, ["QA"])
        result = await start(app, client, {"brief_id": b["id"], "company_profiles": False})
        assert result["sources"]["hh_ru"]["status"] == "complete"
        assert result["sources"][CATALOG_SKIP] == {**result["sources"][CATALOG_SKIP], "status": "skipped"}
        assert result["sources"][CATALOG_SKIP]["error"] == NO_LOGIN
        assert result["request"]["resolution"]["catalog"] == {"status": "login_required", "message": NO_LOGIN}


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"exclude": ["hh_ru", "getmatch"], "groups": ["aggregator", "it_board"]}, "ни одного источника"),
        (selected(f"company:{GONE}"), "больше нет в каталоге"),
        ({"include": ["ats:lever:unknown"]}, None),  # the unknown board is skipped, the others run
    ],
)
async def test_a_run_with_nothing_left_to_search_is_refused_with_the_reason(tmp_path, rules, message):  # 17
    async with service(tmp_path, Boards()) as (app, client):
        app.state.sources_service.catalog = catalog()
        fast_boards(app)
        b = await brief(client, ["QA"])
        response = await client.post(
            "/api/v1/runs", json={"brief_id": b["id"], "source_rules": rules, "company_profiles": False}
        )
        if message is None:
            assert response.status_code == 202, response.text
            await asyncio.gather(*app.state.engine.tasks.values())
            run = (await client.get(f"/api/v1/runs/{response.json()['id']}")).json()
            assert run["sources"]["ats:lever:unknown"]["status"] == "skipped"
        else:
            assert response.status_code == 422 and message in response.text, response.text
            assert (await client.get("/api/v1/runs")).json() == []


async def test_the_old_source_list_in_a_run_request_is_refused(tmp_path):  # case 19
    async with service(tmp_path, Boards()) as (app, client):
        b = await brief(client, ["QA"])
        response = await client.post("/api/v1/runs", json={"brief_id": b["id"], "sources": ["hh_ru"]})
        assert response.status_code == 422 and "sources" in response.text


async def test_without_queries_boards_list_their_whole_feed_and_only_hh_is_skipped(tmp_path):
    """HH refuses a search without queries; a board with a keyword search lists the whole board."""
    async with service(tmp_path, Boards()) as (app, client):
        engine = app.state.engine
        board = app.state.sources_service.registry.board(
            "ats:greenhouse", "https://boards.greenhouse.io/secretco", "Secret Co", GONE
        )
        assert board.search.parameters["query"] == "native" and not board.requires_query
        assert engine.sources["hh_ru"].requires_query
        b = await brief(client, [])
        run = engine.start(
            RunInput(brief_id=b["id"], company_profiles=False),
            {"hh_ru": engine.sources["hh_ru"], board.id: board},
        )
        for task in engine.tasks.values():
            task.cancel()
        await asyncio.gather(*engine.tasks.values(), return_exceptions=True)
        stored = app.state.database.runs.run(run["id"])
        assert stored["request"]["sources"] == [board.id]
        assert stored["request"]["skipped"] == {"hh_ru": "HH requires at least one search query"}
        assert "Secret Co" not in str(stored)  # catalog names stay in Supabase
