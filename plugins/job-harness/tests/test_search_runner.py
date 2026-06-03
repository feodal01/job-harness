from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from job_harness.cli import cmd_wide_search
from job_harness.models import JobListing, SearchParams, SearchResults
from job_harness.search_runner import dedupe_listings, execute_search


class _NoBrowserClass:
    display_name = "No Browser"
    requires_browser = False
    detail_requires_browser = False
    countries = ()

    @classmethod
    def supports_country(cls, country):
        return True


class _BrowserClass:
    display_name = "Browser"
    requires_browser = True
    detail_requires_browser = True
    countries = ()

    @classmethod
    def supports_country(cls, country):
        return True


class _OkScraper:
    display_name = "OK"
    name = "ok"
    timed_out = False
    runtime_error = None

    def __init__(self, context, max_results=20, debug=False, timeout_ms=None):
        self.max_results = max_results

    def search(self, params):
        return [
            JobListing(
                title="QA Engineer",
                url="https://example.test/jobs/1?utm_source=x",
                company="Example",
                remote=True,
                source="ok",
            )
        ]

    def enforce_deadline(self):
        return None

    def fetch_detail(self, listing):
        return listing


class _TimeoutScraper(_OkScraper):
    display_name = "Timeout"
    name = "timeout"

    def search(self, params):
        raise TimeoutError("source timeout")


class SearchRunnerTest(unittest.TestCase):
    def test_search_continues_after_source_timeout_and_writes_raw_jsonl(self) -> None:
        def scraper_class(name):
            return _NoBrowserClass

        def create_scraper(name, context, **kwargs):
            return _TimeoutScraper(context, **kwargs) if name == "timeout" else _OkScraper(context, **kwargs)

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "raw.jsonl"
            with (
                patch("job_harness.registry.list_scrapers", return_value=["timeout", "ok"]),
                patch("job_harness.registry.get_scraper_class", side_effect=scraper_class),
                patch("job_harness.registry.create_scraper", side_effect=create_scraper),
            ):
                result = execute_search(
                    query="QA",
                    ensure_context=lambda: (_ for _ in ()).throw(AssertionError("browser not expected")),
                    cache_factory=lambda: None,
                    raw_jsonl=raw_path,
                )

            records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(1, result.to_dict()["total"])
        self.assertIn("timeout: source timeout", result.errors)
        statuses = result.summary["source_statuses"]
        self.assertEqual("timeout", statuses[0]["status"])
        self.assertEqual("ok", statuses[1]["status"])
        self.assertEqual(["source_status", "listing", "source_status"], [record["type"] for record in records])

    def test_skip_slow_marks_browser_sources_skipped(self) -> None:
        with (
            patch("job_harness.registry.list_scrapers", return_value=["browser"]),
            patch("job_harness.registry.get_scraper_class", return_value=_BrowserClass),
        ):
            result = execute_search(
                query="QA",
                ensure_context=lambda: (_ for _ in ()).throw(AssertionError("browser not expected")),
                cache_factory=lambda: None,
                skip_slow=True,
            )

        self.assertEqual(0, result.to_dict()["total"])
        self.assertEqual("skipped", result.summary["source_statuses"][0]["status"])

    def test_dedupe_uses_url_and_title_company_keys(self) -> None:
        first = JobListing(title="QA Engineer", url="https://hh.ru/vacancy/123?from=serp", company="Example", source="hh_ru")
        richer = JobListing(
            title="QA Engineer",
            url="https://example.test/jobs/qa",
            company="Example",
            description="Detailed",
            source="direct",
        )

        listings = dedupe_listings([first, richer])

        self.assertEqual(1, len(listings))
        self.assertEqual("https://example.test/jobs/qa", listings[0].url)

    def test_wide_search_writes_expected_artifacts(self) -> None:
        fake_result = SearchResults(
            params=SearchParams(query="QA", max_results=10),
            listings=[JobListing(title="QA Engineer", url="https://example.test/jobs/1", company="Example", source="fake")],
            summary={"source_statuses": []},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            args = Namespace(
                query=["QA"],
                queries_file=None,
                countries=None,
                output_dir=tmpdir,
                remote_only=False,
                experience=None,
                max_results=10,
                source_timeout_ms=1000,
                headless=True,
                company_live=False,
                company_max_companies=None,
                company_timeout_ms=1000,
                progress=False,
            )
            with patch("job_harness.cli.execute_search", return_value=fake_result):
                cmd_wide_search(args)

            output_dir = Path(tmpdir)
            self.assertTrue((output_dir / "results.json").exists())
            self.assertTrue((output_dir / "summary.json").exists())
            self.assertTrue((output_dir / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
