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
                    "remote_scope": "global",
                    "skills": ["pytest", "API"],
                    "application_channels": [
                        {
                            "type": "company_career_page",
                            "label": "Careers",
                            "url": "https://example.com/careers",
                            "status": "resolved",
                        }
                    ],
                    "company_contacts": [
                        {
                            "type": "email",
                            "label": "Email",
                            "value": "hr@example.com",
                            "url": "mailto:hr@example.com",
                            "source": "company_site_contact_page",
                        },
                        {
                            "type": "phone",
                            "label": "Phone",
                            "value": "+7 (999) 111-22-33",
                            "source": "company_site_homepage",
                        },
                    ],
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
        self.assertIn("**Apply channels**", markdown)
        self.assertIn("- [Careers](https://example.com/careers)", markdown)
        self.assertIn("**Company contacts**", markdown)
        self.assertIn("- Email: [hr@example.com](mailto:hr@example.com)", markdown)
        self.assertIn("- Phone: +7 (999) 111-22-33", markdown)
        self.assertNotIn("— resolved", markdown)

    def test_render_processed_results_markdown_puts_detail_status_in_diagnostics(self) -> None:
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

        self.assertIn("**Diagnostics**", markdown)
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
                        "phase": "final",
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
                        "phase": "final",
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
                "remote_mode": "compatible_remote",
                "work_from_geographies": ["RU"],
                "vacancy_geographies": ["RU"],
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
                    "remote_scope": "global",
                    "remote_in_country": True,
                    "remote_global": True,
                    "application_channels": [
                        {
                            "type": "company_career_page",
                            "label": "Careers",
                            "url": "https://example.com/careers",
                            "status": "resolved",
                        },
                        {
                            "type": "aggregator_company_profile",
                            "label": "Profile",
                            "url": "https://hh.ru/employer/123",
                            "status": "source_provided",
                        },
                    ],
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
        self.assertIn('cardField("Experience", row.display_experience, highlightedLabels.has("Experience"))', html)
        self.assertIn('cardField("Salary", row.display_salary, highlightedLabels.has("Salary"))', html)
        self.assertIn('cardField("Posted", row.posted_at, highlightedLabels.has("Posted"))', html)
        self.assertIn('cardField("Work format", row.display_work_format, highlightedLabels.has("Work format"))', html)
        self.assertIn('cardField("Country", row.country, highlightedLabels.has("Country"))', html)
        self.assertIn('cardField("Location", row.location_text, highlightedLabels.has("Location"))', html)
        self.assertIn('cardField("Remote scope", row.remote_scope, highlightedLabels.has("Remote scope"))', html)
        self.assertIn('cardField("Relocation", booleanText(row.relocation), highlightedLabels.has("Relocation"))', html)
        self.assertIn('valueText || "not specified"', html)
        self.assertIn("field excluded-reason", html)
        self.assertIn("skillsElement(row)", html)
        self.assertIn("applicationChannelsElement(row)", html)
        self.assertIn("className = \"apply-channels\"", html)
        self.assertIn("Apply channels", html)
        self.assertIn('"application_channels"', html)
        self.assertIn("link.textContent = text(channel.label)", html)
        self.assertIn("if (details) link.title = details", html)
        self.assertNotIn("`${text(channel.label)} · ${status}`", html)
        self.assertIn("className = \"skill\"", html)
        self.assertIn('debugField("Filter reason", removalReasonText(row))', html)
        self.assertIn('debugField("Raw remote in country", booleanText(row.remote_in_country))', html)
        self.assertIn('debugField("Raw remote global", booleanText(row.remote_global))', html)
        self.assertIn('["grade_mismatch", "Experience"]', html)
        self.assertIn('["remote_eligibility_mismatch", "Remote scope"]', html)
        self.assertIn('"decision_reasons": ["grade_mismatch"]', html)
        self.assertNotIn("workMode(row)", html)
        self.assertNotIn("|| row.native_grade", html)
        self.assertIn("className = \"debug-meta\"", html)
        self.assertNotIn("debugField(\"Company\", row.company)", html)
        self.assertIn("debugField(\"Source\", row.source)", html)
        self.assertIn("debugField(\"Source listing ID\", row.source_listing_id)", html)
        self.assertIn('"source_facts"', html)
        self.assertNotIn("factList(row.source_facts)", html)
        self.assertNotIn("formatLocation(row)", html)
        self.assertIn("detailTextParts(row)", html)
        self.assertIn("if (!parts.length && rawText) parts.push(rawText)", html)
        self.assertIn('"QA Intern"', html)
        self.assertIn("Filtered out", html)

    def test_render_processed_results_html_allows_long_request_pills_to_wrap(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 0,
            "result_count": 0,
            "search_request": {
                "query_variants": ["QA"],
                "sources": [
                    "career:appfollow",
                    "career:airslate",
                    "career:wintermute",
                    "career:chainstack",
                ],
            },
            "results": [],
            "filtered_out_results": [],
        }

        html = render_processed_results_html(payload)

        self.assertIn("flex: 0 1 auto;", html)
        self.assertIn("max-width: 100%;", html)
        self.assertIn("min-width: 0;", html)
        self.assertIn("white-space: normal;", html)
        self.assertIn("overflow-wrap: anywhere;", html)
        self.assertIn('className = "pill-value"', html)
        self.assertIn("career:appfollow", html)
        self.assertIn("career:chainstack", html)

    def test_render_processed_results_html_embeds_company_contacts(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 1,
            "result_count": 1,
            "search_request": {"query_variants": ["QA"]},
            "results": [
                {
                    "decision": "kept",
                    "decision_reasons": ["matches_requested_filters"],
                    "title": "QA Engineer",
                    "company_contacts": [
                        {
                            "type": "email",
                            "label": "Email",
                            "value": "hr@example.com",
                            "url": "mailto:hr@example.com",
                            "source": "company_site_contact_page",
                        }
                    ],
                }
            ],
            "filtered_out_results": [],
        }

        html = render_processed_results_html(payload)

        self.assertIn("companyContactsElement(row)", html)
        self.assertIn("className = \"company-contacts\"", html)
        self.assertIn("Company contacts", html)
        self.assertIn('"company_contacts"', html)
        self.assertIn("item.textContent = `${label}: ${value}`", html)
        self.assertIn("if (source) item.title = source", html)

    def test_render_processed_results_html_uses_na_for_unset_request_filters(self) -> None:
        # Arrange
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 0,
            "result_count": 0,
            "search_request": {"query_variants": ["QA"]},
            "results": [],
            "filtered_out_results": [],
        }

        # Act
        html = render_processed_results_html(payload)

        # Assert
        self.assertIn('requestField("Grade", listText(request.grades, "N/a"))', html)
        self.assertIn('requestField("Salary from", text(request.salary_from) || "N/a")', html)
        self.assertIn('requestField("Published since", text(request.published_since) || "N/a")', html)
        self.assertIn('requestField("Work from", listText(request.work_from_geographies, "N/a"))', html)
        self.assertIn('requestField("Vacancy geography", listText(request.vacancy_geographies, "N/a"))', html)
        self.assertIn('requestField("Cities", listText(request.cities, "N/a"))', html)
        self.assertIn('requestField("Exclude companies", listText(request.exclude_companies, "N/a"))', html)
        self.assertIn('requestField("Exclude text", exclusionText(request.exclude_text) || "N/a")', html)
        self.assertIn('return "N/a";', html)
        self.assertNotIn('return "Any";', html)

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
