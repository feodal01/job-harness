"""Real saved smartrecruiters responses; expected vacancy facts reviewed from the source bodies."""

import json
from pathlib import Path

import pytest

from src.parsers.ats.models import Board
from src.parsers.ats.smartrecruiters.parser import create_adapter
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "smartrecruiters"
SAMPLES = [
    ("smartrecruiters", 23, "744000134902606", "Senior Product Manager (Career Sites)"),
    ("visa", 4, "744000133907678", "Sr. Manager"),
    ("bosch", 100, "744000135496889", "CONFERENTE (34526)"),
]


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
    assert source.search.parameters["query"] == "native"
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


@pytest.mark.parametrize("url", ["https://example.org/jobs", "https://example.org/smartrecruiters"])
def test_other_platform_url_is_rejected(url):
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Example"))


def test_total_and_sequential_offset_request():
    url, body = fixture("bosch")
    source = create_adapter(Board(url, "Example"))
    page = source.search.parse(body, source.search.build("QA"))
    assert page.total == 4649
    assert len(page.next_requests) == 1
    assert (
        page.next_requests[0].url
        == "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=100&q=QA&offset=100"
    )
    assert page.next_requests[0].query == "QA"
    payload = json.loads(body)
    payload["offset"] = 100
    page = source.search.parse(json.dumps(payload), page.next_requests[0])
    assert "offset=200" in page.next_requests[0].url
    payload["offset"] = 4600
    payload["content"] = payload["content"][:49]
    page = source.search.parse(json.dumps(payload), page.next_requests[0])
    assert page.next_requests == ()


def test_remote_country_location_and_non_remote_unknown_format():
    url, body = fixture("smartrecruiters")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.locations[0].country == "US"
    assert first.locations[0].city is None
    assert first.work_formats == ["remote_country"]
    assert first.published_at == "2026-06-30T01:06:01.967Z"
    assert first.employment == ["full_time"]
    assert (
        first.url
        == "https://jobs.smartrecruiters.com/smartrecruiters/744000134902606-senior-product-manager-career-sites"
    )
    url, body = fixture("visa")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.locations[0].city == "Austin"
    assert first.locations[0].country == "US"
    assert first.work_formats == []


def test_empty_and_invalid_pagination():
    source = create_adapter(Board("https://careers.smartrecruiters.com/Example", "Example"))
    payload = {"content": [], "totalFound": 0, "offset": 0, "limit": 100}
    assert source.search.parse(json.dumps(payload), source.search.build("")).total == 0
    payload["totalFound"] = 1
    with pytest.raises(ParseError):
        source.search.parse(json.dumps(payload), source.search.build(""))
    payload["content"] = [None]
    with pytest.raises(ParseError):
        source.search.parse(json.dumps(payload), source.search.build(""))
