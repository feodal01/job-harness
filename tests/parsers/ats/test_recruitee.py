"""Real saved recruitee responses; expected vacancy facts reviewed from the source bodies."""

import json
from pathlib import Path

import httpx
import pytest

from src.core.config import Settings
from src.db import Database
from src.engine import SearchEngine
from src.parsers.ats.models import Board
from src.parsers.ats.recruitee.parser import create_adapter
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "recruitee"
SAMPLES = [("wallarm", 1, "2412507", "Senior Rust Developer"), ("bunq", 19, "2642872", "Reporting Expert")]


def fixture(company):
    directory = FIXTURES / company
    meta = json.loads((directory / "meta.json").read_text())
    return meta["captured_url"], (directory / "response.json").read_text()


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


@pytest.mark.parametrize("url", ["https://example.org/jobs", "https://example.org/recruitee"])
def test_other_platform_url_is_rejected(url):
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Example"))


def test_structured_location_list_requirements_and_timestamp():
    url, body = fixture("wallarm")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert {place.country for place in first.locations} == {"BG", "RS", "ES", "PT"}
    assert first.locations[0].city == "Sofia"
    assert first.work_formats == ["remote_country"]
    assert first.employment == ["full_time"]
    assert first.published_at == "2026-01-13T11:54:47Z"
    assert "<h2>Requirements</h2>" in first.description_html
    assert "<li>" in first.description_html
    assert first.salary == {}


def test_hybrid_countries_and_custom_public_job_url_preserved():
    url, body = fixture("bunq")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.work_formats == ["hybrid"]
    assert {place.country for place in first.locations} == {"NL", "TR", "BG"}
    assert first.url == "https://careers.bunq.com/o/reporting-expert"
    assert first.employment == ["full_time"]
    assert source.allowed_hosts == ("bunq.recruitee.com",)


def test_empty_and_salary_keys_match_normalized_filter_contract():
    source = create_adapter(Board("https://demo.recruitee.com/", "Example"))
    assert source.search.parse('{"offers": []}', source.search.build("")).total == 0
    _, body = fixture("wallarm")
    payload = json.loads(body)
    payload["offers"][0]["salary"] = {"min": 100000, "max": 120000, "currency": "USD", "period": "yearly"}
    first = source.search.parse(json.dumps(payload), source.search.build("")).vacancies[0]
    assert first.salary == {"from": 100000, "to": 120000, "currencyCode": "USD", "period": "year"}


async def test_two_tenant_hosts_construct_engine_without_network(tmp_path):
    sources = [create_adapter(Board(f"https://{name}.recruitee.com/", name)) for name in ("first", "second")]
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
