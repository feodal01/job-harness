"""Real saved workable responses; expected vacancy facts reviewed from the source bodies."""

import hashlib
import json
from pathlib import Path

import pytest

from src.models.brief import BriefInput
from src.parsers.ats.models import Board
from src.parsers.ats.workable.parser import create_adapter
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "workable"
SAMPLES = [
    ("zeroavia", 11, "A3DDDB975A", "Mechanical Design Engineer"),
    ("joom", 14, "9D01579E21", "Financial Reporting Specialist at Joom"),
    ("homebuddy", 1, "1FC8DB117A", "Join our team"),
]


def fixture(company):
    directory = FIXTURES / company
    meta = json.loads((directory / "meta.json").read_text())
    return meta["captured_url"], (directory / "response.html").read_text()


@pytest.mark.parametrize("company,count,identifier,title", SAMPLES)
def test_saved_board(company, count, identifier, title):
    url, body = fixture(company)
    source = create_adapter(Board(url, company))
    page = source.search.parse(body, source.search.build(""))
    assert len(page.vacancies) == count
    assert page.vacancies[0].id == identifier
    assert page.vacancies[0].title == title
    assert page.vacancies[0].url.startswith("https://")
    assert source.search.parameters["query"] == "native"
    assert source.type == "ats"


@pytest.mark.parametrize("company,count,identifier,title", SAMPLES)
def test_company_label_does_not_change_vacancy_facts(company, count, identifier, title):
    url, body = fixture(company)
    original = create_adapter(Board(url, company))
    renamed = create_adapter(Board(url, "Unrelated Employer"))
    a = original.search.parse(body, original.search.build(""))
    b = renamed.search.parse(body, renamed.search.build(""))
    assert original.id == renamed.id
    assert [v.model_dump(exclude={"company"}) for v in a.vacancies] == [
        v.model_dump(exclude={"company"}) for v in b.vacancies
    ]


@pytest.mark.parametrize("body", ["garbage", "{}", "<html><h1>Access denied</h1></html>"])
def test_unrecognized_response_is_an_error(body):
    url, _ = fixture(SAMPLES[0][0])
    source = create_adapter(Board(url, "Example"))
    with pytest.raises(ParseError):
        source.search.parse(body, source.search.build(""))


@pytest.mark.parametrize("url", ["https://example.org/jobs", "https://example.org/workable"])
def test_other_platform_url_is_rejected(url):
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Example"))


def test_location_markers_and_date():
    url, body = fixture("zeroavia")
    source = create_adapter(Board(url, "Example"))
    page = source.search.parse(body, source.search.build(""))
    first = page.vacancies[0]
    assert first.locations[0].country == "GB"
    assert first.locations[0].city == "Kemble"
    assert first.work_formats == []
    assert first.employment == ["full_time"]
    assert first.published_at == "2026-06-22"
    assert page.vacancies[9].work_formats == ["hybrid"]
    assert first.description is None
    assert first.salary == {}


def test_remote_scope_and_canonical_board_url():
    source = create_adapter(Board("https://apply.workable.com/homebuddy/", "Example"))
    assert source.search.build("").url == "https://apply.workable.com/homebuddy/jobs.md"
    _, body = fixture("homebuddy")
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.locations[0].country == "PL"
    assert first.locations[0].city is None
    assert first.work_formats == ["remote_country"]
    assert first.url == "https://apply.workable.com/homebuddy/j/1FC8DB117A"


def test_table_empty_missing_columns_and_wrong_board_link():
    url, body = fixture("zeroavia")
    source = create_adapter(Board(url, "Example"))
    assert (
        source.search.parse("| Title | Location | Details |\n|---|---|---|", source.search.build("")).total
        == 0
    )
    with pytest.raises(ParseError):
        source.search.parse("| Title | Unexpected |\n|---|---|", source.search.build(""))
    with pytest.raises(ParseError):
        source.search.parse(
            body.replace("/zeroavia/jobs/view/", "/another/jobs/view/"), source.search.build("")
        )


def test_unseen_destinus_capture_provenance_and_body_integrity():
    directory = FIXTURES / "unseen_destinus"
    meta = json.loads((directory / "meta.json").read_text())
    body = (directory / "response.html").read_bytes()
    assert hashlib.sha256(body).hexdigest() == meta["response_sha256"]
    assert meta["http_status"] == 200
    assert meta["parser_sha256_before_capture"] == meta["parser_sha256_after_capture"]
    assert meta["request"]["method"] == "GET"
    assert meta["parser_frozen_at"] <= meta["started_at"] <= meta["completed_at"]
    assert meta["unchanged_parser_result"]["outcome"] == "parse_error"


def test_unseen_large_board_search_directory_is_an_explicit_limit():
    url, body = fixture("unseen_destinus")
    source = create_adapter(Board(url, "Destinus"))
    with pytest.raises(ParseError, match="search directory.*supported jobs table"):
        source.search.parse(body, source.search.build(""))
    renamed = create_adapter(Board(url, "Unrelated employer"))
    with pytest.raises(ParseError, match="complete board enumeration is unavailable"):
        renamed.search.parse(body, renamed.search.build(""))


@pytest.mark.parametrize("company", ["atmio", "curology", "entuitive", "ouai", "stadium"])
def test_saved_native_api_positive_and_explicit_empty(company):
    directory = FIXTURES / "native_api" / company
    meta = json.loads((directory / "meta.json").read_text())
    slug = meta["positive"]["request"]["url"].split("/")[-2]
    source = create_adapter(Board(f"https://apply.workable.com/{slug}", "Renamed"))
    for label in ["positive", "negative"]:
        query = meta[label]["request"]["query"]
        request = source.search.build(query)
        assert request.method == "POST"
        assert request.url == meta[label]["request"]["url"]
        assert request.json_body == {"query": query}
        body = (directory / f"{label}.json").read_bytes()
        assert hashlib.sha256(body).hexdigest() == meta[label]["response_sha256"]
        payload = json.loads(body)
        page = source.search.parse(body.decode(), request)
        assert [v.id for v in page.vacancies] == [job["shortcode"] for job in payload["results"]]
        assert page.total == len(page.vacancies) == payload["total"]
        assert not page.next_requests
        assert all(v.url == f"https://apply.workable.com/{slug}/j/{v.id}" for v in page.vacancies)
    assert page.total == 0


def test_ouai_json_preserves_literal_title_pipes_and_structured_fields():
    directory = FIXTURES / "native_api/ouai"
    source = create_adapter(Board("https://apply.workable.com/theouai", "OUAI"))
    page = source.search.parse((directory / "positive.json").read_text(), source.search.build("Marketing"))
    first, second, third, fourth = page.vacancies
    assert first.title == "Senior Manger, Brand Marketing | Temp"
    assert fourth.title == "Copywriter | Temp"
    assert first.work_formats == ["hybrid"]
    assert second.work_formats == ["remote_country"]
    assert second.employment == ["full_time"]
    assert third.employment == ["contract"]
    assert first.locations[0].country == "US"
    assert first.locations[0].city == "Los Angeles"
    with pytest.raises(ParseError, match="wrong number of columns"):
        source.search.parse((directory / "legacy-malformed.md").read_text(), source.search.build(""))


def test_real_large_api_pages_keep_query_and_provider_cursor_with_overlapping_row():
    directory = FIXTURES / "native_api/destinus_initial"
    source = create_adapter(Board("https://apply.workable.com/destinusgroup", "Destinus"))
    request = source.search.build("engineer")
    first = source.search.parse((directory / "first.json").read_text(), request)
    following = first.next_requests[0]
    assert following.json_body == {
        "query": "engineer",
        "token": json.loads((directory / "first.json").read_text())["nextPage"],
    }
    second = source.search.parse((directory / "second.json").read_text(), following)
    assert first.total == second.total == 108
    assert len(first.vacancies) == len(second.vacancies) == 10
    assert len({v.id for v in first.vacancies + second.vacancies}) == 19
    assert second.next_requests[0].json_body["query"] == "engineer"
    assert second.next_requests[0].json_body["token"] != following.json_body["token"]


@pytest.mark.asyncio
async def test_native_queries_need_no_complete_board_preparation():
    source = create_adapter(Board("https://apply.workable.com/theouai", "OUAI"))

    async def no_fetch(url):
        pytest.fail(f"Native search must not fetch complete board: {url}")

    plan = await source.search.prepare(BriefInput(name="Test", queries=["Marketing", "Designer"]), no_fetch)
    assert [r.json_body for r in plan.requests] == [{"query": "Marketing"}, {"query": "Designer"}]


@pytest.mark.parametrize(
    "mutation", ["missing_results", "bad_total", "no_cursor", "bad_cursor", "no_jobs", "bad_id"]
)
def test_native_api_malformed_pages_fail_instead_of_empty(mutation):
    source = create_adapter(Board("https://apply.workable.com/destinusgroup", "Destinus"))
    payload = json.loads((FIXTURES / "native_api/destinus_initial/first.json").read_text())
    if mutation == "missing_results":
        del payload["results"]
    elif mutation == "bad_total":
        payload["total"] = 0
    elif mutation == "no_cursor":
        del payload["nextPage"]
    elif mutation == "bad_cursor":
        payload["nextPage"] = []
    elif mutation == "no_jobs":
        payload["results"] = []
    else:
        payload["results"][0]["shortcode"] = "../other-board"
    with pytest.raises(ParseError):
        source.search.parse(json.dumps(payload), source.search.build("engineer"))


def test_native_api_repeated_cursor_and_changed_request_scope_fail():
    source = create_adapter(Board("https://apply.workable.com/destinusgroup", "Destinus"))
    body = (FIXTURES / "native_api/destinus_initial/first.json").read_text()
    request = source.search.build("engineer")
    page = source.search.parse(body, request)
    with pytest.raises(ParseError, match="repeats"):
        source.search.parse(body, page.next_requests[0])
    other = create_adapter(Board("https://apply.workable.com/theouai", "OUAI"))
    with pytest.raises(ParseError, match="board or query"):
        other.search.parse(body, request)
    request.json_body["query"] = "different"
    with pytest.raises(ParseError, match="board or query"):
        source.search.parse(body, request)


@pytest.mark.parametrize(
    "company,query,total,pages",
    [
        ("theouai", "Marketing", 4, 1),
        ("destinusgroup", "engineer", 108, 12),
        ("atmio", "Production", 2, 1),
        ("curology", "Manager", 1, 1),
        ("entuitive", "Proposal", 2, 1),
        ("bystadium", "Integration", 5, 1),
    ],
)
def test_actual_api_requests_replay_every_filtered_page_and_explicit_negative(company, query, total, pages):
    directory = FIXTURES / "native_live_api" / company
    source = create_adapter(Board(f"https://apply.workable.com/{company}", "Renamed"))
    for label, term, expected_total, expected_pages in [
        ("positive", query, total, pages),
        ("negative", "zzqnojobgeneralization20261012", 0, 1),
    ]:
        request = source.search.build(term)
        ids = []
        for number in range(1, expected_pages + 1):
            meta = json.loads((directory / label / f"{number}.meta.json").read_text())
            assert request.url == meta["request"]["url"]
            assert request.json_body == meta["request"]["json_body"]
            body = (directory / label / f"{number}.json").read_bytes()
            assert hashlib.sha256(body).hexdigest() == meta["response_sha256"]
            page = source.search.parse(body.decode(), request)
            ids.extend(v.id for v in page.vacancies)
            if number < expected_pages:
                request = page.next_requests[0]
        assert not page.next_requests
        assert len(set(ids)) == page.total == expected_total
