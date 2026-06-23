from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from job_harness.v2.cli import main as cli_main
from job_harness.v2.postprocessing.formatters import render_processed_results_markdown


class ProcessedResultsMarkdownTests(unittest.TestCase):
    def test_render_processed_results_markdown(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 10,
            "result_count": 1,
            "removed_counts": {"grade_mismatch": 3},
            "results": [
                {
                    "title": "QA Engineer",
                    "url": "https://example.com/jobs/1",
                    "company": "Acme",
                    "source": "hh_ru",
                    "native_grade": "middle",
                    "salary_text": "200 000 ₽",
                    "city": "Moscow",
                    "remote_in_country": True,
                    "skills": ["pytest", "API"],
                    "description": "Build test automation.",
                }
            ],
        }

        markdown = render_processed_results_markdown(payload)

        self.assertIn("# Job search results — `r-test`", markdown)
        self.assertIn("**Filtered out:** grade_mismatch: 3", markdown)
        self.assertIn("## 1. QA Engineer", markdown)
        self.assertIn("[Open vacancy](https://example.com/jobs/1)", markdown)
        self.assertIn("**Company:** Acme", markdown)
        self.assertIn("**Skills:** pytest, API", markdown)

    def test_render_processed_results_markdown_shows_detail_parse_status(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 1,
            "result_count": 1,
            "results": [
                {
                    "title": "QA Engineer",
                    "source": "hh_ru",
                    "description_availability": "detail_blocked",
                    "detail_parse_error": "hh.ru account captcha on vacancy detail",
                }
            ],
        }

        markdown = render_processed_results_markdown(payload)

        self.assertIn("**Description status:** `detail_blocked`", markdown)
        self.assertIn("**Detail parse error:** hh.ru account captcha on vacancy detail", markdown)

    def test_render_processed_results_markdown_respects_listing_limit(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 3,
            "result_count": 3,
            "results": [
                {"title": "First"},
                {"title": "Second"},
                {"title": "Third"},
            ],
        }

        markdown = render_processed_results_markdown(payload, listing_limit=2)

        self.assertIn("**Shown:** 2", markdown)
        self.assertIn("**Processed:** 3", markdown)
        self.assertIn("## 1. First", markdown)
        self.assertIn("## 2. Second", markdown)
        self.assertNotIn("## 3. Third", markdown)
        self.assertIn("markdown preview limited to first `2` listings", markdown)

    def test_cli_format_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_path = root / "processed-results.json"
            output_path = root / "report.md"
            input_path.write_text(
                json.dumps(
                    {
                        "record_type": "processed_results",
                        "run_id": "r-cli",
                        "append_sequence": 0,
                        "raw_records_read": 1,
                        "result_count": 0,
                        "results": [],
                    }
                ),
                encoding="utf-8",
            )

            exit_code = cli_main(
                [
                    "format",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("# Job search results — `r-cli`", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
