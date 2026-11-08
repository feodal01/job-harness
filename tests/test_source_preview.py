"""What source rules give before a run: counts, named rule items and search with participation."""

import pytest
from test_engine_runs import service as app_service
from test_mcp import ManagerProbe
from test_source_service import ABBYY_1, ABBYY_2, BROKEN, LEVER, ROWS, WEB, Catalog

from src.core.config import Settings
from src.core.errors import InvalidSearchError
from src.db import Database
from src.geography.catalog import default_catalog
from src.mcp.server import create_mcp
from src.models.sources import SourceRules
from src.parsers.registry import SourceRegistry
from src.services.auth import AuthError, LoginRequired
from src.services.sources import NO_LOGIN, SourceService

LOGIN = "http://127.0.0.1:8765/auth"
GONE = "00000000-0000-4000-8000-0000000000ff"


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "preview.sqlite")
    yield database
    database.close()


def sources(store, catalog=None) -> SourceService:
    async def hh(url: str) -> str:
        return '[{"id": "113", "name": "Россия"}, {"id": "40", "name": "Казахстан"}]'

    registry = SourceRegistry(Settings(), default_catalog())
    return SourceService(
        registry, store.resources, store.events, catalog if catalog is not None else Catalog(), lambda _: hh
    )


def counts(preview) -> dict[str, tuple[int, int]]:
    return {g["group"]: (g["available"], g["selected"]) for g in preview["groups"]}


# --- Preview ----------------------------------------------------------------------------------


async def test_default_rules_count_every_type_and_read_the_catalog_once(store):
    catalog = Catalog()
    preview = await sources(store, catalog).preview(SourceRules(), ["ru"])
    assert counts(preview) == {
        "aggregator": (1, 1),
        "it_board": (1, 1),
        "company_site": (2, 2),  # two catalog rows of one board count once
        "telegram": (0, 0),
    }
    assert (preview["total"], preview["excluded"], preview["items"]) == (4, 0, [])
    assert preview["countries"] == ["RU"]
    assert preview["catalog"] == {"status": "ok", "message": None, "login_url": None, "rows": len(ROWS)}
    assert catalog.calls == [{"name": None, "ids": None, "providers": None}]


async def test_a_country_the_aggregator_does_not_cover_is_not_available(store):
    preview = await sources(store).preview(SourceRules(), ["DE"])
    assert counts(preview)["aggregator"] == (0, 0)
    assert preview["total"] == 3


async def test_rule_items_are_named_with_their_status(store):
    rules = SourceRules(
        groups=["aggregator"],
        include=[f"company:{LEVER}", "getmatch"],
        exclude=[f"company:{ABBYY_1}", "ats:lever:other"],
    )
    preview = await sources(store).preview(rules, ["RU"])
    assert counts(preview)["company_site"] == (2, 1)  # available stays; selected follows the rules
    assert preview["items"] == [
        {"item": f"company:{LEVER}", "name": "Acme", "group": "company_site", "status": "included"},
        {"item": "getmatch", "name": "Getmatch", "group": "it_board", "status": "included"},
        {"item": f"company:{ABBYY_1}", "name": "ABBYY", "group": "company_site", "status": "excluded"},
        {"item": "ats:lever:other", "name": None, "group": "company_site", "status": "excluded"},
    ]
    assert preview["total"] == 3 and preview["excluded"] == 0


@pytest.mark.parametrize(
    ("item", "name", "reason"),
    [
        (f"company:{GONE}", None, "больше нет в каталоге"),
        (f"company:{WEB}", "Bank", "нет поддерживаемой доски"),
        (f"company:{BROKEN}", "Broken", "Адрес не похож"),
        ("ats:lever:gone", None, "Этой доски больше нет"),
    ],
)
async def test_included_items_that_cannot_be_searched_say_why(store, item, name, reason):
    preview = await sources(store).preview(SourceRules(mode="selected", include=["hh_ru", item]), [])
    found = next(i for i in preview["items"] if i["item"] == item)
    assert found["status"] == "missing" and found["name"] == name and reason in found["reason"]
    assert preview["total"] == 1


async def test_an_included_board_removed_by_its_company_exclusion_is_marked_excluded(store):
    rules = SourceRules(mode="selected", include=["ats:greenhouse:abbyy"], exclude=[f"company:{ABBYY_2}"])
    preview = await sources(store).preview(rules, ["RU"])
    item = preview["items"][0]
    assert item["status"] == "excluded" and item["name"] == "ABBYY"
    assert preview["total"] == 0 and preview["excluded"] == 1


async def test_nothing_left_is_zero_not_an_error(store):
    preview = await sources(store).preview(SourceRules(groups=["it_board"], exclude=["getmatch"]), ["RU"])
    assert preview["total"] == 0 and preview["excluded"] == 1
    assert preview["builtin"] == [] and preview["requires_query"] is False


@pytest.mark.parametrize(
    ("rules", "builtin", "requires_query"),
    [
        (SourceRules(), ["hh_ru", "getmatch"], False),
        (SourceRules(groups=["aggregator"]), ["hh_ru"], True),  # only HH refuses a search without queries
        (SourceRules(groups=["aggregator", "company_site"]), ["hh_ru"], False),  # boards list everything
        (SourceRules(groups=["company_site"]), [], False),
        (SourceRules(mode="selected", include=["getmatch"]), ["getmatch"], False),
    ],
)
async def test_the_preview_says_which_built_ins_search_and_whether_queries_are_needed(
    store, rules, builtin, requires_query
):
    preview = await sources(store).preview(rules, ["RU"])
    assert preview["builtin"] == builtin
    assert preview["requires_query"] is requires_query


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (LoginRequired(LOGIN), "login_required"),
        (AuthError("У этого аккаунта нет доступа к каталогу компаний.", 403), "forbidden"),
        (AuthError("Не удалось связаться с Supabase."), "unavailable"),
    ],
)
async def test_without_the_catalog_built_in_sources_still_count(store, error, status):
    catalog = Catalog(error=error)
    preview = await sources(store, catalog).preview(
        SourceRules(include=[f"company:{LEVER}"], exclude=[f"company:{ABBYY_1}"]), ["RU"]
    )
    assert counts(preview) == {
        "aggregator": (1, 1),
        "it_board": (1, 1),
        "company_site": (0, 0),
        "telegram": (0, 0),
    }
    assert preview["catalog"]["status"] == status and preview["catalog"]["rows"] is None
    assert preview["items"][0]["status"] == "missing"
    assert preview["items"][1] == {
        "item": f"company:{ABBYY_1}",
        "name": None,
        "group": "company_site",
        "status": "excluded",
    }
    assert len(catalog.calls) == 1  # one failed read, not one per question


async def test_no_catalog_at_all_is_unavailable(store):
    registry = SourceRegistry(Settings(), default_catalog())
    preview = await SourceService(registry, store.resources, store.events).preview(SourceRules(), [])
    assert preview["catalog"]["status"] == "unavailable" and preview["catalog"]["rows"] is None
    assert preview["total"] == 2


async def test_unknown_sources_in_rules_are_refused(store):
    with pytest.raises(InvalidSearchError, match="Unknown source: ats:lever"):
        await sources(store).preview(SourceRules(exclude=["ats:lever"]), [])


# --- Search -----------------------------------------------------------------------------------


async def search(store, rules=None, countries=("RU",), catalog=None, **kwargs):
    return await sources(store, catalog).search(rules or SourceRules(), countries, **kwargs)


def participation(page) -> dict[str, tuple[str, str, str | None]]:
    return {i["id"]: (i["participation"], i["participation_reason"], i["rule_item"]) for i in page["items"]}


async def test_search_by_name_says_who_takes_part_and_what_to_put_in_the_rules(store):
    page = await search(store, name="abb")
    assert participation(page) == {
        "ats:greenhouse:abbyy": ("included", "По правилам.", f"company:{ABBYY_1}"),
    }
    assert page["groups"] == [{"group": "company_site", "total": 1}]
    assert page["catalog"]["status"] == "ok"


async def test_unsupported_sources_are_found_with_the_reason_and_no_rule_item(store):
    page = await search(store, name="bank")
    assert participation(page) == {
        f"catalog:{WEB}": ("unsupported", "Общего парсера карьерных страниц пока нет", None)
    }
    telegram = await search(store, name="telegram")
    assert [i["participation"] for i in telegram["items"]] == ["unsupported"]


@pytest.mark.parametrize(
    ("rules", "countries", "expected"),
    [
        (SourceRules(exclude=[f"company:{ABBYY_2}"]), ["RU"], ("excluded", "Компания исключена вами.")),
        (SourceRules(exclude=["ats:greenhouse:abbyy"]), ["RU"], ("excluded", "Исключён вами.")),
        (SourceRules(mode="selected", include=["hh_ru"]), ["RU"], ("not_selected", "Нет в выбранных.")),
        (
            SourceRules(groups=["aggregator"]),
            ["RU"],
            ("not_selected", "Нет в выбранных компаниях."),
        ),
        (
            SourceRules(groups=["aggregator"], include=[f"company:{ABBYY_2}"]),
            ["RU"],
            ("included", "В выбранных компаниях."),
        ),
        (
            SourceRules(mode="selected", include=["ats:greenhouse:abbyy"]),
            [],
            ("included", "Добавлен вами."),
        ),
    ],
)
async def test_participation_follows_the_rules(store, rules, countries, expected):
    page = await search(store, rules, countries, name="abbyy")
    assert participation(page)["ats:greenhouse:abbyy"][:2] == expected


async def test_sources_that_do_not_cover_the_places_say_so(store):
    hh = participation(await search(store, countries=["DE"], name="HH"))["hh_ru"]
    assert hh == ("not_covered", "Не покрывает места брифа.", "hh_ru")
    types = participation(await search(store, SourceRules(groups=["company_site"]), name="getmatch"))
    assert types["getmatch"][:2] == ("not_selected", "Тип источника не выбран.")


async def test_search_pages_and_narrows_by_type(store):
    everything = await search(store, page_size=200)
    assert everything["total"] == len(everything["items"])
    first = await search(store, page=1, page_size=1)
    second = await search(store, page=2, page_size=1)
    assert first["items"][0]["id"] == everything["items"][0]["id"]
    assert second["items"][0]["id"] == everything["items"][1]["id"]
    sites = await search(store, groups=["company_site"], page_size=200)
    assert {i["group"] for i in sites["items"]} == {"company_site"}
    beyond = await search(store, page=99)
    assert beyond["items"] == [] and beyond["total"] == everything["total"]


async def test_nothing_found_is_an_empty_page(store):
    page = await search(store, name="нет такого")
    assert (page["items"], page["groups"], page["total"]) == ([], [], 0)


async def test_search_without_login_finds_built_in_sources(store):
    page = await search(store, catalog=Catalog(error=LoginRequired(LOGIN)), page_size=200)
    assert page["catalog"]["status"] == "login_required"
    assert {i["id"] for i in page["items"] if i["supported"]} == {"hh_ru", "getmatch"}


@pytest.mark.parametrize(
    "rules",
    [
        SourceRules(),
        SourceRules(groups=["aggregator"], include=[f"company:{LEVER}"]),
        SourceRules(exclude=["getmatch", f"company:{ABBYY_1}"]),
        SourceRules(mode="selected", include=["hh_ru", "ats:greenhouse:abbyy"], exclude=[f"company:{LEVER}"]),
        SourceRules(groups=["it_board", "company_site"], include=["hh_ru"], exclude=["ats:lever:acme"]),
    ],
)
@pytest.mark.parametrize("countries", [["RU"], ["DE"], []])
async def test_search_and_the_run_agree_on_who_takes_part(store, rules, countries):
    service = sources(store)
    page = await service.search(rules, countries, page_size=200)
    searched = {i["id"] for i in page["items"] if i["participation"] == "included"}
    resolved = await service.resolve_rules(rules, countries)
    assert searched == set(resolved.sources)
    preview = await service.preview(rules, countries)
    assert preview["total"] == len(resolved.sources)


# --- API and MCP ------------------------------------------------------------------------------


async def test_api_validates_the_body_and_answers_without_login(tmp_path):
    from test_board_runs import Boards, catalog

    async with app_service(tmp_path, Boards()) as (app, client):
        app.state.sources_service.catalog = catalog(error=LoginRequired(LOGIN))
        ok = await client.post("/api/v1/sources/preview", json={"countries": ["ru"]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["catalog"]["status"] == "login_required"
        assert ok.json()["catalog"]["message"] == NO_LOGIN
        for body in (
            {"countries": ["RUS"]},
            {"countries": ["RU"], "sources": ["hh_ru"]},
            {"source_rules": {"exclude": ["nope"]}},
            {"source_rules": {"mode": "selected"}},
        ):
            response = await client.post("/api/v1/sources/preview", json=body)
            assert response.status_code == 422, body
        found = await client.post(
            "/api/v1/sources/search", json={"name": "hh", "source_rules": {"groups": ["it_board"]}}
        )
        assert found.status_code == 200, found.text
        assert found.json()["items"][0]["participation"] == "not_selected"
        too_big = await client.post("/api/v1/sources/search", json={"page_size": 201})
        assert too_big.status_code == 422


async def test_mcp_preview_asks_search_only_with_a_name():
    manager = ManagerProbe()
    server = create_mcp(manager)
    await server.call_tool("preview_sources", {"countries": ["RU"]})
    assert [c[1] for c in manager.calls] == ["/api/v1/sources/preview"]
    assert manager.calls[0][2]["body"] == {
        "source_rules": SourceRules().model_dump(mode="json"),
        "countries": ["RU"],
    }
    await server.call_tool(
        "preview_sources", {"name": "Яндекс", "source_rules": {"groups": ["company_site"]}}
    )
    assert [c[1] for c in manager.calls[1:]] == ["/api/v1/sources/preview", "/api/v1/sources/search"]
    assert manager.calls[-1][2]["body"]["name"] == "Яндекс"


async def test_found_company_sites_carry_the_industries_of_every_catalog_row(store):
    from test_source_service import link

    rows = [
        link(ABBYY_1, "ABBYY", "https://job-boards.greenhouse.io/abbyy", "ats:greenhouse").model_copy(
            update={"categories": ["Корпоративное ПО"]}
        ),
        link(ABBYY_2, "ABBYY Group", "https://boards.greenhouse.io/abbyy", "ats:greenhouse").model_copy(
            update={"categories": ["Корпоративное ПО", "Искусственный интеллект"]}
        ),
        link(WEB, "Web", "https://example.org/careers", "web").model_copy(update={"categories": ["Финтех"]}),
    ]
    page = await sources(store, Catalog(rows=rows)).search(SourceRules(), ["RU"], groups=["company_site"])
    found = {item["id"]: item["categories"] for item in page["items"]}
    assert found["ats:greenhouse:abbyy"] == ["Корпоративное ПО", "Искусственный интеллект"]
    assert found[f"catalog:{WEB}"] == ["Финтех"]  # unsupported sites keep theirs too
