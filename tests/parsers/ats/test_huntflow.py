"""Real saved huntflow responses; expected vacancy facts reviewed from the source bodies."""

import json
from pathlib import Path

import httpx
import pytest

from src.core.config import Settings
from src.db import Database
from src.engine import SearchEngine
from src.parsers.ats.huntflow.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "huntflow"
SAMPLES = [("apicworld", 2, "senior-php-developer-1", "Senior PHP Developer")]


def fixture(company):
    directory = FIXTURES / company
    meta = json.loads((directory / "meta.json").read_text())
    return meta["captured_url"], (directory / "response.html").read_text()


@pytest.mark.parametrize("company,count,identifier,title", SAMPLES)
def test_saved_board(company, count, identifier, title):
    url, body = fixture(company)
    source = create_adapter(Board(url, company))
    page = source.search.parse(body, source.search.build("QA"))
    assert len(page.vacancies) == count
    assert page.vacancies[0].id == identifier
    assert page.vacancies[0].title == title
    assert page.vacancies[0].url.startswith("https://")
    assert source.search.parameters["query"] == "ignored"
    assert source.type == "ats"


@pytest.mark.parametrize("company,count,identifier,title", SAMPLES)
def test_company_label_does_not_change_vacancy_facts(company, count, identifier, title):
    url, body = fixture(company)
    original = create_adapter(Board(url, company))
    renamed = create_adapter(Board(url, "Unrelated Employer"))
    a = original.search.parse(body, original.search.build(""))
    b = renamed.search.parse(body, renamed.search.build(""))
    assert original.id == renamed.id
    assert [v.model_dump(exclude={"company"}) for v in a.vacancies] == [
        v.model_dump(exclude={"company"}) for v in b.vacancies
    ]


@pytest.mark.parametrize("body", ["garbage", "{}", "<html><h1>Access denied</h1></html>"])
def test_unrecognized_response_is_an_error(body):
    url, _ = fixture(SAMPLES[0][0])
    source = create_adapter(Board(url, "Example"))
    with pytest.raises(ParseError):
        source.search.parse(body, source.search.build(""))


@pytest.mark.parametrize("url", ["https://example.org/jobs", "https://example.org/huntflow"])
def test_other_platform_url_is_rejected(url):
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Example"))


def test_remote_unknown_scope_is_not_worldwide():
    url, body = fixture("apicworld")
    source = create_adapter(Board(url, "Example"))
    first, second = source.search.parse(body, source.search.build("")).vacancies
    assert first.location == "Cyprus / Remote"
    assert first.locations[0].country == "CY"
    assert first.work_formats == ["remote_country"]
    assert second.id == "support-specialist-l1"
    assert second.title == "Support Specialist L1"
    assert second.location == "Remote"
    assert second.locations[0].country is None
    assert not second.locations[0].worldwide


def test_empty_state_and_generic_host_validation():
    source = create_adapter(Board("https://unrelated.huntflow.io/", "Other employer"))
    assert source.search.parse("<h2>Current openings</h2>", source.search.build("")).total == 0
    _, body = fixture("apicworld")
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.url == "https://unrelated.huntflow.io/vacancy/senior-php-developer-1"
    assert first.title == "Senior PHP Developer"
    with pytest.raises(ValueError):
        create_adapter(Board("https://demo.huntflow.io.evil.test/", "Example"))


async def test_two_tenant_hosts_construct_engine_without_network(tmp_path):
    sources = [create_adapter(Board(f"https://{name}.huntflow.io/", name)) for name in ("first", "second")]
    assert sources[0].resource != sources[1].resource
    store = Database(tmp_path / "boards.sqlite")

    def no_http(request):
        raise AssertionError("Adapter and engine construction must not perform HTTP")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
            engine = SearchEngine(Settings(), store, {source.id: source for source in sources}, client)
            assert engine.networks[sources[0].id] is not engine.networks[sources[1].id]
            await engine.close()
    finally:
        store.close()
