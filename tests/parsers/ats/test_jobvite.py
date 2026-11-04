import json
from pathlib import Path

import pytest

from src.parsers.ats.jobvite.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError, ResponseInfo, SearchRequest

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/jobvite"


@pytest.mark.parametrize(
    ("company", "case", "count", "title", "location", "next_count"),
    [
        ("progress", "success", 48, "Executive Assistant II", "Hybrid Remote, Burlington, Massachusetts", 0),
        ("visionist", "success", 43, "Signals Analyst", "Fort Meade, MD", 1),
        ("visionist", "pagination_software_engineering", 27, "AI Platform Lead", "Laurel, MD", 0),
    ],
)
def test_captured_jobvite_boards_and_category_page(company, case, count, title, location, next_count) -> None:
    fixture = FIXTURES / company
    meta = json.loads((fixture / "success/meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], company))
    meta = json.loads((fixture / case / "meta.json").read_text())
    page = adapter.search.parse(
        (fixture / case / "response.html").read_text(), SearchRequest(meta["captured_url"], "qa")
    )
    assert len(page.vacancies) == count
    assert len(page.next_requests) == next_count
    first = page.vacancies[0]
    assert first.id == first.source_data["id"]
    assert first.title == title and first.location == location
    assert first.company.name == company
    assert first.salary == {} and first.locations == []
    if company == "progress":
        assert first.work_formats == ["hybrid"]
        assert first.url == "https://jobs.jobvite.com/progress/job/ohwmAfwt"


def test_jobvite_show_more_uses_real_parallel_requests() -> None:
    adapter = create_adapter(Board("https://jobs.jobvite.com/visionist", "Example"))
    body = (FIXTURES / "visionist/success/response.html").read_text()
    body += '<a href="/visionist/search?c=Other&amp;p=0">Show more</a>'
    page = adapter.search.parse(body, adapter.search.build("qa"))
    assert len(page.next_requests) == 2
    assert all(request.query == "qa" for request in page.next_requests)
    assert not hasattr(page.next_requests[0], "headers")


def test_jobvite_unavailable_redirect_classified_before_following() -> None:
    adapter = create_adapter(Board("https://jobs.jobvite.com/visionist", "Example"))
    assert adapter.classify is not None
    assert (
        adapter.classify(
            ResponseInfo(
                adapter.search.build("").url,
                302,
                {
                    "Location": "/careers/info/unavailable.html",
                },
                "",
            )
        )
        == "blocked"
    )
    assert (
        adapter.classify(ResponseInfo("https://jobs.jobvite.com/careers/info/unavailable.html", 200, {}, ""))
        == "blocked"
    )
    with pytest.raises(ParseError):
        adapter.search.parse("<html>Temporarily unavailable</html>", adapter.search.build(""))
    with pytest.raises(ValueError):
        create_adapter(Board("https://other.example/visionist", "Example"))
