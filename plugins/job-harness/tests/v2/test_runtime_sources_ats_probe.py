from __future__ import annotations

import json
import unittest
from pathlib import Path

from job_harness.v2.contracts import SourceFetchRequest, SourceResponseArtifact
from job_harness.v2.runtime import fetch_ats_company_listings
from job_harness.v2.runtime.sources.companies.ats import detect_ats_company_config

_FIXTURES = Path(__file__).parent / "fixtures" / "scrapers"


class MappingFetcher:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping
        self.calls: list[SourceFetchRequest] = []

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        self.calls.append(request)
        try:
            body = self._mapping[request.url]
        except KeyError as exc:
            raise AssertionError(f"unexpected fixture URL: {request.url}") from exc
        return SourceResponseArtifact(
            source_id=request.source_id,
            url=request.url,
            media_type="application/json" if body.lstrip().startswith(("{", "[")) else "text/html",
            body=body,
        )


class AtsProbeTest(unittest.IsolatedAsyncioTestCase):
    def test_detect_greenhouse_public_url_builds_api_board_url(self) -> None:
        config = detect_ats_company_config(
            "https://job-boards.greenhouse.io/airbnb",
            company="Airbnb",
            source_id="adhoc:airbnb",
        )

        self.assertEqual("adhoc:airbnb", config.source_id)
        self.assertEqual("Airbnb", config.company)
        self.assertEqual("greenhouse", config.platform)
        self.assertEqual(
            "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs?content=true",
            config.board_url,
        )
        self.assertEqual("https://job-boards.greenhouse.io/airbnb", config.career_url)

    async def test_fetch_ats_company_listings_detects_and_parses_new_greenhouse_company(self) -> None:
        board_url = "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs?content=true"
        fetcher = MappingFetcher(
            {
                board_url: json.dumps(
                    {
                        "jobs": [
                            {
                                "id": 123,
                                "title": "QA Engineer",
                                "absolute_url": "https://careers.airbnb.com/positions/123",
                                "location": {"name": "Remote, United States"},
                                "content": "<p>Own quality for a product area.</p>",
                                "departments": [{"name": "Engineering"}],
                                "offices": [],
                            }
                        ],
                        "meta": {"total": 1},
                    }
                )
            }
        )

        result = await fetch_ats_company_listings(
            "https://job-boards.greenhouse.io/airbnb",
            company="Airbnb",
            source_id="adhoc:airbnb",
            fetcher=fetcher,
        )

        self.assertEqual("greenhouse", result.config.platform)
        self.assertEqual(1, result.pages_visited)
        self.assertFalse(result.limit_reached)
        self.assertEqual(1, len(result.listings))
        self.assertEqual("QA Engineer", result.listings[0].title)
        self.assertEqual("adhoc:airbnb", result.listings[0].source)
        self.assertEqual([board_url], [call.url for call in fetcher.calls])

    async def test_fetch_ats_company_listings_follows_ats_pagination(self) -> None:
        initial_url = "https://jobs.jobvite.com/visionist"
        next_url = "https://jobs.jobvite.com/visionist/search?c=Software+Engineering&p=0"
        fetcher = MappingFetcher(
            {
                initial_url: (
                    _FIXTURES / "career_visionist" / "success" / "response.html"
                ).read_text(encoding="utf-8"),
                next_url: (
                    _FIXTURES
                    / "career_visionist"
                    / "pagination_software_engineering"
                    / "response.html"
                ).read_text(encoding="utf-8"),
            }
        )

        result = await fetch_ats_company_listings(
            initial_url,
            company="Visionist",
            source_id="adhoc:visionist",
            source_limit=100,
            fetcher=fetcher,
        )

        self.assertEqual("jobvite", result.config.platform)
        self.assertEqual(2, result.pages_visited)
        self.assertFalse(result.limit_reached)
        self.assertGreater(len(result.listings), 1)
        self.assertEqual([initial_url, next_url], [call.url for call in fetcher.calls])

    async def test_fetch_ats_company_listings_follows_smartrecruiters_offset_pagination(self) -> None:
        initial_url = "https://api.smartrecruiters.com/v1/companies/NielsenIQ/postings?limit=100"
        next_url = "https://api.smartrecruiters.com/v1/companies/NielsenIQ/postings?limit=100&offset=2"
        fetcher = MappingFetcher(
            {
                initial_url: json.dumps(
                    {
                        "limit": 100,
                        "offset": 0,
                        "totalFound": 3,
                        "content": [
                            {
                                "id": "job-1",
                                "name": "QA Engineer",
                                "company": {"identifier": "NielsenIQ", "name": "NielsenIQ"},
                                "location": {"fullLocation": "Chicago, IL, United States"},
                            },
                            {
                                "id": "job-2",
                                "name": "Backend Engineer",
                                "company": {"identifier": "NielsenIQ", "name": "NielsenIQ"},
                                "location": {"fullLocation": "Warsaw, Poland"},
                            },
                        ],
                    }
                ),
                next_url: json.dumps(
                    {
                        "limit": 100,
                        "offset": 2,
                        "totalFound": 3,
                        "content": [
                            {
                                "id": "job-3",
                                "name": "Data Engineer",
                                "company": {"identifier": "NielsenIQ", "name": "NielsenIQ"},
                                "location": {"fullLocation": "Madrid, Spain"},
                            }
                        ],
                    }
                ),
            }
        )

        result = await fetch_ats_company_listings(
            "https://jobs.smartrecruiters.com/NielsenIQ",
            company="NielsenIQ",
            source_id="adhoc:nielseniq",
            source_limit=10,
            fetcher=fetcher,
        )

        self.assertEqual("smartrecruiters", result.config.platform)
        self.assertEqual(2, result.pages_visited)
        self.assertFalse(result.limit_reached)
        self.assertEqual(
            ["job-1", "job-2", "job-3"],
            [listing.source_listing_id for listing in result.listings],
        )
        self.assertEqual([initial_url, next_url], [call.url for call in fetcher.calls])

    def test_detect_unsupported_url_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported ATS URL pattern"):
            detect_ats_company_config("https://example.com/careers")
