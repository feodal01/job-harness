import json
from pathlib import Path

import pytest

from src.models.brief import BriefInput
from src.parsers.ats.models import Board
from src.parsers.ats.successfactors.parser import create_adapter
from src.parsers.interfaces import ParseError, SearchRequest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "successfactors"


def board_case(company):
    path = FIXTURES / company / "success"
    url = json.loads((path / "meta.json").read_text())["captured_url"]
    return create_adapter(Board(url, company)), (path / "response.html").read_text()


@pytest.mark.parametrize(
    "company,count,uid,title,country",
    [
        ("pictet", 43, "124592", "(Senior) Investment Specialist (m/f/d)", "DE"),
        ("brevard-county", 59, "23286", "Accounting Technician", None),
        ("mindray", 112, "5514", "Accountant, Poland- Fixed Term Contract", "PL"),
    ],
)
def test_real_boards(company, count, uid, title, country):
    source, body = board_case(company)
    page = source.search.parse(body, source.search.build(""))
    assert len(page.vacancies) == page.total == count
    first = page.vacancies[0]
    assert (first.id, first.title) == (uid, title)
    assert (first.locations[0].country if first.locations else None) == country
    assert first.description and first.description_html
    assert not first.work_formats
    assert "career_job_req_id=" + uid in first.url
    if company == "brevard-county":
        assert first.published_at == "2026-06-26"
        assert first.location is None
    elif company == "pictet":
        assert first.locations[0].city == "Frankfurt"
    else:
        assert first.location == "Warsaw, Poland"
        assert first.locations[0].city is None  # Free-form description retained, not guessed.


def test_company_label_does_not_control_facts():
    source, body = board_case("mindray")
    renamed = create_adapter(Board(source.search.board.url, "Another label"))
    assert [
        v.model_dump(exclude={"company"})
        for v in source.search.parse(body, source.search.build("")).vacancies
    ] == [
        v.model_dump(exclude={"company"})
        for v in renamed.search.parse(body, renamed.search.build("")).vacancies
    ]


@pytest.mark.parametrize(
    "body", ["<html></html>", "<broken", "<Job-Listing><Job><ReqId>1</ReqId></Job></Job-Listing>"]
)
def test_malformed_payload(body):
    source, _ = board_case("pictet")
    with pytest.raises(ParseError):
        source.search.parse(body, source.search.build(""))


def test_explicit_empty_and_url_building():
    source = create_adapter(Board("https://career012.successfactors.eu/career?company=example", "Label"))
    assert "career_ns=job_listing_summary" in source.search.build("").url
    assert "resultType=XML" in source.search.build("").url
    assert source.search.parse("<Job-Listing/>", source.search.build("")).total == 0
    assert source.search.parameters["query"] == "ignored"
    assert "/search/" in source.search.query_notes


def test_rejects_unrelated_board():
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.com/career?company=acme", "Acme"))


def rmk_case(company, case="keyword", **native_filters):
    path = FIXTURES / company / case
    meta = json.loads((path / "meta.json").read_text())
    request_body = meta.get("request_body") or meta["request"]["json_body"]
    url = meta.get("board_url") or meta["url"].split("?")[0]
    source = create_adapter(Board(url, company, {"locale": request_body["locale"], **native_filters}))
    return source, meta, (path / "response.json").read_text()


@pytest.mark.parametrize(
    "company,case",
    [
        ("danfoss", "keyword"),
        ("danfoss", "requisition"),
        ("danfoss", "negative"),
        ("port-of-rotterdam", "keyword"),
        ("port-of-rotterdam", "negative"),
    ],
)
def test_rmk_public_api_matches_independent_browser_results(company, case):
    source, meta, body = rmk_case(company, case)
    request = source.search.build(meta["query"])
    assert request.method == "POST"
    assert request.url == meta["captured_url"]
    assert request.json_body == meta["request_body"]
    page = source.search.parse(body, request)
    assert [job.id for job in page.vacancies] == meta["reference_ids"]
    assert [job.url for job in page.vacancies] == meta["independent_job_urls"]
    assert page.total == meta["reference_total"]
    assert bool(page.next_requests) == (page.total > 10)
    assert source.search.parameters["query"] == "native"
    if case == "keyword":
        first = page.vacancies[0]
        assert first.published_at == ("2026-09-04" if company == "danfoss" else "2026-09-30")
        assert first.location == ("Pune, IND" if company == "danfoss" else None)
        assert first.locations == []  # No country/city inference from a display label.


def test_rmk_explicit_location_multiple_facets_and_url_locale():
    source = create_adapter(
        Board(
            "https://jobs.danfoss.com/search/?locale=en_GB",
            "Label",
            {"location": "Nordborg", "jobLocationCountry": ["Denmark", "Sweden"], "filter2": "Sales"},
        )
    )
    request = source.search.build("")
    assert request.method == "POST"
    assert request.json_body["keywords"] == ""
    assert request.json_body["location"] == "Nordborg"
    assert request.json_body["locale"] == "en_GB"
    assert request.json_body["facetFilters"] == {
        "jobLocationCountry": ["Denmark", "Sweden"],
        "filter2": ["Sales"],
    }


def test_rmk_observed_country_filter_and_final_partial_page():
    source, meta, body = rmk_case("danfoss", "country-page-3", jobLocationCountry=["Denmark"])
    built = source.search.build("Engineer")
    assert built.json_body["facetFilters"] == {"jobLocationCountry": ["Denmark"]}
    request = SearchRequest(built.url, "Engineer", method="POST", json_body=meta["request"]["json_body"])
    page = source.search.parse(body, request)
    assert [job.id for job in page.vacancies] == meta["reference_ids"]
    assert page.total == 44
    assert page.next_requests[0].json_body == {**request.json_body, "pageNumber": 3}
    assert page.next_requests[0].query == "Engineer"
    source, meta, body = rmk_case("danfoss", "country-last-page", jobLocationCountry="Denmark")
    request = SearchRequest(built.url, "Engineer", method="POST", json_body=meta["request"]["json_body"])
    page = source.search.parse(body, request)
    assert [job.id for job in page.vacancies] == meta["reference_ids"]
    assert len(page.vacancies) == 4
    assert page.next_requests == ()


@pytest.mark.parametrize("company,locale", [("danfoss", "en_GB"), ("port-of-rotterdam", "nl_NL")])
async def test_rmk_prepare_discovers_public_locale_and_keeps_each_phrase(company, locale):
    meta = json.loads((FIXTURES / company / "keyword/meta.json").read_text())
    source = create_adapter(Board(meta["board_url"], "Unrelated label"))
    fetched = []

    async def fetch(url):
        fetched.append(url)
        return (FIXTURES / company / "bootstrap/response.html").read_text()

    plan = await source.search.prepare(BriefInput(name="RMK", queries=["Engineer", "Architect"]), fetch)
    assert len(fetched) == 1
    assert [request.query for request in plan.requests] == ["Engineer", "Architect"]
    assert [request.json_body["keywords"] for request in plan.requests] == ["Engineer", "Architect"]
    assert all(request.json_body["locale"] == locale for request in plan.requests)
    assert all(request.method == "POST" for request in plan.requests)
    assert plan.application["query"] == "native"


def test_rmk_direct_build_can_discover_locale_without_defaulting_to_english():
    source = create_adapter(Board("https://werkenbij.portofrotterdam.com/search/", "Label"))
    request = source.search.build("Engineer")
    assert request.stage == "preparation"
    page = source.search.parse((FIXTURES / "port-of-rotterdam/bootstrap/response.html").read_text(), request)
    assert page.total is None
    assert not page.vacancies
    assert page.next_requests[0].json_body["locale"] == "nl_NL"
    assert page.next_requests[0].json_body["keywords"] == "Engineer"


@pytest.mark.parametrize(
    "body",
    [
        "{}",
        '{"error":"unavailable"}',
        '{"totalJobs":1}',
        '{"totalJobs":true}',
        '{"totalJobs":1,"jobSearchResult":[]}',
        '{"totalJobs":0,"jobSearchResult":[{}]}',
    ],
)
def test_rmk_malformed_or_incomplete_response_is_not_empty(body):
    source, meta, _ = rmk_case("danfoss")
    with pytest.raises(ParseError):
        source.search.parse(body, source.search.build(meta["query"]))


def test_rmk_bootstrap_must_identify_rmk_configuration():
    source = create_adapter(Board("https://example.com/search/", "Label"))
    with pytest.raises(ParseError):
        source.search.parse('<html lang="en-GB">No jobs found</html>', source.search.build("Engineer"))


def test_rmk_scalar_options_reject_lists_and_unknown_filters():
    with pytest.raises(ValueError):
        create_adapter(Board("https://jobs.danfoss.com/search/", "Label", {"locale": ["en_GB", "de_DE"]}))
    with pytest.raises(ValueError):
        create_adapter(Board("https://jobs.danfoss.com/search/", "Label", {"not_a_filter": "value"}))


def test_rmk_company_label_does_not_control_facts():
    source, meta, body = rmk_case("danfoss")
    renamed = create_adapter(Board(source.search.board.url, "Another label", {"locale": "en_GB"}))
    request = source.search.build(meta["query"])
    assert [v.model_dump(exclude={"company"}) for v in source.search.parse(body, request).vacancies] == [
        v.model_dump(exclude={"company"}) for v in renamed.search.parse(body, request).vacancies
    ]
