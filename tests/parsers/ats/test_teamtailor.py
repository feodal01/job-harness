import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.parsers.ats.models import Board
from src.parsers.ats.teamtailor.parser import create_adapter
from src.parsers.interfaces import ParseError, SearchRequest

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/teamtailor"


@pytest.mark.parametrize(
    ("company", "count", "title", "location", "formats", "next_count"),
    [
        (
            "crystal",
            11,
            "Senior Python Engineer (DevEx Team, Remote)",
            "Warsaw, Bucharest, Lisbon",
            ["remote_country"],
            0,
        ),
        ("synthesized", 4, "Senior AI/Agentic Engineer", "London", ["hybrid"], 0),
        ("tradingview", 20, "Senior Product Manager", "Cyprus, Tbilisi", ["hybrid"], 1),
        ("osome", 20, "Audit Partner (HK)", "Hong Kong", ["hybrid"], 1),
        ("sumsub", 20, "Solution Architect (EMEA)", "Limassol Office 2", ["remote_country"], 1),
    ],
)
def test_real_native_and_custom_boards(company, count, title, location, formats, next_count) -> None:
    fixture = FIXTURES / company / "success"
    meta = json.loads((fixture / "meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    page = adapter.search.parse((fixture / "response.html").read_text(), adapter.search.build(""))
    assert len(page.vacancies) == count
    assert len(page.next_requests) == next_count
    first = page.vacancies[0]
    assert first.id == first.source_data["id"]
    assert first.title == title
    assert first.location == location
    assert first.work_formats == formats
    assert first.locations == []  # Labels are not structured eligibility or countries.
    assert first.salary == {} and first.employment == []
    assert len({v.id for v in page.vacancies}) == count
    assert all(urlsplit(v.url).hostname == adapter.resource for v in page.vacancies)


@pytest.mark.parametrize(
    ("company", "count", "next_count"), [("tradingview", 20, 1), ("osome", 1, 0), ("sumsub", 7, 0)]
)
def test_captured_show_more_pages(company: str, count: int, next_count: int) -> None:
    fixture = FIXTURES / company
    meta = json.loads((fixture / "success/meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    meta = json.loads((fixture / "pagination/meta.json").read_text())
    request = SearchRequest(meta["captured_url"], "qa")
    page = adapter.search.parse((fixture / "pagination/response.html").read_text(), request)
    assert len(page.vacancies) == count
    assert len(page.next_requests) == next_count
    if company == "tradingview":
        # Captured HTML repeats page=2 before page=3; the last value is the next page.
        assert parse_qs(urlsplit(page.next_requests[0].url).query)["page"] == ["2", "3"]
        assert page.next_requests[0].query == "qa"


def test_teamtailor_custom_board_validation_and_unknown_document() -> None:
    adapter = create_adapter(Board("https://careers.example.org/jobs", "Example"))
    with pytest.raises(ParseError):
        adapter.search.parse("<html>Generic company home page</html>", adapter.search.build(""))
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.org/jobs/123-test", "Example"))
    assert adapter.allowed_hosts == ("careers.example.org",)
