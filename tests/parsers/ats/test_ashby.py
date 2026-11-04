"""Real saved ashby responses; expected vacancy facts reviewed from the source bodies."""

import hashlib
import json
from pathlib import Path

import pytest

from src.parsers.ats.ashby.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "ashby"
SAMPLES = [
    ("unseen_apron", 1, "dfabb0eb-c00f-4f3e-a162-c42b174e79bf", "Senior Software Engineer | Backend"),
    ("3commas", 6, "36d7fc60-43d6-44e8-95e8-769891b8e54c", "Senior Product Manager, Trading"),
    ("datafold", 3, "21cad7b8-546a-40a1-9a37-cde266201821", "Forward Deployed Data Engineer"),
    ("luminai", 11, "2384bdd6-9a80-48b3-99b6-774cdc62400f", "Head of Talent"),
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
    assert source.search.parameters["query"] == "ignored"
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


@pytest.mark.parametrize("url", ["https://example.org/jobs", "https://example.org/ashby"])
def test_other_platform_url_is_rejected(url):
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Example"))


def test_explicit_locations_and_rich_description():
    url, body = fixture("3commas")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.location == "Spain; Portugal; Cyprus; Tallinn; Poland"
    assert first.locations[0].country == "ES"
    assert first.locations[0].city == "Barcelona"
    assert {place.country for place in first.locations} >= {"ES", "PT", "CY", "PL"}
    assert first.work_formats == ["remote_country"]
    assert first.employment == ["full_time"]
    assert first.published_at == "2026-05-27T08:24:20.391+00:00"
    assert "Role Overview" in first.description
    assert "<h1>" in first.description_html
    assert "<ul>" in first.description_html
    assert "style=" not in first.description_html
    assert not first.source_data.get("salary")


def test_unlisted_jobs_excluded_and_unknown_format_stays_unknown():
    url, body = fixture("luminai")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.work_formats == []
    payload = json.loads(body)
    payload["jobs"] = [dict(payload["jobs"][0], isListed=False)]
    assert source.search.parse(json.dumps(payload), source.search.build("")).vacancies == []


def test_native_url_and_empty_feed():
    source = create_adapter(Board("https://jobs.ashbyhq.com/datafold/", "Any label"))
    assert source.search.build("").url == "https://api.ashbyhq.com/posting-api/job-board/datafold"
    assert source.search.parse('{"jobs": []}', source.search.build("")).total == 0
    with pytest.raises(ParseError):
        source.search.parse('{"jobs": [null]}', source.search.build(""))


def test_unseen_apron_snapshot_fields_reviewed_directly_from_public_response():
    url, body = fixture("unseen_apron")
    source = create_adapter(Board(url, "Apron"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.locations[0].country == "GB"
    assert first.locations[0].city == "London"
    assert first.location == "London"
    assert first.work_formats == ["hybrid"]
    assert first.published_at == "2026-10-07T12:41:19.526+00:00"
    assert first.employment == ["full_time"]
    assert "About Apron" in first.description
    assert "<ul>" in first.description_html


def test_unseen_apron_capture_provenance_and_body_integrity():
    directory = FIXTURES / "unseen_apron"
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
        "https://jobs.ashbyhq.com/unrelated/dfabb0eb-c00f-4f3e-a162-c42b174e79bf",
        "https://jobs.ashbyhq.com/apron/different-id",
        "https://credentials@jobs.ashbyhq.com/apron/dfabb0eb-c00f-4f3e-a162-c42b174e79bf",
        "http://jobs.ashbyhq.com/apron/dfabb0eb-c00f-4f3e-a162-c42b174e79bf",
    ],
)
def test_native_job_identity_mutations_fail(job_url):
    url, body = fixture("unseen_apron")
    source = create_adapter(Board(url, "Example"))
    payload = json.loads(body)
    payload["jobs"][0]["jobUrl"] = job_url
    with pytest.raises(ParseError):
        source.search.parse(json.dumps(payload), source.search.build(""))
