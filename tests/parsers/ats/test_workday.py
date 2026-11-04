import json
from pathlib import Path

import httpx
import pytest

from src.core.config import Settings
from src.db import Database
from src.engine import SearchEngine
from src.models.brief import BriefInput
from src.parsers.ats.models import Board
from src.parsers.ats.successfactors.parser import create_adapter as create_successfactors_adapter
from src.parsers.ats.taleo.parser import create_adapter as create_taleo_adapter
from src.parsers.ats.workday.parser import create_adapter
from src.parsers.interfaces import ParseError, SearchRequest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "workday"


def board_case(company, kind="success"):
    root = FIXTURES / company
    url = json.loads((root / "success" / "meta.json").read_text())["captured_url"].split("?", 1)[0]
    return create_adapter(Board(url, company)), (root / kind / "response.json").read_text()


@pytest.mark.parametrize(
    "company,total,uid,title",
    [
        ("semrush", 47, "JR100839", "Senior Data Scientist (Sota Team)"),
        ("nvidia", 2000, "JR2024972-1", "Senior Systems Software Engineer- EDA Infrastructure"),
    ],
)
def test_real_boards(company, total, uid, title):
    source, body = board_case(company)
    request = source.search.build("QA")
    assert request.method == "POST"
    assert request.json_body == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "QA"}
    page = source.search.parse(body, request)
    assert page.total == total and len(page.vacancies) == 20
    first = page.vacancies[0]
    assert (first.id, first.title) == (uid, title)
    assert first.location == "6 Locations"
    assert first.locations == [] and first.work_formats == [] and first.published_at is None
    assert first.url.endswith(first.source_data["externalPath"])
    (next_request,) = page.next_requests
    assert next_request.method == "POST" and next_request.query == "QA"
    assert next_request.url == request.url
    assert next_request.json_body == {"appliedFacets": {}, "limit": 20, "offset": 20, "searchText": "QA"}


@pytest.mark.parametrize("offset,count,next_offset", [(20, 20, 40), (40, 7, None)])
def test_real_pagination(offset, count, next_offset):
    source, body = board_case("semrush", f"pagination_offset_{offset}")
    initial = source.search.build("Python")
    request = SearchRequest(
        initial.url,
        "Python",
        method="POST",
        json_body={
            "appliedFacets": {},
            "limit": 20,
            "offset": offset,
            "searchText": "Python",
        },
    )
    page = source.search.parse(body, request)
    assert len(page.vacancies) == count and page.total == 47
    if next_offset is None:
        assert page.next_requests == ()
    else:
        assert page.next_requests[0].json_body["offset"] == next_offset
        assert page.next_requests[0].json_body["searchText"] == "Python"


@pytest.mark.parametrize(
    "company,uid,date,format",
    [
        ("semrush", "JR100794", "2026-06-24", ["remote_country"]),
        ("nvidia", "JR2024972-1", "2026-10-11", []),
    ],
)
def test_real_details(company, uid, date, format):
    source, body = board_case(company)
    vacancy = next(v for v in source.search.parse(body, source.search.build("")).vacancies if v.id == uid)
    details = source.vacancy_details
    detail_body = (FIXTURES / company / "detail" / "response.json").read_text()
    captured_url = json.loads((FIXTURES / company / "detail" / "meta.json").read_text())["captured_url"]
    assert details.url(vacancy) == captured_url
    result = details.parse(detail_body, vacancy)
    assert result.id == uid and result.published_at == date
    assert len(result.description) > 1000 and result.description_html
    assert result.work_formats == format
    assert result.locations[0].country == "US" and result.locations[0].city is None
    assert result.employment == ["full_time"]
    assert vacancy.description is None  # Enrichment does not mutate the input.
    if company == "semrush":
        assert "United States - Maryland" in result.location
        assert "Technical Pre-Sales" in result.description
    else:
        assert "US, MA, Westford" in result.location
        assert result.source_data["detail"]["jobReqId"] == "JR2024972"


async def test_native_queries_and_public_board_urls():
    source = create_adapter(Board("https://sample.wd5.myworkdayjobs.com/en-US/Careers", "Sample"))

    async def no_fetch(url):
        raise AssertionError("No preparatory HTTP request is required")

    plan = await source.search.prepare(BriefInput(name="QA", queries=["QA", "Python"]), no_fetch)
    assert [request.json_body["searchText"] for request in plan.requests] == ["QA", "Python"]
    assert plan.application["query"] == "native"
    assert plan.requests[0].url == "https://sample.wd5.myworkdayjobs.com/wday/cxs/sample/Careers/jobs"


def test_company_label_does_not_control_facts():
    source, body = board_case("nvidia")
    renamed = create_adapter(Board(source.search.board.url, "Another label"))
    assert [
        v.model_dump(exclude={"company"})
        for v in source.search.parse(body, source.search.build("")).vacancies
    ] == [
        v.model_dump(exclude={"company"})
        for v in renamed.search.parse(body, renamed.search.build("")).vacancies
    ]


@pytest.mark.parametrize("body", ["{}", '{"total":1,"jobPostings":[]}', '{"total":1,"jobPostings":[{}]}'])
def test_malformed_payload(body):
    source, _ = board_case("nvidia")
    with pytest.raises(ParseError):
        source.search.parse(body, source.search.build(""))


def test_empty_and_detail_identity():
    source, body = board_case("nvidia")
    assert source.search.parse('{"total":0,"jobPostings":[]}', source.search.build("")).total == 0
    vacancy = source.search.parse(body, source.search.build("")).vacancies[0]
    changed = json.loads((FIXTURES / "nvidia" / "detail" / "response.json").read_text())
    changed["jobPostingInfo"]["jobPostingId"] = "another-posting"
    with pytest.raises(ParseError):
        source.vacancy_details.parse(json.dumps(changed), vacancy)


def test_rejects_unrelated_board():
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.com/Careers", "Acme"))


@pytest.mark.parametrize(
    "factory,urls",
    [
        (
            create_adapter,
            (
                "https://first.wd5.myworkdayjobs.com/Careers",
                "https://second.wd5.myworkdayjobs.com/Careers",
                "https://first.wd5.myworkdayjobs.com/OtherCareers",
            ),
        ),
        (
            create_taleo_adapter,
            (
                "https://phg.tbe.taleo.net/phg01/ats/careers/v2/searchResults?org=FIRST&cws=37",
                "https://phe.tbe.taleo.net/phe01/ats/careers/v2/searchResults?org=SECOND&cws=37",
                "https://phg.tbe.taleo.net/phg01/ats/careers/v2/searchResults?org=THIRD&cws=37",
            ),
        ),
        (
            create_successfactors_adapter,
            (
                "https://career012.successfactors.eu/career?company=first",
                "https://career8.successfactors.com/career?company=second",
                "https://career012.successfactors.eu/career?company=third",
            ),
        ),
    ],
)
async def test_multiple_board_hosts_coexist_and_same_host_shares_budget(tmp_path, factory, urls):
    sources = [factory(Board(url, f"Company {index}")) for index, url in enumerate(urls)]
    store = Database(tmp_path / "boards.sqlite")

    def no_http(request):
        raise AssertionError("Constructing adapters and engine must not perform HTTP")

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_http)) as client:
            engine = SearchEngine(Settings(), store, {source.id: source for source in sources}, client)
            assert engine.networks[sources[0].id] is not engine.networks[sources[1].id]
            assert engine.networks[sources[0].id] is engine.networks[sources[2].id]
            await engine.close()
    finally:
        store.close()
