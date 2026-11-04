"""Architecture boundaries and generic structured-data contracts, not live ATS evidence."""

import ast
import importlib
import json
from pathlib import Path

import pytest

from src.models.filters import WorkLocation
from src.models.vacancy import Vacancy
from src.parsers.ats.jsonld import enrich, job_postings
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import country_code, locations

PLATFORMS = (
    "ashby",
    "bamboohr",
    "breezy",
    "comeet",
    "dreamjob",
    "greenhouse",
    "huntflow",
    "icims",
    "jazzhr",
    "jobvite",
    "join",
    "lever",
    "personio",
    "recruitee",
    "smartrecruiters",
    "successfactors",
    "taleo",
    "teamtailor",
    "workable",
    "workday",
    "ycombinator",
)
ROOT = Path(__file__).parents[1] / "src" / "parsers" / "ats"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_each_platform_is_an_independent_importable_parser(platform):
    module = importlib.import_module(f"src.parsers.ats.{platform}.parser")
    assert callable(module.create_adapter)
    assert Path(module.__file__).parent.name == platform


def test_ats_parsers_do_not_import_orchestration_io_detection_or_other_platforms():
    forbidden = (
        "job_harness",
        "src.services",
        "src.db",
        "src.engine",
        "src.auth",
        "src.network",
        "httpx",
        "requests",
        "aiohttp",
        "urllib.request",
        "subprocess",
        "mcp",
    )
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports = [node.module]
            for name in imports:
                assert not any(name == p or name.startswith(p + ".") for p in forbidden), (path, name)
                assert "detector" not in name and "detection" not in name, (path, name)
                if name.startswith("src.parsers.ats."):
                    part = name.split(".")[3]
                    if part in PLATFORMS:
                        assert path.parent.name == part, (path, name)
            if isinstance(node, ast.Name):
                assert node.id not in {"ATS_COMPANY_SOURCE_CONFIGS", "source_descriptor"}, path


def test_known_donor_employer_overrides_are_absent_from_execution_code():
    employers = {
        "coinspaid",
        "outschool",
        "appfollow",
        "jetbrains",
        "airslate",
        "wintermute",
        "truv",
        "termius",
        "wallarm",
        "3commas",
        "tripleten",
        "visionist",
        "keylogic",
    }
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value.casefold() not in employers, (path, node.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/jobs",
        "https://user:password@example.org/jobs",
        "https://localhost/jobs",
        "https://127.0.0.1/jobs",
        "https://[::1]/jobs",
    ],
)
def test_board_rejects_invalid_connection_data(url):
    with pytest.raises(ValueError):
        Board(url, "Any employer")


def test_structured_geography_does_not_guess_cities_or_worldwide_scope():
    assert locations(city="Remote") == []
    assert locations(country="Europe") == []
    assert country_code("Annapolis Junction") is None
    assert country_code("BG") == "BG"
    assert country_code("United Kingdom") == "GB"
    remote = locations(format="remote")[0]
    assert remote.country is None and remote.worldwide is False


def test_generic_jsonld_graph_extracts_only_postings_and_keeps_rich_text():
    markup = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@graph": [
                    {"@type": "Organization", "name": "Example"},
                    {
                        "@type": ["Thing", "JobPosting"],
                        "description": "<h2>Tasks</h2><ul><li>Test systems</li></ul>",
                        "datePosted": "2026-10-01",
                        "employmentType": "FULL_TIME",
                    },
                ]
            }
        )
        + "</script>"
    )
    postings = job_postings(markup)
    assert len(postings) == 1
    result = enrich(Vacancy(id="1", title="QA", url="https://example.org/jobs/1"), postings[0])
    assert result.published_at == "2026-10-01" and result.employment == ["full_time"]
    assert "<h2>Tasks</h2>" in result.description_html and "Test systems" in result.description


def test_jsonld_remote_does_not_turn_office_address_into_eligibility():
    original = Vacancy(id="1", title="QA", url="https://example.org/jobs/1")
    result = enrich(
        original,
        {
            "jobLocationType": "TELECOMMUTE",
            "jobLocation": {"address": {"addressCountry": "US", "addressLocality": "Remote"}},
            "applicantLocationRequirements": [
                {"@type": "Country", "name": "Germany"},
                {"@type": "Country", "name": "Germany"},
            ],
        },
    )
    assert len(result.locations) == 2
    assert result.locations[0].country == "US" and result.locations[0].format is None
    assert result.locations[0].city is None
    assert result.locations[1].country == "DE" and result.locations[1].format == "remote_country"
    assert not any(place.worldwide for place in result.locations)
    assert not original.locations


def test_jsonld_keeps_existing_remote_eligibility_when_adding_office_address():
    original = Vacancy(
        id="1",
        title="QA",
        url="https://example.org/jobs/1",
        work_formats=["remote_worldwide"],
        locations=[WorkLocation(country="DE", format="remote_country")],
    )
    result = enrich(
        original,
        {
            "jobLocationType": "TELECOMMUTE",
            "jobLocation": {"address": {"addressCountry": "US", "addressLocality": "Boston"}},
        },
    )
    assert result.work_formats == ["remote_worldwide"]
    assert result.locations[0] == original.locations[0]
    assert result.locations[1].country == "US" and result.locations[1].format is None
