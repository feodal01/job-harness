import hashlib
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from src.models.brief import BriefInput
from src.parsers.ats.join.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/join/sidestream"


def test_captured_join_list_and_detail_preserve_explicit_remote_scope() -> None:
    adapter = create_adapter(Board("https://join.com/companies/sidestream", "Sidestream"))
    page = adapter.search.parse(
        (FIXTURES / "success/response.html").read_text(), adapter.search.build("ignored")
    )
    assert len(page.vacancies) == page.total == 4
    assert not page.next_requests
    first = page.vacancies[0]
    assert first.id == first.source_data["id"]
    assert first.title == "Junior Projektmanager (w/m/d) - Quereinsteiger willkommen"
    assert first.location == "Cologne, Germany"
    assert first.locations[0].country == "DE" and first.locations[0].city == "Cologne"
    assert first.work_formats == ["hybrid"]
    assert first.employment == []  # The source's "Employee" label says nothing about full-time.
    vacancy = page.vacancies[1]
    assert vacancy.source_data["id"] == "16328249"
    assert vacancy.work_formats == ["remote_country"]
    assert vacancy.locations[0].country == "DE" and vacancy.locations[0].city is None
    assert adapter.vacancy_details is not None
    result = adapter.vacancy_details.parse((FIXTURES / "detail/response.html").read_text(), vacancy)
    assert result.id == vacancy.id
    assert result.employment == ["full_time"]
    assert result.published_at == "2026-06-19T06:46:26.566Z"
    assert result.description and "Why Join Sidestream?" in result.description
    assert result.description_html and "<ul>" in result.description_html
    assert "&lt;p&gt;" not in result.description_html
    assert result.salary == []
    assert result.locations[0].worldwide is False


def test_join_wrong_detail_and_missing_state_fail() -> None:
    adapter = create_adapter(Board("https://join.com/companies/sidestream", "Anything"))
    page = adapter.search.parse((FIXTURES / "success/response.html").read_text(), adapter.search.build(""))
    assert adapter.vacancy_details is not None
    with pytest.raises(ParseError, match="id does not match"):
        adapter.vacancy_details.parse((FIXTURES / "detail/response.html").read_text(), page.vacancies[0])
    with pytest.raises(ParseError):
        adapter.search.parse("<html>Company home</html>", adapter.search.build(""))
    with pytest.raises(ValueError):
        create_adapter(Board("https://join.com/companies/sidestream/123-position", "Anything"))


def test_join_empty_state_and_unsupported_pagination_do_not_silently_truncate() -> None:
    adapter = create_adapter(Board("https://join.com/companies/example", "Example"))
    soup = BeautifulSoup((FIXTURES / "success/response.html").read_text(), "html.parser")
    payload = json.loads(soup.select_one("#__NEXT_DATA__").get_text())
    jobs = payload["props"]["pageProps"]["initialState"]["jobs"]
    jobs["items"] = []
    jobs["pagination"] = {"page": 1, "pageCount": 0, "total": 0}
    body = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
    assert adapter.search.parse(body, adapter.search.build("")).total == 0
    jobs["pagination"] = {"page": 1, "pageCount": 2, "total": 10}
    body = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
    with pytest.raises(ParseError, match="more results"):
        adapter.search.parse(body, adapter.search.build(""))


def test_join_pagination_follows_explicit_link_and_rejects_inconsistent_counts() -> None:
    adapter = create_adapter(Board("https://join.com/companies/sidestream", "Example"))
    soup = BeautifulSoup((FIXTURES / "success/response.html").read_text(), "html.parser")
    payload = json.loads(soup.select_one("#__NEXT_DATA__").get_text())
    jobs = payload["props"]["pageProps"]["initialState"]["jobs"]
    jobs["pagination"] = {"page": 1, "pageCount": 2, "total": 8}
    body = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
    body += '<a rel="next" href="/companies/sidestream?page=2">Next</a>'
    page = adapter.search.parse(body, adapter.search.build("qa"))
    assert page.next_requests[0].url == "https://join.com/companies/sidestream?page=2"
    assert page.next_requests[0].query == "qa"
    with pytest.raises(ParseError, match="changed board"):
        adapter.search.parse(
            body.replace('href="/companies/sidestream', 'href="https://other.example/companies/sidestream'),
            adapter.search.build(""),
        )
    jobs["pagination"]["total"] = 0
    body = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
    with pytest.raises(ParseError, match="contradict"):
        adapter.search.parse(body, adapter.search.build(""))


NATIVE = FIXTURES.parent / "native_filters"


@pytest.mark.parametrize("company", ["join", "softgate", "aroundhome", "meet5", "jehle-gruppe"])
@pytest.mark.asyncio
async def test_saved_native_categories_and_public_ids(company):
    directory = NATIVE / company
    meta = json.loads((directory / "meta.json").read_text())
    board_body = (directory / "board.html").read_text()
    calls = []

    async def fetch(url):
        calls.append(url)
        return board_body

    for label in ["positive", "negative"]:
        expected_input = meta[label]["request"]["json_body"]["variables"]["input"]
        category_ids = [str(v) for v in expected_input["filters"]["categoryIds"]]
        source = create_adapter(
            Board(meta["board"]["request"]["url"], "Renamed", native_filters={"categoryIds": category_ids})
        )
        plan = await source.search.prepare(BriefInput(name="Test", queries=["Engineer", "Developer"]), fetch)
        assert len(plan.requests) == 1
        request = plan.requests[0]
        assert request.method == "POST"
        assert request.url == "https://join.com/candidate-api/graphql"
        assert request.json_body["variables"]["input"] == expected_input
        assert source.search.parameters["query"] == "ignored"
        body = (directory / meta[label]["file"]).read_text()
        payload = json.loads(body)["data"]["publicJobs"]
        page = source.search.parse(body, request)
        assert [v.id for v in page.vacancies] == [v["idParam"].split("-", 1)[0] for v in payload["items"]]
        assert [v.source_data["internal_id"] for v in page.vacancies] == [
            str(v["id"]) for v in payload["items"]
        ]
        assert page.total == payload["pageInfo"]["rowCount"]
        assert not page.next_requests
        assert hashlib.sha256(body.encode()).hexdigest() == meta[label]["response_sha256"]
    assert len(calls) == 2
    assert page.vacancies == []


def test_saved_join_boards_allow_distinct_public_and_internal_ids():
    for directory in NATIVE.iterdir():
        meta = json.loads((directory / "meta.json").read_text())
        source = create_adapter(Board(meta["board"]["request"]["url"], "Example"))
        page = source.search.parse((directory / "board.html").read_text(), source.search.build(""))
        assert page.vacancies
        assert all(v.url.split("/")[-1].startswith(v.id + "-") for v in page.vacancies)


@pytest.mark.asyncio
async def test_live_graphql_pagination_keeps_categories_and_company_and_details_identity():
    directory = FIXTURES.parent / "native_live/softgate"
    source = create_adapter(Board("https://join.com/companies/softgate", "Example", {"categoryIds": "351"}))

    async def fetch(url):
        return (directory / "preparation.html").read_text()

    request = (await source.search.prepare(BriefInput(name="Test"), fetch)).requests[0]
    request.json_body["variables"]["input"]["paginationPage"]["pageSize"] = 1
    ids = []
    first = None
    for number in range(1, 4):
        meta = json.loads((directory / f"pagination-{number}.meta.json").read_text())
        assert request.json_body == meta["request"]["json_body"]
        body = (directory / f"pagination-{number}.json").read_bytes()
        assert hashlib.sha256(body).hexdigest() == meta["response_sha256"]
        page = source.search.parse(body.decode(), request)
        first = first or page.vacancies[0]
        ids.extend(v.id for v in page.vacancies)
        if number < 3:
            request = page.next_requests[0]
    assert ids == ["16783479", "16802872", "16757906"]
    assert page.total == 3 and not page.next_requests
    enriched = source.vacancy_details.parse((directory / "detail.html").read_text(), first)
    assert enriched.id == first.id and enriched.description


@pytest.mark.asyncio
async def test_graphql_error_missing_data_bad_counts_and_company_scope_fail():
    directory = FIXTURES.parent / "native_live/join"
    source = create_adapter(Board("https://join.com/companies/join", "Example", {"categoryIds": "372"}))

    async def fetch(url):
        return (directory / "preparation.html").read_text()

    request = (await source.search.prepare(BriefInput(name="Test"), fetch)).requests[0]
    for payload in [{"errors": [{"message": "failure"}], "data": None}, {"data": {}}]:
        with pytest.raises(ParseError):
            source.search.parse(json.dumps(payload), request)
    payload = json.loads((directory / "positive-1.json").read_text())
    payload["data"]["publicJobs"]["items"][0]["companyId"] = 999
    with pytest.raises(ParseError, match="company"):
        source.search.parse(json.dumps(payload), request)
    payload = json.loads((directory / "positive-1.json").read_text())
    payload["data"]["publicJobs"]["pageInfo"]["rowCount"] = 0
    with pytest.raises(ParseError, match="contradict"):
        source.search.parse(json.dumps(payload), request)


@pytest.mark.parametrize("value", ["Sales", "0", "-1", ["372", "invalid"]])
def test_category_ids_must_be_explicit_numeric_provider_values(value):
    with pytest.raises(ValueError, match="categoryIds"):
        create_adapter(Board("https://join.com/companies/join", "Example", {"categoryIds": value}))
