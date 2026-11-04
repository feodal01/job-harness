import json
from pathlib import Path

import pytest

from src.models.brief import BriefInput
from src.parsers.ats.comeet.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "comeet"


def board_case(company: str):
    path = FIXTURES / company / "success"
    url = json.loads((path / "meta.json").read_text())["captured_url"]
    return create_adapter(Board(url, company)), (path / "response.html").read_text()


@pytest.mark.parametrize(
    "company,count,uid,title,country,city,work_format",
    [
        ("tripleten", 305, "AC.F59", "AI Automation Teaching Experts", "US", "Boston", "remote_country"),
        ("comm-it", 151, "54.460", "Account Executive- Defense Sector", "IL", "Petah Tikva", "hybrid"),
    ],
)
def test_real_boards(company, count, uid, title, country, city, work_format):
    source, body = board_case(company)
    page = source.search.parse(body, source.search.build("ignored query"))
    assert len(page.vacancies) == page.total == count
    vacancy = page.vacancies[0]
    assert (vacancy.id, vacancy.title) == (uid, title)
    assert (vacancy.locations[0].country, vacancy.locations[0].city) == (country, city)
    assert vacancy.work_formats == [work_format]
    assert vacancy.description and vacancy.description_html
    assert vacancy.published_at is None  # time_updated is not a publication timestamp.
    assert page.next_requests == ()
    assert "<script" not in vacancy.description_html


def test_company_label_does_not_control_facts():
    source, body = board_case("tripleten")
    renamed = create_adapter(Board(source.search.board.url, "Unrelated display label"))
    left = source.search.parse(body, source.search.build(""))
    right = renamed.search.parse(body, renamed.search.build(""))
    assert [v.model_dump(exclude={"company"}) for v in left.vacancies] == [
        v.model_dump(exclude={"company"}) for v in right.vacancies
    ]


async def test_one_board_request_for_multiple_queries():
    source, _ = board_case("comm-it")

    async def no_fetch(url):
        raise AssertionError("No preparatory HTTP request is required")

    plan = await source.search.prepare(BriefInput(name="QA", queries=["QA", "Python"]), no_fetch)
    assert len(plan.requests) == 1
    assert plan.application["query"] == "ignored"


@pytest.mark.parametrize(
    "body", ["<html>broken</html>", "COMPANY_DATA = {}; COMPANY_POSITIONS_DATA = [null];"]
)
def test_malformed_payload(body):
    source, _ = board_case("tripleten")
    with pytest.raises(ParseError):
        source.search.parse(body, source.search.build(""))


def test_explicit_empty():
    source, _ = board_case("tripleten")
    page = source.search.parse("COMPANY_DATA = {}; COMPANY_POSITIONS_DATA = [];", source.search.build(""))
    assert page.total == 0 and not page.vacancies


def test_rejects_unrelated_board():
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.com/jobs/acme/123", "Acme"))
