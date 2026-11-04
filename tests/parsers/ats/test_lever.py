"""Real saved lever responses; expected vacancy facts reviewed from the source bodies."""

import hashlib
import json
from pathlib import Path

import pytest

from src.parsers.ats.lever.parser import create_adapter
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ats" / "lever"
SAMPLES = [
    (
        "unseen_finom",
        76,
        "2776275b-c47b-4672-8774-288a49a084c2",
        "AI Process Owner — Transaction Monitoring & Customer Lifecycle",
    ),
    ("coinspaid", 6, "3d311307-c6b9-44f8-b238-6392eee5f40f", "Lead Compliance Officer"),
    ("appfollow", 3, "f3e97cf3-9777-4e54-be7a-29525fe67b86", "ASO Product Builder"),
    (
        "airslate",
        4,
        "1fe1bfc3-12bc-482f-8cd6-102651e0a1f8",
        "Automation QE Lead: Quality Architecture & Agentic Testing",
    ),
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


@pytest.mark.parametrize("url", ["https://example.org/jobs", "https://example.org/lever"])
def test_other_platform_url_is_rejected(url):
    with pytest.raises(ValueError):
        create_adapter(Board(url, "Example"))


def test_eu_url_html_sections_and_timestamp():
    source = create_adapter(Board("https://jobs.eu.lever.co/coinspaid", "Example"))
    assert source.search.build("").url == "https://api.eu.lever.co/v0/postings/coinspaid?mode=json"
    _, body = fixture("coinspaid")
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.locations[0].country == "EE"
    assert first.work_formats == ["hybrid"]
    assert first.employment == ["full_time"]
    assert first.published_at.startswith("2026-03-19T")
    assert "<h3>" in first.description_html
    assert "<li>" in first.description_html
    assert first.description and "Coinspaid" in first.description


def test_remote_region_does_not_become_administrative_country():
    url, body = fixture("appfollow")
    source = create_adapter(Board(url, "Example"))
    first = source.search.parse(body, source.search.build("")).vacancies[0]
    assert first.location == "Europe"
    assert first.source_data["country"] == "FI"
    assert first.locations[0].country is None
    assert first.work_formats == ["remote_country"]
    assert first.employment == ["full_time"]
    assert not any(place.worldwide for place in first.locations)
    assert "• Relaunch and evolve AppFollow's ASO product" in first.description
    assert "<ul><li>" in first.description_html


def test_empty_invalid_timestamp_and_sanitized_plain_fallback():
    source = create_adapter(Board("https://jobs.lever.co/demo", "Example"))
    assert source.search.parse("[]", source.search.build("")).total == 0
    job = {
        "id": "1",
        "text": "Engineer",
        "hostedUrl": "https://jobs.lever.co/demo/1",
        "descriptionPlain": "Test <literal>\nNext",
        "lists": [],
        "createdAt": "today",
    }
    with pytest.raises(ParseError):
        source.search.parse(json.dumps([job]), source.search.build(""))
    job.pop("createdAt")
    result = source.search.parse(json.dumps([job]), source.search.build("")).vacancies[0]
    assert result.description == "Test <literal>\nNext"
    assert "&lt;literal&gt;" in result.description_html


def test_unseen_finom_snapshot_fields_reviewed_directly_from_public_response():
    url, body = fixture("unseen_finom")
    source = create_adapter(Board(url, "Finom"))
    first, second, third = source.search.parse(body, source.search.build("")).vacancies[:3]
    assert first.location == "Netherlands"
    assert first.locations[0].country == "NL"
    assert first.work_formats == ["remote_country"]
    assert first.employment == ["full_time"]
    assert first.published_at == "2026-09-22T14:24:42.343000+00:00"
    assert first.url == "https://jobs.eu.lever.co/pnlfin/2776275b-c47b-4672-8774-288a49a084c2"
    assert "About Finom" in first.description
    assert "<ul>" in first.description_html
    assert second.locations[0].country == "ES"
    assert third.locations[0].country == "PL"
    assert first.source_data["categories"]["allLocations"] == ["Netherlands", "Spain", "Poland"]


def test_unseen_finom_capture_provenance_and_body_integrity():
    directory = FIXTURES / "unseen_finom"
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
        "https://jobs.eu.lever.co/unrelated/2776275b-c47b-4672-8774-288a49a084c2",
        "https://jobs.eu.lever.co/pnlfin/different-id",
        "https://credentials@jobs.eu.lever.co/pnlfin/2776275b-c47b-4672-8774-288a49a084c2",
        "http://jobs.eu.lever.co/pnlfin/2776275b-c47b-4672-8774-288a49a084c2",
    ],
)
def test_native_posting_identity_mutations_fail(job_url):
    url, body = fixture("unseen_finom")
    source = create_adapter(Board(url, "Example"))
    payload = json.loads(body)[:1]
    payload[0]["hostedUrl"] = job_url
    with pytest.raises(ParseError):
        source.search.parse(json.dumps(payload), source.search.build(""))


def test_all_explicit_remote_countries_preserved_unknown_labels_not_inferred():
    url, body = fixture("unseen_finom")
    source = create_adapter(Board(url, "Example"))
    payload = json.loads(body)[:1]
    first = source.search.parse(json.dumps(payload), source.search.build("")).vacancies[0]
    assert [place.country for place in first.locations] == ["NL", "ES", "PL"]
    payload[0]["categories"]["allLocations"] = [
        "Netherlands",
        "Europe",
        "Spain",
        "Unknown place",
        "Netherlands",
    ]
    first = source.search.parse(json.dumps(payload), source.search.build("")).vacancies[0]
    assert [place.country for place in first.locations] == ["NL", "ES"]
    assert "Europe" in first.source_data["categories"]["allLocations"]
