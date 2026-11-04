"""Requests must carry selected native filters before any local filtering runs."""

import gzip
import importlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from src.models.brief import BriefInput
from src.parsers.ats.models import Board

FIXTURES = Path(__file__).parents[2] / "fixtures/ats/native_filters"
RECORDED = json.loads((FIXTURES / "cases.json").read_text())


CASES = [
    ("dreamjob", "https://dreamjob.ru/employers/123", "jbfrp[text]"),
    ("icims", "https://careers-example.icims.com/jobs/search", "searchKeyword"),
    ("jazzhr", "https://example.applytojob.com/apply/jobs", "q"),
    ("jobvite", "https://jobs.jobvite.com/example/jobs", "q"),
    ("smartrecruiters", "https://careers.smartrecruiters.com/Example", "q"),
    ("teamtailor", "https://example.teamtailor.com/jobs", "query"),
    ("workable", "https://apply.workable.com/example/", "query"),
]


async def no_fetch(url):
    raise AssertionError(f"This search needs no preparation request: {url}")


@pytest.mark.parametrize("platform,url,parameter", CASES)
async def test_each_brief_phrase_reaches_the_server(platform, url, parameter):
    factory = importlib.import_module(f"src.parsers.ats.{platform}.parser").create_adapter
    source = factory(Board(url, "Example"))
    phrases = ["QA / Test & Automation", "Python"]
    plan = await source.search.prepare(BriefInput(name="Test", queries=phrases), no_fetch)
    assert plan.application["query"] == "native"
    assert [request.query for request in plan.requests] == phrases
    for request, phrase in zip(plan.requests, phrases, strict=True):
        if platform == "workable":
            assert request.method == "POST" and request.json_body == {"query": phrase}
        else:
            assert parse_qs(urlsplit(request.url).query)[parameter] == [phrase]
    assert source.capabilities.query_parameters["query"] == "native"


@pytest.mark.parametrize(
    "platform,url,filters",
    [
        (
            "lever",
            "https://jobs.lever.co/example",
            {"team": ["Engineering", "Product"], "location": "Remote"},
        ),
        ("breezy", "https://example.breezy.hr/", {"location": "London, UK"}),
        (
            "smartrecruiters",
            "https://careers.smartrecruiters.com/Example",
            {"country": "gb", "city": "London"},
        ),
    ],
)
async def test_explicit_native_filters_are_encoded_and_advertised(platform, url, filters):
    factory = importlib.import_module(f"src.parsers.ats.{platform}.parser").create_adapter
    source = factory(Board(url, "Example", native_filters=filters))
    plan = await source.search.prepare(BriefInput(name="Test"), no_fetch)
    params = parse_qs(urlsplit(plan.requests[0].url).query)
    for key, value in filters.items():
        assert params[key] == (value if isinstance(value, list) else [value])
        assert key in source.capabilities.native_filters


def test_unsupported_native_filter_is_rejected_instead_of_silently_ignored():
    from src.parsers.ats.lever.parser import create_adapter

    with pytest.raises(ValueError, match="country"):
        create_adapter(Board("https://jobs.lever.co/example", "Example", native_filters={"country": "US"}))


def test_non_filterable_feed_rejects_explicit_native_filters():
    from src.parsers.ats.ashby.parser import create_adapter

    with pytest.raises(ValueError, match="location"):
        create_adapter(
            Board("https://jobs.ashbyhq.com/example", "Example", native_filters={"location": "US"})
        )


@pytest.mark.parametrize(
    "platform,url,filters",
    [
        ("breezy", "https://brookhill.breezy.hr", {"location": ["Bullard, TX", "Bullard, TX"]}),
        ("smartrecruiters", "https://careers.smartrecruiters.com/Example", {"country": ["gb", "us"]}),
        ("lever", "https://jobs.lever.co/example", {"level": ["Senior", "Junior"]}),
    ],
)
def test_scalar_native_parameters_reject_multiple_values(platform, url, filters):
    factory = importlib.import_module(f"src.parsers.ats.{platform}.parser").create_adapter
    with pytest.raises(ValueError, match="one value"):
        factory(Board(url, "Example", native_filters=filters))


@pytest.mark.parametrize("case", RECORDED, ids=lambda c: f"{c['platform']}-{c['company']}-{c['label']}")
async def test_recorded_native_search_requests_and_results(case):
    factory = importlib.import_module(f"src.parsers.ats.{case['platform']}.parser").create_adapter
    source = factory(Board(case["board_url"], case["company"], native_filters=case["filters"]))
    plan = await source.search.prepare(
        BriefInput(name="Native search", queries=[case["query"]] if case["query"] else []), no_fetch
    )
    request = plan.requests[0]
    for capture in case["pages"]:
        expected = urlsplit(capture["url"])
        actual = urlsplit(request.url)
        assert (actual.netloc, actual.path) == (expected.netloc, expected.path)
        assert request.method == capture["method"]
        active = case["filters"] or {source.search.query_parameter: case["query"]}
        if request.method == "POST":
            assert request.json_body == capture["json_body"]
        else:
            params = parse_qs(actual.query)
            for name, value in active.items():
                assert params[name] == ([value] if isinstance(value, str) else value)
        page = source.search.parse(
            gzip.decompress((FIXTURES / capture["file"]).read_bytes()).decode(), request
        )
        assert {v.id for v in page.vacancies} == set(capture["ids"])
        if case["label"] == "negative":
            assert page.vacancies == [] and page.next_requests == ()
        if page.next_requests:
            request = page.next_requests[0]
    assert page.next_requests == ()


@pytest.mark.parametrize("body_type", ["json", "form"])
def test_post_request_identity_distinguishes_encoding(body_type):
    from src.parsers.interfaces import SearchRequest

    kwargs = {body_type + "_body": {"keywords": "QA"}}
    request = SearchRequest("https://example.org/search", "QA", method="POST", **kwargs)
    other = SearchRequest(
        "https://example.org/search",
        "QA",
        method="POST",
        **{("form_body" if body_type == "json" else "json_body"): {"keywords": "QA"}},
    )
    assert request.identity != other.identity
