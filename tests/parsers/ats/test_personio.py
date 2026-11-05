import json
from pathlib import Path

import pytest

from src.parsers.ats.models import Board
from src.parsers.ats.personio.parser import create_adapter
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/personio/vivid-money"


def test_captured_personio_list_and_detail() -> None:
    meta = json.loads((FIXTURES / "success/meta.json").read_text())
    adapter = create_adapter(Board(meta["captured_url"], "Vivid Money"))
    page = adapter.search.parse(
        (FIXTURES / "success/response.html").read_text(), adapter.search.build("ignored")
    )
    assert len(page.vacancies) == page.total == 21
    assert not page.next_requests
    first = page.vacancies[0]
    assert first.id == first.source_data["id"]
    assert first.title == "Junior Risk & Compliance Officer"
    assert first.url == "https://vivid.jobs.personio.de/job/2665389"
    assert first.location == "Amsterdam, Remote"
    assert first.employment == ["full_time"]
    assert first.work_formats == ["remote_country"]
    assert first.locations == []
    assert first.salary == []
    vacancy = next(v for v in page.vacancies if v.source_data["id"] == "2628973")
    assert adapter.vacancy_details is not None
    assert adapter.vacancy_details.url(vacancy) == vacancy.url
    result = adapter.vacancy_details.parse((FIXTURES / "detail/response.html").read_text(), vacancy)
    assert result.id == vacancy.id
    assert result.published_at == "2026-05-08"
    assert result.description and "Detection & Response" in result.description
    assert result.description_html and "<ul>" in result.description_html
    assert "<script" not in result.description_html
    assert {p.country for p in result.locations} == {"KZ", "CY", "ES", "RS"}
    assert "description" in adapter.capabilities.fields


def test_personio_wrong_detail_and_invalid_document_fail() -> None:
    adapter = create_adapter(Board("https://vivid.jobs.personio.de", "Any name"))
    page = adapter.search.parse((FIXTURES / "success/response.html").read_text(), adapter.search.build(""))
    assert adapter.vacancy_details is not None
    with pytest.raises(ParseError, match="id does not match"):
        adapter.vacancy_details.parse((FIXTURES / "detail/response.html").read_text(), page.vacancies[0])
    with pytest.raises(ParseError):
        adapter.search.parse("<html>Unexpected gateway response</html>", adapter.search.build(""))
    empty = adapter.search.parse("<p>No positions at the moment</p>", adapter.search.build(""))
    assert empty.total == 0


@pytest.mark.parametrize(
    "url", ["https://personio.de", "https://example.com/jobs", "https://a.jobs.personio.de/job/1"]
)
def test_personio_rejects_non_board_urls(url: str) -> None:
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Company"))


def test_personio_identity_does_not_depend_on_company_display_name() -> None:
    left = create_adapter(Board("https://vivid.jobs.personio.de/", "Original"))
    right = create_adapter(Board("https://vivid.jobs.personio.de", "Renamed"))
    assert left.id == right.id
    assert left.resource == "vivid.jobs.personio.de"
    assert left.allowed_hosts == ("vivid.jobs.personio.de",)


def test_personio_absolute_native_links_and_foreign_identity() -> None:
    adapter = create_adapter(Board("https://vivid.jobs.personio.de/", "Example"))
    body = (FIXTURES / "success/response.html").read_text()
    absolute = body.replace('href="/job/', 'href="https://vivid.jobs.personio.de/job/')
    page = adapter.search.parse(absolute, adapter.search.build(""))
    assert len(page.vacancies) == 21
    with pytest.raises(ParseError, match="does not belong"):
        adapter.search.parse(
            absolute.replace(
                "https://vivid.jobs.personio.de/job/2665389", "https://other.jobs.personio.de/job/2665389"
            ),
            adapter.search.build(""),
        )
