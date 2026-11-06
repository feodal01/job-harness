"""Sources by filters: fixed sources always, company boards from the catalog when it answers."""

import pytest

from src.core.config import Settings
from src.core.errors import InvalidSearchError
from src.db import Database
from src.geography.catalog import default_catalog
from src.parsers.registry import SourceRegistry
from src.schemas.companies import CompanyLink
from src.schemas.sources import SourceFilter
from src.services.auth import AuthError, LoginRequired
from src.services.sources import NO_LOGIN, SourceService

LOGIN = "http://127.0.0.1:8765/auth"
ABBYY_1 = "00000000-0000-4000-8000-000000000001"
ABBYY_2 = "00000000-0000-4000-8000-000000000002"
LEVER = "00000000-0000-4000-8000-000000000003"
WEB = "00000000-0000-4000-8000-000000000004"
BROKEN = "00000000-0000-4000-8000-000000000005"


def link(id: str, name: str, url: str, provider: str) -> CompanyLink:
    return CompanyLink(
        id=id,
        name=name,
        url=url,
        link_kind="ats_board",
        provider=provider,
        categories=[],
        specialization=None,
    )


ROWS = [
    link(ABBYY_1, "ABBYY", "https://job-boards.greenhouse.io/abbyy", "ats:greenhouse"),
    link(ABBYY_2, "ABBYY Group", "https://boards.greenhouse.io/abbyy", "ats:greenhouse"),  # same board
    link(LEVER, "Acme", "https://jobs.lever.co/acme", "ats:lever"),
    link(WEB, "Bank", "https://bank.example/careers", "web"),
    link(BROKEN, "Broken", "https://example.com/not-a-board", "ats:ashby"),
]


class Catalog:
    """The catalog in Supabase: filters like PostgREST, or fails like Supabase without a session."""

    def __init__(self, rows=ROWS, error: Exception | None = None) -> None:
        self.rows, self.error = rows, error
        self.calls: list[dict] = []

    async def __call__(self, *, name=None, ids=None, providers=None) -> list[CompanyLink]:
        self.calls.append({"name": name, "ids": ids, "providers": providers})
        if self.error:
            raise self.error
        result = self.rows
        if name:
            result = [r for r in result if name.casefold() in r.name.casefold()]
        if ids is not None:
            result = [r for r in result if str(r.id) in set(ids)]
        if providers is not None:
            result = [r for r in result if r.provider in set(providers)]
        return result


@pytest.fixture(scope="module")
def registry() -> SourceRegistry:
    return SourceRegistry(Settings(), default_catalog())


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "sources.sqlite")
    yield database
    database.close()


def service(registry, store, catalog=None, fetch=None) -> SourceService:
    return SourceService(registry, store.resources, store.events, catalog, fetch)


async def ids(service: SourceService, **filters) -> list[str]:
    page = await service.list_sources(SourceFilter(**filters), 1, 200)
    return [item["id"] for item in page["items"]]


async def test_without_login_fixed_sources_work_and_the_answer_says_why_boards_are_missing(registry, store):
    sources = service(registry, store, Catalog(error=LoginRequired(LOGIN)))
    page = await sources.list_sources(SourceFilter(), 1, 50)
    supported = [item["id"] for item in page["items"] if item["supported"]]
    assert supported == ["hh_ru", "getmatch"]
    assert page["catalog"] == {"status": "login_required", "message": NO_LOGIN, "login_url": LOGIN}
    counts = await sources.counts(SourceFilter())
    assert counts["supported"] == 2
    assert next(g for g in counts["groups"] if g["group"] == "company_site") == {
        "group": "company_site",
        "supported": 0,
        "unsupported": 0,
    }


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AuthError("У этого аккаунта нет доступа к каталогу компаний.", 403), "forbidden"),
        (AuthError("Не удалось связаться с Supabase."), "unavailable"),
    ],
)
async def test_catalog_failures_never_break_the_answer(registry, store, error, status):
    page = await service(registry, store, Catalog(error=error)).list_sources(SourceFilter(), 1, 50)
    assert page["catalog"]["status"] == status and page["catalog"]["message"] == str(error)
    assert {"hh_ru", "getmatch"} <= {item["id"] for item in page["items"]}


async def test_no_catalog_at_all_is_unavailable(registry, store):
    page = await service(registry, store).list_sources(SourceFilter(groups=["company_site"]), 1, 50)
    assert page["items"] == [] and page["catalog"]["status"] == "unavailable"


async def test_boards_are_merged_by_board_and_rows_that_cannot_be_searched_say_why(registry, store):
    sources = service(registry, store, Catalog())
    page = await sources.list_sources(SourceFilter(groups=["company_site"]), 1, 50)
    items = {item["id"]: item for item in page["items"]}
    abbyy = items["ats:greenhouse:abbyy"]
    assert abbyy["supported"] and sorted(abbyy["company_ids"]) == [ABBYY_1, ABBYY_2]
    assert abbyy["kind_name"] == "Greenhouse" and abbyy["group"] == "company_site"
    assert abbyy["coverage"] == {"countries": None, "known": False}
    assert abbyy["capabilities"]["query_parameters"]["salary"] == "none"
    assert items[f"catalog:{WEB}"]["supported"] is False
    assert "Общего парсера" in items[f"catalog:{WEB}"]["reason"]
    assert "не похож на доску Ashby" in items[f"catalog:{BROKEN}"]["reason"]
    assert items[f"catalog:{BROKEN}"]["capabilities"]["query_parameters"]  # the platform's, for the row
    assert page["catalog"]["status"] == "ok"
    resolved = await sources.resolve(SourceFilter(groups=["company_site"]))
    assert resolved.sources["ats:greenhouse:abbyy"].company_ids in {(ABBYY_1, ABBYY_2), (ABBYY_2, ABBYY_1)}


async def test_groups_without_company_sites_do_not_touch_the_catalog(registry, store):
    catalog = Catalog()
    sources = service(registry, store, catalog)
    assert await ids(sources, groups=["aggregator", "it_board"], status="supported") == ["hh_ru", "getmatch"]
    assert catalog.calls == []
    page = await sources.list_sources(SourceFilter(groups=["aggregator"]), 1, 50)
    assert page["catalog"]["status"] == "not_requested"


async def test_supports_filter_is_decided_by_kind_before_asking_the_catalog(registry, store):
    catalog = Catalog()
    sources = service(registry, store, catalog)
    found = await ids(sources, supports=["salary"])
    assert "ats:greenhouse:abbyy" not in found and "ats:lever:acme" not in found
    asked = set(catalog.calls[0]["providers"])
    assert "ats:greenhouse" not in asked and "web" not in asked  # unsupported kinds are not requested
    assert all(registry.capabilities[p].query_parameters["salary"] != "none" for p in asked)
    # A condition nobody checks selects nothing, and the catalog is not asked.
    catalog.calls.clear()
    assert await ids(sources, supports=["no_such_condition"]) == []
    assert catalog.calls == []


async def test_countries_keep_sources_that_cover_them_or_whose_coverage_is_unknown(registry, store):
    fresh = SourceRegistry(Settings(), default_catalog())

    async def hh(url: str) -> str:
        return '[{"id": "113", "name": "Россия"}, {"id": "40", "name": "Казахстан"}]'

    sources = service(fresh, store, Catalog(), fetch=lambda source: hh)
    germany = await ids(sources, countries=["de"], status="supported")
    assert "hh_ru" not in germany  # HH does not cover Germany
    assert "getmatch" in germany  # the world, through relocation
    assert "ats:lever:acme" in germany  # unknown coverage takes part
    assert "hh_ru" in await ids(sources, countries=["RU", "DE"])
    hh_item = next(
        i for i in (await sources.list_sources(SourceFilter(), 1, 50))["items"] if i["id"] == "hh_ru"
    )
    assert hh_item["coverage"] == {"countries": ["KZ", "RU"], "known": True}


async def test_a_failing_country_lookup_does_not_hide_the_source(registry, store):
    fresh = SourceRegistry(Settings(), default_catalog())

    async def down(url: str) -> str:
        raise RuntimeError("HH is down")

    sources = service(fresh, store, Catalog(), fetch=lambda source: down)
    assert "hh_ru" in await ids(sources, countries=["DE"])


async def test_plain_lists_do_not_look_up_countries(registry, store):
    calls = []

    async def hh(url: str) -> str:
        calls.append(url)
        return "[]"

    await service(
        SourceRegistry(Settings(), default_catalog()), store, Catalog(), fetch=lambda s: hh
    ).list_sources(SourceFilter(), 1, 50)
    assert calls == []


async def test_selected_companies_name_kind_and_status_filters(registry, store):
    catalog = Catalog()
    sources = service(registry, store, catalog)
    assert await ids(sources, company_ids=[ABBYY_2]) == ["ats:greenhouse:abbyy"]  # no fixed sources
    assert catalog.calls[-1]["ids"] == [ABBYY_2]
    assert await ids(sources, name="acm") == ["ats:lever:acme"]
    assert await ids(sources, name="hh") == ["hh_ru"]
    assert await ids(sources, kinds=["ats:lever"]) == ["ats:lever:acme"]
    unsupported = await ids(sources, status="unsupported")
    assert "hh_ru" not in unsupported and "telegram" in unsupported and f"catalog:{WEB}" in unsupported
    assert await ids(sources, company_ids=["00000000-0000-4000-8000-000000000099"]) == []


async def test_hh_employer_pages_in_the_catalog_are_listed_as_not_connected(registry, store):
    rows = [link(WEB, "Employer", "https://hh.ru/employer/1", "hh_ru")]
    sources = service(registry, store, Catalog(rows))
    items = (await sources.list_sources(SourceFilter(groups=["company_site"]), 1, 50))["items"]
    assert [(i["kind"], i["supported"]) for i in items] == [("hh_employer", False)]


async def test_paging_and_order(registry, store):
    sources = service(registry, store, Catalog())
    everything = await ids(sources)
    first = (await sources.list_sources(SourceFilter(), 1, 3))["items"]
    second = (await sources.list_sources(SourceFilter(), 2, 3))["items"]
    assert [i["id"] for i in first + second] == everything[:6]
    assert everything[:2] == ["hh_ru", "linkedin"]  # aggregators first, supported before unsupported
    assert (await sources.list_sources(SourceFilter(), 99, 3))["items"] == []


async def test_resolve_ids_for_a_run(registry, store):
    catalog = Catalog()
    sources = service(registry, store, catalog)
    fixed = await sources.resolve_ids(["hh_ru", "hh_ru"])
    assert list(fixed.sources) == ["hh_ru"] and catalog.calls == []  # fixed only: catalog untouched
    with pytest.raises(InvalidSearchError, match="Unknown source: nope, ats:manatal:acme"):
        await sources.resolve_ids(["nope", "ats:manatal:acme"])
    mixed = await sources.resolve_ids(["getmatch", "ats:lever:acme", "ats:greenhouse:gone"])
    assert list(mixed.sources) == ["getmatch", "ats:lever:acme"]
    assert mixed.skipped == {"ats:greenhouse:gone": "Этой доски больше нет в каталоге компаний."}
    assert set(catalog.calls[-1]["providers"]) == {"ats:lever", "ats:greenhouse"}


async def test_resolve_ids_without_login_leaves_boards_out_with_the_reason(registry, store):
    sources = service(registry, store, Catalog(error=LoginRequired(LOGIN)))
    resolved = await sources.resolve_ids(["hh_ru", "ats:lever:acme"])
    assert list(resolved.sources) == ["hh_ru"]
    assert resolved.skipped == {"ats:lever:acme": NO_LOGIN}
    assert resolved.catalog.status == "login_required"


async def test_every_catalog_row_is_a_company_site_even_on_a_board_platform_or_unknown_provider(
    registry, store
):
    rows = [
        link(WEB, "Habr page", "https://career.habr.com/companies/acme", "habr_career"),
        link(BROKEN, "Future", "https://example.com/jobs", "ats:brand_new"),
    ]
    catalog = Catalog(rows)
    sources = service(registry, store, catalog)
    items = (await sources.list_sources(SourceFilter(groups=["company_site"]), 1, 50))["items"]
    assert [(i["id"], i["kind"], i["group"], i["supported"]) for i in items] == [
        (f"catalog:{BROKEN}", "ats:brand_new", "company_site", False),
        (f"catalog:{WEB}", "habr_career", "company_site", False),
    ]
    assert catalog.calls[-1]["providers"] is None  # unfiltered: new providers show up too
    catalog.calls.clear()
    await sources.list_sources(SourceFilter(groups=["it_board"]), 1, 50)
    assert catalog.calls == []
