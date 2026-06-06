from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.company_career_batch import (
    DEFAULT_COMPANY_LIVE_WORKERS,
    RESOLVED_EMPLOYER_CACHE_SOURCE,
    _build_summary,
    _check_company,
    _default_employer_cache_paths,
    _load_company_targets,
    _read_completed_companies,
    _write_summary,
    run_company_career_batch,
)
from job_harness.company_career_search import CompanyVacancyHit
from job_harness.company_directory import CompanyProfile


class _SlowEvaluatePage:
    def __init__(self):
        self.url = "https://alpha.test/careers"
        self.closed = False

    async def goto(self, url: str, **kwargs) -> None:
        self.url = url

    async def wait_for_timeout(self, timeout: int) -> None:
        return None

    async def evaluate(self, script: str):
        await asyncio.sleep(1)
        return []

    async def close(self) -> None:
        self.closed = True


class _SlowEvaluatePageWithContent(_SlowEvaluatePage):
    async def content(self) -> str:
        return '<a href="/jobs/qa">QA Engineer</a>'


class _AsyncContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class CompanyCareerBatchTest(unittest.TestCase):
    def test_default_worker_count_is_full_scale_concurrency(self) -> None:
        default = inspect.signature(run_company_career_batch).parameters["workers"].default

        self.assertEqual(12, DEFAULT_COMPANY_LIVE_WORKERS)
        self.assertEqual(DEFAULT_COMPANY_LIVE_WORKERS, default)

    def test_company_targets_include_resolved_employer_cache_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory_path = Path(tmpdir) / "companies.json"
            directory_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "Bundled Co",
                            "careers_url": "https://old.example.test/careers",
                            "countries": ["Armenia"],
                            "sources": ["test"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            cache_path = Path(tmpdir) / "careers.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "bundled co": {
                            "company": "Bundled Co",
                            "careers_url": "https://new.example.test/jobs",
                            "ats_type": "lever",
                            "scraper_name": None,
                            "last_checked": "2026-06-03",
                            "last_found_roles": True,
                            "ignored": False,
                        },
                        "resolved only": {
                            "company": "Resolved Only",
                            "careers_url": "https://resolved.example.test/careers",
                            "ats_type": "direct",
                            "scraper_name": None,
                            "last_checked": "2026-06-03",
                            "last_found_roles": False,
                            "ignored": False,
                        },
                        "ignored co": {
                            "company": "Ignored Co",
                            "careers_url": "https://ignored.example.test/careers",
                            "ats_type": "direct",
                            "scraper_name": None,
                            "last_checked": "2026-06-03",
                            "last_found_roles": False,
                            "ignored": True,
                        },
                        "empty co": {
                            "company": "Empty Co",
                            "careers_url": None,
                            "ats_type": "unknown",
                            "scraper_name": None,
                            "last_checked": "2026-06-03",
                            "last_found_roles": False,
                            "ignored": False,
                        },
                    }
                ),
                encoding="utf-8",
            )

            targets = _load_company_targets(
                directory_path=directory_path,
                employer_cache_paths=[cache_path],
            )

        by_name = {target.name: target for target in targets}
        self.assertEqual({"Bundled Co", "Resolved Only"}, set(by_name))
        self.assertEqual("https://new.example.test/jobs", by_name["Bundled Co"].careers_url)
        self.assertEqual("lever", by_name["Bundled Co"].ats_type)
        self.assertEqual(("Armenia",), by_name["Bundled Co"].countries)
        self.assertIn(RESOLVED_EMPLOYER_CACHE_SOURCE, by_name["Bundled Co"].sources)
        self.assertEqual("https://resolved.example.test/careers", by_name["Resolved Only"].careers_url)
        self.assertEqual((RESOLVED_EMPLOYER_CACHE_SOURCE,), by_name["Resolved Only"].sources)

    def test_company_targets_apply_max_companies_after_cache_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory_path = Path(tmpdir) / "companies.json"
            directory_path.write_text(
                json.dumps(
                    [
                        {"name": "B Co", "careers_url": "https://b.example.test", "sources": ["test"]},
                        {"name": "C Co", "careers_url": "https://c.example.test", "sources": ["test"]},
                    ]
                ),
                encoding="utf-8",
            )
            cache_path = Path(tmpdir) / "careers.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "a co": {
                            "company": "A Co",
                            "careers_url": "https://a.example.test",
                            "ats_type": "direct",
                            "scraper_name": None,
                            "last_checked": "2026-06-03",
                            "last_found_roles": False,
                            "ignored": False,
                        }
                    }
                ),
                encoding="utf-8",
            )

            targets = _load_company_targets(
                directory_path=directory_path,
                employer_cache_paths=[cache_path],
                max_results=2,
            )

        self.assertEqual(["A Co", "B Co"], [target.name for target in targets])

    def test_default_employer_cache_paths_include_artifact_root_from_output_path(self) -> None:
        output_path = Path("/tmp/search/.job-harness/briefs/demo/runs/run/raw/company-live-results.jsonl")

        paths = _default_employer_cache_paths(
            directory_path=Path("data/company-directory.json"),
            output_path=output_path,
        )

        self.assertIn(Path("/tmp/search/.job-harness/companies/careers.json"), paths)

    def test_check_company_times_out_hanging_link_extraction(self) -> None:
        page = _SlowEvaluatePage()
        company = CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers")

        with patch("job_harness.company_career_batch._find_matching_links_http", side_effect=RuntimeError("http failed")):
            record = asyncio.run(_check_company(_AsyncContext(page), company, ["qa"], timeout_ms=10))

        self.assertEqual("error", record["status"])
        self.assertEqual("http failed", record["error"])
        self.assertIn("link extraction timeout", record["attempt_errors"][0]["error"])
        self.assertTrue(page.closed)

    def test_check_company_uses_http_fallback_after_browser_timeout(self) -> None:
        page = _SlowEvaluatePage()
        company = CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers")
        hit = CompanyVacancyHit(
            company="Alpha",
            title="QA Engineer",
            vacancy_url="https://alpha.test/jobs/qa",
            careers_url="https://alpha.test/careers",
            matched_text="QA Engineer",
            score=3,
            countries=[],
            stack=[],
            job_types=[],
        )

        with patch("job_harness.company_career_batch._find_matching_links_http", return_value=[hit]):
            record = asyncio.run(_check_company(_AsyncContext(page), company, ["qa"], timeout_ms=10))

        self.assertEqual("ok", record["status"])
        self.assertEqual("http", record["method"])
        self.assertEqual(1, record["hit_count"])
        self.assertIn("link extraction timeout", record["attempt_errors"][0]["error"])

    def test_check_company_uses_browser_html_after_evaluate_timeout(self) -> None:
        page = _SlowEvaluatePageWithContent()
        company = CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers")

        record = asyncio.run(_check_company(_AsyncContext(page), company, ["qa"], timeout_ms=10))

        self.assertEqual("ok", record["status"])
        self.assertEqual("browser_html", record["method"])
        self.assertEqual(1, record["hit_count"])
        self.assertEqual("https://alpha.test/jobs/qa", record["hits"][0]["vacancy_url"])

    def test_check_company_uses_alternate_jobs_url_when_careers_url_missing(self) -> None:
        company = CompanyProfile(
            name="Alpha",
            careers_url=None,
            linkedin_jobs_url="https://linkedin.test/company/alpha/jobs/",
        )
        hit = CompanyVacancyHit(
            company="Alpha",
            title="QA Engineer",
            vacancy_url="https://linkedin.test/jobs/view/1",
            careers_url="https://linkedin.test/company/alpha/jobs/",
            matched_text="QA Engineer",
            score=3,
            countries=[],
            stack=[],
            job_types=[],
        )

        with patch("job_harness.company_career_batch._find_matching_links_http", return_value=[hit]) as fallback:
            record = asyncio.run(_check_company(_AsyncContext(_SlowEvaluatePage()), company, ["qa"], timeout_ms=10))

        self.assertEqual("ok", record["status"])
        self.assertEqual("alternate_jobs_http", record["method"])
        self.assertEqual("https://linkedin.test/company/alpha/jobs/", record["alternate_url"])
        fallback.assert_called_once()

    def test_linkedin_only_timeout_is_reported_as_access_issue(self) -> None:
        company = CompanyProfile(
            name="Alpha",
            careers_url=None,
            linkedin_jobs_url="https://www.linkedin.com/company/alpha/jobs/",
        )

        with patch(
            "job_harness.company_career_batch._find_matching_links_http",
            side_effect=TimeoutError("_ssl.c:1063: The handshake operation timed out"),
        ):
            record = asyncio.run(_check_company(_AsyncContext(_SlowEvaluatePage()), company, ["qa"], timeout_ms=10))

        self.assertEqual("access_issue", record["status"])
        self.assertEqual("network_restricted", record["reason"])
        self.assertEqual("https://www.linkedin.com/company/alpha/jobs/", record["alternate_url"])
        self.assertIn("VPN", record["remediation"])

    def test_linkedin_fallback_timeout_after_direct_fail_is_access_issue(self) -> None:
        page = _SlowEvaluatePage()
        company = CompanyProfile(
            name="Alpha",
            careers_url="https://alpha.test/careers",
            linkedin_jobs_url="https://www.linkedin.com/company/alpha/jobs/",
        )

        with patch(
            "job_harness.company_career_batch._find_matching_links_http",
            side_effect=TimeoutError("_ssl.c:1063: The handshake operation timed out"),
        ):
            record = asyncio.run(_check_company(_AsyncContext(page), company, ["qa"], timeout_ms=10))

        self.assertEqual("access_issue", record["status"])
        self.assertEqual("network_restricted", record["reason"])
        self.assertEqual("https://alpha.test/careers", record["careers_url"])
        self.assertEqual("https://www.linkedin.com/company/alpha/jobs/", record["alternate_url"])
        self.assertGreaterEqual(len(record["attempt_errors"]), 3)

    def test_check_company_marks_known_no_open_positions_without_network(self) -> None:
        company = CompanyProfile(
            name="Alpha",
            careers_url="https://alpha.test/careers",
            job_types=("There are no open positions at the moment",),
        )

        record = asyncio.run(_check_company(_AsyncContext(_SlowEvaluatePage()), company, ["qa"], timeout_ms=10))

        self.assertEqual("ok", record["status"])
        self.assertEqual("known_no_open_positions", record["method"])
        self.assertEqual(0, record["hit_count"])
        self.assertEqual([], record["hits"])

    def test_completed_companies_are_read_from_jsonl_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"company": "Alpha", "status": "ok", "hits": []}),
                        json.dumps({"company": "Beta", "status": "error", "hits": []}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            completed = _read_completed_companies(path)

        self.assertEqual({"Alpha", "Beta"}, completed)

    def test_completed_companies_ignore_interrupted_jsonl_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run.jsonl"
            path.write_text(
                json.dumps({"company": "Alpha", "status": "ok", "hits": []}) + "\n" + '{"company": ',
                encoding="utf-8",
            )

            completed = _read_completed_companies(path)

        self.assertEqual({"Alpha"}, completed)

    def test_build_summary_counts_statuses_and_sorts_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "company": "Alpha",
                                "status": "ok",
                                "hits": [{"company": "Alpha", "title": "QA", "score": 3}],
                            }
                        ),
                        json.dumps({"company": "Beta", "status": "skipped", "hits": []}),
                        json.dumps({"company": "Gamma", "status": "error", "error": "timeout", "hits": []}),
                        json.dumps({"company": "Delta", "status": "access_issue", "error": "blocked", "hits": []}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = _build_summary(query="QA", companies_considered=4, output_path=path)

        self.assertEqual("QA", summary["query"])
        self.assertEqual(4, summary["companies_recorded"])
        self.assertEqual(1, summary["companies_checked"])
        self.assertEqual(1, summary["companies_skipped"])
        self.assertEqual(1, summary["companies_error"])
        self.assertEqual(1, summary["companies_access_issue"])
        self.assertEqual(0, summary["companies_pending"])
        self.assertEqual(1, summary["total"])
        self.assertEqual("Alpha", summary["hits"][0]["company"])
        self.assertEqual("Gamma", summary["errors"][0]["company"])
        self.assertEqual("Delta", summary["access_issues"][0]["company"])
        self.assertIn("VPN", summary["warnings"][0])

    def test_write_summary_updates_incremental_summary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "run.jsonl"
            summary_path = Path(tmpdir) / "summary.json"
            output_path.write_text(
                json.dumps({"company": "Alpha", "status": "ok", "hits": []}) + "\n",
                encoding="utf-8",
            )

            summary = _write_summary(
                query="QA",
                companies_considered=3,
                output_path=output_path,
                summary_path=summary_path,
            )
            data = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(summary, data)
        self.assertEqual(1, data["companies_recorded"])
        self.assertEqual(2, data["companies_pending"])


if __name__ == "__main__":
    unittest.main()
