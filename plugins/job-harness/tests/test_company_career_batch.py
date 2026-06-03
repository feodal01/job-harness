from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from job_harness.company_career_batch import _build_summary, _check_company, _read_completed_companies, _write_summary
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


class _AsyncContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class CompanyCareerBatchTest(unittest.TestCase):
    def test_check_company_times_out_hanging_link_extraction(self) -> None:
        page = _SlowEvaluatePage()
        company = CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers")

        record = asyncio.run(_check_company(_AsyncContext(page), company, ["qa"], timeout_ms=10))

        self.assertEqual("error", record["status"])
        self.assertIn("link extraction timeout", record["error"])
        self.assertTrue(page.closed)

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
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = _build_summary(query="QA", companies_considered=3, output_path=path)

        self.assertEqual("QA", summary["query"])
        self.assertEqual(3, summary["companies_recorded"])
        self.assertEqual(1, summary["companies_checked"])
        self.assertEqual(1, summary["companies_skipped"])
        self.assertEqual(1, summary["companies_error"])
        self.assertEqual(0, summary["companies_pending"])
        self.assertEqual(1, summary["total"])
        self.assertEqual("Alpha", summary["hits"][0]["company"])
        self.assertEqual("Gamma", summary["errors"][0]["company"])

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
