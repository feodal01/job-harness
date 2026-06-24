from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_harness.v2.cli import main as cli_main
from job_harness.v2.persistence import SqliteRunStore
from job_harness.v2.presentation import render_processed_results_html, render_processed_results_markdown


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
            input_path = root / "run.sqlite"
            output_path = root / "report.md"
            with SqliteRunStore(input_path, run_id="r-cli") as store:
                store.reserve_append_attempt({"query_variants": ["QA"]})
                store.write_processed_results(
                    {
                        "record_type": "processed_results",
                        "run_id": "r-cli",
                        "append_sequence": 0,
                        "raw_records_read": 1,
                        "result_count": 1,
                        "results": [{"title": "Old append snapshot"}],
                    }
                )
                store.mark_append_attempt_completed()
                store.reserve_append_attempt({"query_variants": ["AQA"]})
                store.write_processed_results(
                    {
                        "record_type": "processed_results",
                        "run_id": "r-cli",
                        "append_sequence": 1,
                        "raw_records_read": 2,
                        "result_count": 1,
                        "results": [{"title": "Latest full run snapshot"}],
                    }
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
            markdown = output_path.read_text(encoding="utf-8")
            self.assertIn("# Job search results — `r-cli`", markdown)
            self.assertIn("Latest full run snapshot", markdown)
            self.assertNotIn("Old append snapshot", markdown)


class ProcessedResultsHtmlTests(unittest.TestCase):
    def test_render_processed_results_html_embeds_kept_and_filtered_results(self) -> None:
        # Arrange
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 1,
            "raw_records_read": 2,
            "result_count": 1,
            "removed_counts": {"grade_mismatch": 1},
            "search_request": {
                "query_variants": ["QA"],
                "countries": ["RU"],
                "sources": ["hh_ru"],
            },
            "results": [
                {
                    "decision": "kept",
                    "decision_reasons": ["matches_requested_filters"],
                    "title": "QA Engineer",
                    "company": "Acme",
                    "source": "hh_ru",
                    "url": "https://example.com/jobs/1",
                    "display_salary": "не указан",
                    "display_experience": "1–3 года",
                    "display_work_format": "удалённо",
                    "source_facts": [{"label": "Experience", "value": "1–3 года"}],
                }
            ],
            "filtered_out_results": [
                {
                    "decision": "filtered_out",
                    "decision_reasons": ["grade_mismatch"],
                    "title": "QA Intern",
                    "company": "Beta",
                    "source": "habr_career",
                    "description": "Internship",
                }
            ],
        }

        # Act
        html = render_processed_results_html(payload)

        # Assert
        self.assertIn('<script id="job-harness-payload" type="application/json">', html)
        self.assertIn('"filtered_out_results"', html)
        self.assertIn('"search_request"', html)
        self.assertIn("Search request", html)
        self.assertIn("Salary from", html)
        self.assertIn("Full catalog", html)
        self.assertIn("className = \"ordinal\"", html)
        self.assertIn("renderRow(row, index + 1)", html)
        self.assertIn("className = \"company-line\"", html)
        self.assertIn('cardField("Experience", row.display_experience)', html)
        self.assertIn('cardField("Salary", row.display_salary)', html)
        self.assertIn('cardField("Posted", row.posted_at)', html)
        self.assertIn('cardField("Country", row.country)', html)
        self.assertIn('cardField("Location", row.location_text)', html)
        self.assertIn('cardField("Remote in country", booleanText(row.remote_in_country))', html)
        self.assertIn('cardField("Remote global", booleanText(row.remote_global))', html)
        self.assertIn('cardField("Relocation", booleanText(row.relocation))', html)
        self.assertIn('valueText || "not specified"', html)
        self.assertNotIn('cardField("Work format", row.display_work_format)', html)
        self.assertNotIn("workMode(row)", html)
        self.assertNotIn("|| row.native_grade", html)
        self.assertIn("className = \"debug-meta\"", html)
        self.assertNotIn("debugField(\"Company\", row.company)", html)
        self.assertIn("debugField(\"Source\", row.source)", html)
        self.assertIn("debugField(\"Source listing ID\", row.source_listing_id)", html)
        self.assertIn('"source_facts"', html)
        self.assertNotIn("factList(row.source_facts)", html)
        self.assertNotIn("const skills = list(row.skills)", html)
        self.assertNotIn("formatLocation(row)", html)
        self.assertIn("detailTextParts(row)", html)
        self.assertIn("if (!parts.length && rawText) parts.push(rawText)", html)
        self.assertIn('"QA Intern"', html)
        self.assertIn("Filtered out", html)

    def test_render_processed_results_html_escapes_script_terminator(self) -> None:
        # Arrange
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 1,
            "result_count": 1,
            "results": [{"title": "</script><script>alert(1)</script>"}],
            "filtered_out_results": [],
        }

        # Act
        html = render_processed_results_html(payload)
        embedded_payload = html.split('<script id="job-harness-payload" type="application/json">', 1)[1].split(
            "</script>",
            1,
        )[0]

        # Assert
        self.assertNotIn("</script>", embedded_payload)
        self.assertIn("\\u003c/script\\u003e", embedded_payload)


if __name__ == "__main__":
    unittest.main()
