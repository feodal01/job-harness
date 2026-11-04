import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.parsers.ats.icims.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError, SearchRequest

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/icims"


@pytest.mark.parametrize(
    ("company", "count", "title", "country", "city", "employment", "next_count"),
    [
        ("expleo", 12, "Senior Data Analyst", "IE", "Dublin", ["contract"], 0),
        ("epe-consulting", 50, "Senior Power Systems Engineer", "US", "Austin", ["full_time"], 1),
        ("western-southern", 20, "Project Management Office Project Manager II", "US", "CINCINNATI", [], 1),
    ],
)
def test_captured_icims_boards(company, count, title, country, city, employment, next_count) -> None:
    fixture = FIXTURES / company / "success"
    meta = json.loads((fixture / "meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    page = adapter.search.parse((fixture / "response.html").read_text(), adapter.search.build("qa"))
    assert len(page.vacancies) == count
    first = page.vacancies[0]
    assert first.id == first.source_data["id"]
    assert first.title == title
    assert first.locations[0].country == country
    assert first.locations[0].city == city
    assert first.employment == employment
    assert first.summary and first.summary_html
    assert "<script" not in first.summary_html
    assert len(page.next_requests) == next_count
    if company == "expleo":
        assert first.work_formats == ["hybrid"]
        assert "data transformation programme" in first.summary
    else:
        assert first.work_formats == []


@pytest.mark.parametrize(
    ("company", "case", "count", "next_count"),
    [
        ("epe-consulting", "pagination_pr_1", 23, 0),
        ("western-southern", "pagination_pr_1", 20, 1),
        ("western-southern", "pagination_pr_2", 20, 1),
        ("western-southern", "pagination_pr_3", 20, 1),
        ("western-southern", "pagination_pr_4", 20, 1),
        ("western-southern", "pagination_pr_5", 9, 0),
    ],
)
def test_captured_icims_pagination(company, case, count, next_count) -> None:
    fixture = FIXTURES / company
    meta = json.loads((fixture / "success/meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    meta = json.loads((fixture / case / "meta.json").read_text())
    page = adapter.search.parse(
        (fixture / case / "response.html").read_text(), SearchRequest(meta["captured_url"], "qa")
    )
    assert len(page.vacancies) == count
    assert len(page.next_requests) == next_count
    assert all(req.query == "qa" for req in page.next_requests)


def test_icims_pagination_keeps_filter_and_rejects_unexpected_body() -> None:
    fixture = FIXTURES / "western-southern/success"
    meta = json.loads((fixture / "meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], "Example"))
    request = SearchRequest(adapter.search.build("").url + "&searchKeyword=QA", "qa")
    page = adapter.search.parse((fixture / "response.html").read_text(), request)
    assert all(parse_qs(urlsplit(req.url).query)["searchKeyword"] == ["QA"] for req in page.next_requests)
    with pytest.raises(ParseError):
        adapter.search.parse("<html>Access unavailable</html>", request)
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.com/jobs/search", "Example"))
