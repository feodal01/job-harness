"""Real saved greenhouse responses; expected vacancy facts reviewed from the source bodies."""

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.models.brief import BriefInput
from src.parsers.ats.greenhouse.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "greenhouse"
SAMPLES = [
    ("unseen_novacredit", 8, "4301553009", "Campaign Marketing Lead"),
    ("jetbrains", 4, "4881348101", "Account Manager (China)"),
    ("outschool", 5, "4690260006", "Director of Data"),
    ("ahrefs", 8, "6005637004", "Events Marketing Executive"),
]


def fixture(company):
    directory = FIXTURES / company
    meta = json.loads((directory / "meta.json").read_text())
    return meta["captured_url"], (directory / "response.json").read_text()


@pytest.mark.parametrize("company,count,identifier,title", SAMPLES)
def test_saved_board(company, count, identifier, title):
    url, body = fixture(company)
    source = create_adapter(Board(url, company))
    page = source.search.parse(body, source.search.build("QA"))
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


@pytest.mark.parametrize("url", ["https://example.org/jobs", "https://example.org/greenhouse"])
def test_other_platform_url_is_rejected(url):
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Example"))


def test_decodes_html_and_never_derives_office_from_a_city():
    url, body = fixture("jetbrains")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.location == "Shanghai, China"
    assert first.work_formats == []
    assert first.description.startswith("At JetBrains")
    assert "<ul>" in first.description_html
    assert "&lt;p&gt;" not in first.description_html
    assert first.published_at == "2026-06-22T05:47:19-04:00"
    payload = json.loads(body)
    payload["jobs"] = [payload["jobs"][0]]
    payload["jobs"][0].pop("first_published")
    assert source.search.parse(json.dumps(payload), source.search.build("")).vacancies[0].published_at is None


def test_remote_country_scope_not_guessed_from_prose():
    url, body = fixture("outschool")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.location == "Remote (U.S. & Canada)"
    assert first.work_formats == ["remote_country"]
    assert first.locations[0].country is None
    assert not first.locations[0].worldwide


def test_native_url_and_empty_feed():
    source = create_adapter(Board("https://job-boards.eu.greenhouse.io/jetbrains", "Example"))
    assert (
        source.search.build("").url
        == "https://boards-api.greenhouse.io/v1/boards/jetbrains/jobs?content=true"
    )
    assert source.search.parse('{"jobs": [], "meta": {"total": 0}}', source.search.build("")).total == 0
    with pytest.raises(ParseError):
        source.search.parse('{"jobs": [{}]}', source.search.build(""))


def test_unseen_novacredit_snapshot_fields_reviewed_directly_from_public_response():
    url, body = fixture("unseen_novacredit")
    source = create_adapter(Board(url, "Nova Credit"))
    first, second, third = source.search.parse(body, source.search.build("")).vacancies[:3]
    assert first.location == "New York, NY"
    assert first.work_formats == []
    assert first.published_at == "2026-06-30T16:00:37-04:00"
    assert first.url == "https://job-boards.greenhouse.io/novacredit/jobs/4301553009"
    assert "WHO WE ARE" in first.description
    assert "<p>" in first.description_html
    assert (second.id, second.title) == ("4367833009", "Senior Deployment Lead")
    assert (third.id, third.title) == ("4138967009", "Senior Product Manager")


def test_unseen_novacredit_capture_provenance_and_body_integrity():
    directory = FIXTURES / "unseen_novacredit"
    meta = json.loads((directory / "meta.json").read_text())
    body = (directory / "response.json").read_bytes()
    assert hashlib.sha256(body).hexdigest() == meta["response_sha256"]
    assert meta["http_status"] == 200
    assert meta["parser_sha256_before_capture"] == meta["parser_sha256_after_capture"]
    assert meta["request"]["method"] == "GET"
    assert meta["parser_frozen_at"] <= meta["started_at"] <= meta["completed_at"]
    assert meta["unchanged_parser_result"]["outcome"] == "success"


@pytest.mark.parametrize(
    "job_url",
    [
        "https://job-boards.greenhouse.io/unrelated/jobs/4301553009",
        "https://job-boards.greenhouse.io/novacredit/jobs/different-id",
        "https://credentials@job-boards.greenhouse.io/novacredit/jobs/4301553009",
        "http://job-boards.greenhouse.io/novacredit/jobs/4301553009",
    ],
)
def test_native_job_identity_mutations_fail(job_url):
    url, body = fixture("unseen_novacredit")
    source = create_adapter(Board(url, "Example"))
    payload = json.loads(body)
    payload["jobs"] = [payload["jobs"][0]]
    payload["jobs"][0]["absolute_url"] = job_url
    with pytest.raises(ParseError):
        source.search.parse(json.dumps(payload), source.search.build(""))


def test_recorded_custom_career_url_binds_matching_greenhouse_id():
    url, body = fixture("lokalise")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.id == "7768246003"
    assert first.title == "Country Manager, North America"
    assert first.url == "https://lokalise.com/job?gh_jid=7768246003"
    payload = json.loads(body)
    payload["jobs"] = [payload["jobs"][0]]
    payload["jobs"][0]["absolute_url"] = "https://lokalise.com/job?gh_jid=different-id"
    with pytest.raises(ParseError, match="matching gh_jid"):
        source.search.parse(json.dumps(payload), source.search.build(""))


def test_unverified_regional_api_variant_is_rejected():
    with pytest.raises(ValueError):
        create_adapter(Board("https://boards-api.eu.greenhouse.io/v1/boards/demo/jobs", "Example"))


def test_source_authoritative_custom_https_url_without_public_id_is_preserved():
    url, body = fixture("unseen_novacredit")
    source = create_adapter(Board(url, "Example"))
    payload = json.loads(body)
    payload["jobs"] = [payload["jobs"][0]]
    payload["jobs"][0]["absolute_url"] = "https://careers.example.org/roles/opaque-engineering-slug"
    first = source.search.parse(json.dumps(payload), source.search.build("")).vacancies[0]
    assert first.id == "4301553009"
    assert first.url == "https://careers.example.org/roles/opaque-engineering-slug"


@pytest.mark.parametrize("company", ["attaintalent", "atwellgroup", "dept", "inthepocket", "takealotcom"])
def test_saved_native_keyword_positive_and_explicit_empty(company):
    directory = FIXTURES / "native_filters" / company
    meta = json.loads((directory / "meta.json").read_text())
    source = create_adapter(Board(f"https://job-boards.greenhouse.io/{company}", "Renamed"))
    for label in ["positive", "negative"]:
        body = (directory / meta[label]["file"]).read_text()
        payload = json.loads(body)
        query = payload["keyword"]
        request = source.search.build(query)
        assert urlsplit(request.url).path == f"/{company}"
        assert parse_qs(urlsplit(request.url).query) == {"keyword": [query], "_data": ["routes/$url_token"]}
        page = source.search.parse(body, request)
        assert [v.id for v in page.vacancies] == [str(v["id"]) for v in payload["jobPosts"]["data"]]
        assert page.total == payload["jobPosts"]["total"]
        assert not page.next_requests
        assert hashlib.sha256(body.encode()).hexdigest() == meta[label]["response_sha256"]
    assert page.vacancies == []


@pytest.mark.asyncio
async def test_native_keyword_prepare_issues_each_query_without_feed_fetch():
    source = create_adapter(Board("https://job-boards.greenhouse.io/attaintalent", "Example"))

    async def no_fetch(url):
        pytest.fail(f"Native keyword must not download complete feed: {url}")

    plan = await source.search.prepare(BriefInput(name="Test", queries=["Director", "Engineer"]), no_fetch)
    assert [request.query for request in plan.requests] == ["Director", "Engineer"]
    assert all("keyword=" in request.url for request in plan.requests)


def test_native_keyword_pagination_preserves_query_and_rejects_scope_mismatch():
    source = create_adapter(Board("https://job-boards.greenhouse.io/attaintalent", "Example"))
    payload = json.loads((FIXTURES / "native_filters/attaintalent/positive.json").read_text())
    query = payload["keyword"]
    request = source.search.build(query)
    payload["jobPosts"].update(total=2, total_pages=2)
    page = source.search.parse(json.dumps(payload), request)
    assert parse_qs(urlsplit(page.next_requests[0].url).query) == {
        "keyword": [query],
        "_data": ["routes/$url_token"],
        "page": ["2"],
    }
    payload["urlToken"] = "unrelated"
    with pytest.raises(ParseError, match="board"):
        source.search.parse(json.dumps(payload), request)


@pytest.mark.parametrize("company,query,total", [("atwellgroup", "Engineer", 94), ("dept", "Consultant", 25)])
def test_live_native_capture_replays_all_filtered_pages_and_individual_detail(company, query, total):
    directory = FIXTURES / "native_live" / company
    source = create_adapter(Board(f"https://job-boards.greenhouse.io/{company}", "Example"))
    request = source.search.build(query)
    ids = []
    number = 0
    first = None
    while request:
        number += 1
        meta = json.loads((directory / f"positive-{number}.meta.json").read_text())
        assert request.url == meta["request"]["url"]
        body = (directory / f"positive-{number}.json").read_bytes()
        assert hashlib.sha256(body).hexdigest() == meta["response_sha256"]
        page = source.search.parse(body.decode(), request)
        first = first or page.vacancies[0]
        ids.extend(v.id for v in page.vacancies)
        request = next(iter(page.next_requests), None)
    assert len(ids) == len(set(ids)) == page.total == total
    assert source.vacancy_details.url(first).endswith(f"/{first.id}?content=true")
    result = source.vacancy_details.parse((directory / "detail.json").read_text(), first)
    assert result.id == first.id and result.description and result.description_html
    assert result.url == first.url
    malformed = json.loads((directory / "detail.json").read_text())
    malformed["id"] = "different"
    with pytest.raises(ParseError, match="id does not match"):
        source.vacancy_details.parse(json.dumps(malformed), first)


@pytest.mark.parametrize("mutation", ["missing_data", "bad_count", "wrong_keyword", "wrong_page"])
def test_native_keyword_malformed_response_never_becomes_empty(mutation):
    source = create_adapter(Board("https://job-boards.greenhouse.io/attaintalent", "Example"))
    payload = json.loads((FIXTURES / "native_filters/attaintalent/positive.json").read_text())
    request = source.search.build(payload["keyword"])
    if mutation == "missing_data":
        del payload["jobPosts"]["data"]
    elif mutation == "bad_count":
        payload["jobPosts"]["count"] = 0
    elif mutation == "wrong_keyword":
        payload["keyword"] = "different"
    else:
        payload["jobPosts"].update(page=2, total_pages=2)
    with pytest.raises(ParseError):
        source.search.parse(json.dumps(payload), request)
