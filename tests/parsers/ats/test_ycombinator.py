import json
from pathlib import Path

import pytest

from src.parsers.ats.models import Board
from src.parsers.ats.ycombinator.parser import create_adapter
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "ycombinator"


def board_case(company):
    path = FIXTURES / company / "success"
    url = json.loads((path / "meta.json").read_text())["captured_url"]
    return create_adapter(Board(url, company)), (path / "response.html").read_text()


@pytest.mark.parametrize(
    "company,count,uid,title,location",
    [
        ("prequel", 1, "43482", "Senior Software Engineer", "New York City"),
        (
            "veryfi",
            12,
            "96450",
            "Technical Account Manager Latam",
            "BR / MX / AR / CO / Remote (BR; MX; AR; CO)",
        ),
    ],
)
def test_real_boards(company, count, uid, title, location):
    source, body = board_case(company)
    page = source.search.parse(body, source.search.build(""))
    assert len(page.vacancies) == page.total == count
    first = page.vacancies[0]
    assert (first.id, first.title, first.location) == (uid, title, location)
    assert first.min_experience == 3 and first.employment == ["full_time"]
    assert first.published_at is None  # createdAt is an imprecise relative age.
    assert first.source_data["salaryRange"]
    if company == "veryfi":
        assert [place.country for place in first.locations] == ["BR", "MX", "AR", "CO"]
        assert first.work_formats == ["remote_country"]
        second = page.vacancies[1]
        assert second.id == "90288" and second.title == "Full-Stack Software Engineer"
        assert second.skills == ["JavaScript", "Python", "React"]
        assert not second.work_formats
    else:
        assert first.locations == []
        assert first.salary == []  # An unqualified $ range does not establish currency/period.


def test_company_label_does_not_control_facts():
    source, body = board_case("veryfi")
    renamed = create_adapter(Board(source.search.board.url, "Another label"))
    assert [
        v.model_dump(exclude={"company"})
        for v in source.search.parse(body, source.search.build("")).vacancies
    ] == [
        v.model_dump(exclude={"company"})
        for v in renamed.search.parse(body, renamed.search.build("")).vacancies
    ]


@pytest.mark.parametrize("body", ["<html>broken</html>", '<div data-page="WaasShowJobsPage"></div>'])
def test_malformed_payload(body):
    source, _ = board_case("prequel")
    with pytest.raises(ParseError):
        source.search.parse(body, source.search.build(""))


def test_empty_board():
    source, _ = board_case("prequel")
    body = '<div data-page=\'{"component":"WaasShowJobsPage","props":{"jobPostings":[]}}\'></div>'
    assert source.search.parse(body, source.search.build("")).total == 0


def test_rejects_unrelated_board():
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.com/companies/acme/jobs", "Acme"))
