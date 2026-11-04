import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.models.brief import BriefInput
from src.parsers.ats.models import Board
from src.parsers.ats.taleo.parser import create_adapter
from src.parsers.interfaces import ParseError, SearchRequest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "taleo"


def board_case(company, kind="success"):
    root = FIXTURES / company
    url = json.loads((root / "success" / "meta.json").read_text())["captured_url"]
    path = root / kind
    meta = json.loads((path / "meta.json").read_text())
    return create_adapter(Board(url, company)), (path / "response.html").read_text(), meta["captured_url"]


@pytest.mark.parametrize(
    "company,uid,title,location",
    [
        ("keylogic", "2740", "Data Engineer, Databricks", "Remote"),
        ("navstar", "977", "Automated Test Framework SWE, Senior", "Annapolis Junction, MD"),
        (
            "aurora-flight-sciences",
            "3798",
            "Autonomous Vehicle Software Engineer",
            "Manassas, VA",
        ),
        ("mediacom", "19708", "Account Executive, OnMedia", "Cedar Rapids IA OnMedia"),
    ],
)
def test_real_boards(company, uid, title, location):
    source, body, url = board_case(company)
    page = source.search.parse(body, SearchRequest(url, ""))
    assert len(page.vacancies) == 10
    first = page.vacancies[0]
    assert (first.id, first.title, first.location) == (uid, title, location)
    assert parse_qs(urlsplit(first.url).query)["rid"] == [uid]
    (next_request,) = page.next_requests
    original, next_query = parse_qs(urlsplit(url).query), parse_qs(urlsplit(next_request.url).query)
    assert next_query["rowFrom"] == ["10"]
    assert (next_query["org"], next_query["cws"]) == (original["org"], original["cws"])
    assert first.work_formats == (["remote_country"] if company == "keylogic" else [])
    if company == "mediacom":
        assert "Cedar Rapids IA OnMedia" in first.source_data["fields"]
        assert first.locations == []


@pytest.mark.parametrize(
    "company,kind,count,next_offset",
    [
        ("keylogic", "pagination_row_10", 10, 20),
        ("keylogic", "pagination_row_20", 1, None),
        ("navstar", "pagination_row_10", 10, 20),
        ("navstar", "pagination_row_20", 10, 30),
        ("navstar", "pagination_row_30", 1, None),
        ("aurora-flight-sciences", "pagination_row_10", 3, None),
        ("mediacom", "pagination_terminal", 0, None),
    ],
)
def test_real_pagination(company, kind, count, next_offset):
    source, body, url = board_case(company, kind)
    page = source.search.parse(body, SearchRequest(url, "QA"))
    assert len(page.vacancies) == count
    if next_offset is None:
        assert not page.next_requests
    else:
        assert parse_qs(urlsplit(page.next_requests[0].url).query)["rowFrom"] == [str(next_offset)]
        assert page.next_requests[0].query == "QA"
    if company == "keylogic" and kind == "pagination_row_20":
        assert page.vacancies[0].source_data["fields"][0] == "Annapolis Junction"
        assert page.vacancies[0].location == "Annapolis Junction"
        assert page.vacancies[0].locations == []  # No city/country structure is declared.


def test_company_label_does_not_control_facts():
    source, body, url = board_case("navstar")
    renamed = create_adapter(Board(source.search.board.url, "Another label"))
    assert [
        v.model_dump(exclude={"company"}) for v in source.search.parse(body, SearchRequest(url, "")).vacancies
    ] == [
        v.model_dump(exclude={"company"})
        for v in renamed.search.parse(body, SearchRequest(url, "")).vacancies
    ]


def test_empty_malformed_and_nonadvancing_pagination():
    source, _, url = board_case("keylogic")
    assert source.search.parse("<p>No jobs found</p>", SearchRequest(url, "")).vacancies == []
    for body in ["<html>broken</html>", '<a class="jscroll-next" href="?rowFrom=0">next</a>']:
        with pytest.raises(ParseError):
            source.search.parse(body, SearchRequest(url, ""))


def test_rejects_unrelated_board():
    with pytest.raises(ValueError):
        create_adapter(Board("https://example.com/ats/careers/v2/searchResults?org=acme&cws=1", "Acme"))


def test_search_form_url_becomes_listing_url():
    source = create_adapter(
        Board(
            "https://phg.tbe.taleo.net/phg02/ats/careers/v2/jobSearch?act=redirectCwsV2&org=EXAMPLE&cws=37",
            "Label",
        )
    )
    request = source.search.build("")
    assert urlsplit(request.url).path.endswith("/searchResults")
    assert parse_qs(urlsplit(request.url).query) == {"org": ["EXAMPLE"], "cws": ["37"]}


def native_case(company):
    root = FIXTURES / "native" / company
    meta = json.loads((root / "meta.json").read_text())
    return root, meta


@pytest.mark.parametrize(
    "company,query,ids,version",
    [
        ("three-saints-bay", "3408", ["3408"], "3"),
        ("nurses-and-more", "Behavioral", ["623", "591", "507"], "0"),
        ("electronics-for-imaging", "Onboarding", ["9024"], "0"),
        ("american-states-water", "Administrative", ["2612", "2509", "2531"], "0"),
    ],
)
async def test_native_keywords_are_discovered_and_posted(company, query, ids, version):
    root, meta = native_case(company)
    source = create_adapter(Board(meta["form_url"], company))

    async def fetch(url):
        assert urlsplit(url)._replace(query="") == urlsplit(meta["form_url"])._replace(query="")
        assert parse_qs(urlsplit(url).query) == parse_qs(urlsplit(meta["form_url"]).query)
        return (root / "search-form.html").read_text()

    plan = await source.search.prepare(
        BriefInput(name="Native", queries=[query, "zzqnojobgeneralization20261012"]), fetch
    )
    assert plan.application["query"] == "native"
    assert source.capabilities.query_parameters["query"] == "native"
    positive, negative = plan.requests
    assert positive.method == negative.method == "POST"
    assert positive.query == query
    assert positive.form_body["keywords"] == query
    assert positive.form_body["WebVersion"] == version
    assert positive.form_body["act"] == "search"
    assert positive.url == meta["responses"]["narrow"]["request"]["url"]
    page = source.search.parse((root / "narrow.html").read_text(), positive)
    assert [vacancy.id for vacancy in page.vacancies] == ids
    assert not page.next_requests
    empty = source.search.parse((root / "negative.html").read_text(), negative)
    assert empty.total == 0 and not empty.vacancies and not empty.next_requests


async def test_filtered_pagination_reposts_the_same_form():
    root, meta = native_case("american-states-water")
    source = create_adapter(Board(meta["form_url"], meta["company"]))

    async def fetch(url):
        return (root / "search-form.html").read_text()

    plan = await source.search.prepare(
        BriefInput(name="Native", queries=["Administrative Support Associate I (11NC.26)"]), fetch
    )
    request = plan.requests[0]
    page = source.search.parse((root / "positive.html").read_text(), request)
    assert page.total == 20
    (next_request,) = page.next_requests
    assert next_request.method == "POST"
    assert next_request.query == request.query
    assert next_request.form_body == request.form_body
    assert parse_qs(urlsplit(next_request.url).query) == {"org": ["ASWC"], "cws": ["52"], "rowFrom": ["10"]}


async def test_board_without_keywords_does_not_invent_a_search_control():
    root, meta = native_case("institute-for-defense-analyses")
    source = create_adapter(Board(meta["form_url"], meta["company"]))

    async def fetch(url):
        return (root / "search-form.html").read_text()

    plan = await source.search.prepare(BriefInput(name="IDA", queries=["Research", "zzqnojob"]), fetch)
    assert plan.application["query"] == "ignored"
    assert source.capabilities.query_parameters["query"] == "ignored"
    assert source.capabilities.native_filters == ["CUSTOM_1066", "CUSTOM_1218", "CUSTOM_1240", "location"]
    assert plan.notes
    (request,) = plan.requests
    assert request.method == "POST"
    assert request.query == ""
    assert "keywords" not in request.form_body


async def test_missing_search_form_is_an_explicit_parse_failure():
    source, _, _ = board_case("keylogic")

    async def fetch(url):
        return "<html>No form</html>"

    with pytest.raises(ParseError, match="form"):
        await source.search.prepare(BriefInput(name="Native", queries=["Engineer"]), fetch)


@pytest.mark.parametrize(
    "company,filters",
    [
        ("institute-for-defense-analyses", {"location": "35"}),
        ("institute-for-defense-analyses", {"location": ["35", "36"], "CUSTOM_1066": "844"}),
        ("three-saints-bay", {"state": ["Virginia", "Alabama"], "CUSTOM_1254": "976"}),
        ("nurses-and-more", {"title": "Nurse", "region": "37"}),
    ],
)
async def test_exact_board_options_are_sent_as_native_form_filters(company, filters):
    root, meta = native_case(company)
    source = create_adapter(Board(meta["form_url"], meta["company"], native_filters=filters))

    async def fetch(url):
        return (root / "search-form.html").read_text()

    plan = await source.search.prepare(BriefInput(name="Native"), fetch)
    request = plan.requests[0]
    assert request.method == "POST"
    for name, value in filters.items():
        assert request.form_body[name] == value
    if filters == {"location": "35"}:
        page = source.search.parse((root / "positive.html").read_text(), request)
        assert page.total == 28
        assert page.next_requests[0].form_body["location"] == "35"
        assert "keywords" not in page.next_requests[0].form_body


@pytest.mark.parametrize("filters", [{"keywords": "Research"}, {"location": "Alexandria, VA"}])
async def test_unavailable_controls_or_non_native_option_values_are_rejected(filters):
    root, meta = native_case("institute-for-defense-analyses")
    source = create_adapter(Board(meta["form_url"], meta["company"], native_filters=filters))

    async def fetch(url):
        return (root / "search-form.html").read_text()

    with pytest.raises(ValueError, match="(?:available|option)"):
        await source.search.prepare(BriefInput(name="Native"), fetch)


@pytest.mark.parametrize("present", [True, False])
async def test_unseen_custom_filter_requires_discovery_on_this_board(present):
    root, meta = native_case("institute-for-defense-analyses")
    source = create_adapter(
        Board(meta["form_url"], meta["company"], native_filters={"CUSTOM_9999": ["123", "456"]})
    )
    body = (root / "search-form.html").read_text()
    if present:
        body = body.replace(
            "</form>",
            '<input type="checkbox" name="CUSTOM_9999" value="123">'
            '<input type="checkbox" name="CUSTOM_9999" value="456"></form>',
        )

    async def fetch(url):
        return body

    if present:
        plan = await source.search.prepare(BriefInput(name="New custom field"), fetch)
        assert plan.requests[0].form_body["CUSTOM_9999"] == ["123", "456"]
        assert "CUSTOM_9999" in source.capabilities.native_filters
    else:
        with pytest.raises(ValueError, match="CUSTOM_9999 is not available"):
            await source.search.prepare(BriefInput(name="Missing custom field"), fetch)


@pytest.mark.parametrize("name", ["CUSTOM_other", "CUSTOM_9999_total", "CUSTOM_", "arbitrary"])
def test_malformed_custom_filter_names_are_rejected_before_fetch(name):
    _, meta = native_case("institute-for-defense-analyses")
    with pytest.raises(ValueError, match="Unsupported native filters"):
        create_adapter(Board(meta["form_url"], meta["company"], native_filters={name: "123"}))
