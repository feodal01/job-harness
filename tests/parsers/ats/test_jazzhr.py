import json
from pathlib import Path

import pytest

from src.parsers.ats.jazzhr.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/jazzhr"


@pytest.mark.parametrize(
    ("company", "count", "title", "location", "identifier"),
    [
        ("foundation-ai", 12, "Application Support Engineer", "Hyderabad, India", "ZBfHaf2Nv9"),
        ("imanage", 40, "Senior AI Data Engineer", "Chicago, IL", "Gsyz0IZVAd"),
        ("pairsoft", 11, "Accounts Receivable Analyst (US Accounting)", "Remote", "Bqj75U397V"),
    ],
)
def test_captured_jazzhr_boards(company, count, title, location, identifier) -> None:
    fixture = FIXTURES / company / "success"
    meta = json.loads((fixture / "meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    page = adapter.search.parse((fixture / "response.html").read_text(), adapter.search.build(""))
    assert len(page.vacancies) == page.total == count
    first = page.vacancies[0]
    assert first.id == first.source_data["id"]
    assert first.title == title and first.location == location
    assert first.source_data["id"] == identifier
    assert first.url.endswith("/apply/jobs/details/" + identifier)
    assert first.locations == [] and first.employment == []
    if company == "pairsoft":
        assert first.source_data["metadata"]["department"] == "Accounting and Finance"
        assert first.work_formats == ["remote_country"]
    assert not page.next_requests


def test_jazzhr_missing_markup_is_not_empty_board() -> None:
    adapter = create_adapter(Board("https://example.applytojob.com", "Example"))
    with pytest.raises(ParseError):
        adapter.search.parse("<html>An error occurred</html>", adapter.search.build(""))
    assert adapter.search.parse("<p>No jobs found</p>", adapter.search.build("")).total == 0
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.com/apply/jobs", "Example"))
