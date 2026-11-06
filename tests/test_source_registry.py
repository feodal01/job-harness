"""The one registry of source kinds: fixed sources, board platforms and kinds not supported yet."""

import json

import pytest

from src.core.config import Settings
from src.geography.catalog import default_catalog
from src.network.client import FetchError
from src.parsers.ats.models import Board
from src.parsers.models import Place
from src.parsers.registry import (
    KINDS,
    UNKNOWN,
    WORLD,
    Coverage,
    SourceKind,
    SourceRegistry,
    Unsupported,
)

COMPANY = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(scope="module")
def registry() -> SourceRegistry:
    return SourceRegistry(Settings(), default_catalog())


def test_every_kind_is_listed_once_and_every_platform_folder_is_registered(registry):
    from pathlib import Path

    keys = [kind.key for kind in KINDS]
    assert len(keys) == len(set(keys))
    folders = {
        p.name
        for p in (Path(__file__).parents[1] / "src/parsers/ats").iterdir()
        if (p / "parser.py").exists()
    }
    boards = {kind.key.removeprefix("ats:") for kind in KINDS if kind.board is not None}
    assert boards == folders
    assert set(registry.fixed) == {"hh_ru", "getmatch"}
    assert {registry.fixed[k].group for k in registry.fixed} == {"aggregator", "it_board"}


def test_capabilities_are_known_for_every_supported_kind_without_a_catalog(registry):
    supported = {kind.key for kind in KINDS if kind.supported}
    assert set(registry.capabilities) == supported
    assert registry.capabilities["ats:greenhouse"].query_parameters["salary"] == "none"
    assert "planned" not in registry.capabilities


def test_duplicate_kinds_and_a_fixed_builder_with_a_foreign_id_are_refused():
    hh = next(kind for kind in KINDS if kind.key == "hh_ru")
    with pytest.raises(ValueError, match="Duplicate source kinds: hh_ru"):
        SourceRegistry(Settings(), kinds=(hh, hh))
    wrong = SourceKind("other", "Other", "aggregator", fixed=hh.fixed)
    with pytest.raises(ValueError, match="other builds hh_ru"):
        SourceRegistry(Settings(), kinds=(wrong,))
    no_example = SourceKind("ats:lever", "Lever", "company_site", board=lambda board: None)  # type: ignore[arg-type,return-value]
    with pytest.raises(ValueError, match="needs an example"):
        SourceRegistry(Settings(), kinds=(no_example,))


@pytest.mark.parametrize(
    ("source_id", "kind"),
    [
        ("hh_ru", "hh_ru"),
        ("ats:greenhouse:abbyy", "ats:greenhouse"),
        ("ats:workday:acme.wd5.myworkdayjobs.com:acme:Careers", "ats:workday"),  # keys with colons
        ("ats:join:companies/acme", "ats:join"),  # keys with slashes
        ("ats:manatal:acme", None),  # a planned platform has no boards
        ("ats:unknown:acme", None),
        ("habr_career", "habr_career"),
        ("", None),
    ],
)
def test_kind_of_source_ids(registry, source_id, kind):
    found = registry.kind_of(source_id)
    assert (found.key if found else None) == kind


def test_a_catalog_row_becomes_a_board_of_its_company(registry):
    board = registry.board(" ATS:Greenhouse ", " https://job-boards.greenhouse.io/abbyy ", " ABBYY ", COMPANY)
    assert not isinstance(board, Unsupported)
    assert board.id == "ats:greenhouse:abbyy"
    assert board.name == "ABBYY"
    assert board.group == "company_site" and board.company_ids == (COMPANY,)
    # Each board has its own pacing and breaker even on a shared API host.
    assert board.resource == "boards-api.greenhouse.io/abbyy"


@pytest.mark.parametrize(
    ("provider", "url", "company", "reason"),
    [
        ("web", "https://example.com/careers", "Acme", "Общего парсера"),
        ("ats:manatal", "https://careers.manatal.com/acme", "Acme", "Парсера пока нет"),
        ("smoke_signals", "https://example.com", "Acme", "не поддерживается"),
        ("hh_ru", "https://hh.ru/employer/1", "Acme", "через HH"),
        ("ats:greenhouse", "https://example.com/abbyy", "Acme", "не похож на доску Greenhouse"),
        ("ats:greenhouse", "http://job-boards.greenhouse.io/abbyy", "Acme", "не похож"),
        ("ats:lever", "https://localhost/acme", "Acme", "не похож"),
        ("ats:lever", "https://10.0.0.1/acme", "Acme", "не похож"),
        ("ats:lever", "https://user:secret@jobs.lever.co/acme", "Acme", "не похож"),
        ("ats:lever", "https://jobs.lever.co/acme", "   ", "не похож"),
        ("ats:lever", "", "Acme", "не похож"),
    ],
)
def test_rows_that_cannot_be_searched_say_why_instead_of_failing(registry, provider, url, company, reason):
    result = registry.board(provider, url, company, COMPANY)
    assert isinstance(result, Unsupported)
    assert reason in result.reason


def test_unsupported_parameters_follow_the_kind_and_are_unknown_for_gone_sources(registry):
    assert "salary" in registry.unsupported_parameters("ats:lever:acme")
    assert registry.unsupported_parameters("ats:lever:acme") == registry.unsupported_parameters(
        "ats:lever:other"
    )
    assert registry.unsupported_parameters("removed_source") is None
    assert registry.unsupported_parameters("habr_career") is None


async def test_coverage_is_static_looked_up_cached_or_unknown(registry):
    kinds = registry.kinds
    assert await registry.coverage(kinds["getmatch"], None) == WORLD
    assert await registry.coverage(kinds["ats:lever"], None) == UNKNOWN
    assert WORLD.covers(frozenset({"DE"})) and UNKNOWN.covers(frozenset({"DE"}))
    assert Coverage(frozenset({"RU"}), True).covers(frozenset())  # no countries asked: everything fits
    assert not Coverage(frozenset({"RU"}), True).covers(frozenset({"DE"}))

    fresh = SourceRegistry(Settings(), default_catalog())
    calls = []

    async def failing(url: str) -> str:
        calls.append(url)
        raise FetchError("http_error", "down")

    # Without a lookup nothing is requested; a failed lookup is unknown and remembered for a while.
    assert await fresh.coverage(fresh.kinds["hh_ru"], failing, lookup=False) == UNKNOWN
    assert calls == []
    assert await fresh.coverage(fresh.kinds["hh_ru"], failing) == UNKNOWN
    assert await fresh.coverage(fresh.kinds["hh_ru"], failing) == UNKNOWN
    assert len(calls) == 1

    answered = SourceRegistry(Settings(), default_catalog())

    async def countries(url: str) -> str:
        assert url.startswith("https://api.hh.ru/areas/countries")
        return json.dumps(
            [
                {"id": "113", "name": "Россия"},
                {"id": "40", "name": "Казахстан"},
                {"id": "9", "name": "Атлантида"},
            ]
        )

    coverage = await answered.coverage(answered.kinds["hh_ru"], countries)
    assert coverage == Coverage(frozenset({"RU", "KZ"}), known=True)  # unknown names are not guessed
    assert await answered.coverage(answered.kinds["hh_ru"], None, lookup=False) == coverage


def test_examples_are_boards_of_their_platform():
    for kind in KINDS:
        if kind.board is not None:
            assert kind.board(Board(kind.example, kind.name)).id.startswith(kind.key + ":")


def test_place_model_is_what_hh_countries_return():
    assert Place(id="113", name="Россия").name == "Россия"
