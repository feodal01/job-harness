from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from job_harness.v2.cli import main as cli_main
from job_harness.v2.persistence import SqliteGraphRepository
from job_harness.v2.presentation import render_processed_results_html, render_processed_results_markdown


class ProcessedResultsMarkdownTests(unittest.TestCase):
    def test_render_processed_results_markdown(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 10,
            "result_count": 1,
            "results": [
                {
                    "title": "QA Engineer",
                    "vacancyUrl": "https://example.com/jobs/1",
                    "company": {"name": "Acme"},
                    "sourceId": "hh_ru",
                    "grade": {"resolved": ["middle"], "conflict": False},
                    "compensation": {
                        "minimum": 200000,
                        "currency": "RUB",
                        "period": "month",
                        "gross": False,
                    },
                    "location": {
                        "rawText": "Moscow",
                        "cities": ["Moscow"],
                        "countries": ["RU"],
                    },
                    "workplace": {
                        "formats": ["remote"],
                        "remoteScopes": ["country:RU"],
                    },
                    "skills": ["pytest", "API"],
                    "applicationChannels": [
                        {
                            "kind": "company_career_page",
                            "label": "Careers",
                            "value": "https://example.com/careers",
                        }
                    ],
                    "contacts": [
                        {
                            "kind": "email",
                            "label": "Email",
                            "value": "hr@example.com",
                        },
                        {
                            "kind": "phone",
                            "label": "Phone",
                            "value": "+7 (999) 111-22-33",
                        },
                    ],
                    "description": "Build test automation.",
                }
            ],
        }

        markdown = render_processed_results_markdown(payload)

        self.assertIn("# Job search results — `r-test`", markdown)
        self.assertIn("## 1. QA Engineer", markdown)
        self.assertIn("[Open vacancy](https://example.com/jobs/1)", markdown)
        self.assertIn("**Company:** Acme", markdown)
        self.assertIn("**Grade:** middle", markdown)
        self.assertIn("**Salary:** 200000 RUB / month net", markdown)
        self.assertIn("**Location:** Moscow | RU", markdown)
        self.assertIn("**Format:** remote", markdown)
        self.assertIn("**Skills:** pytest, API", markdown)
        self.assertIn("**Apply channels**", markdown)
        self.assertIn("- [Careers](https://example.com/careers)", markdown)
        self.assertIn("**Company contacts**", markdown)
        self.assertIn("- Email: [hr@example.com](mailto:hr@example.com)", markdown)
        self.assertIn("- Phone: [+7 (999) 111-22-33](tel:+7 (999) 111-22-33)", markdown)
        self.assertNotIn("— resolved", markdown)

    def test_render_processed_results_markdown_shows_duplicate_confidence(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-test",
            "append_sequence": 0,
            "raw_records_read": 1,
            "result_count": 1,
            "results": [
                {
                    "title": "QA Engineer",
                    "sourceId": "hh_ru",
                    "duplicateConfidence": "probable",
                }
            ],
        }

        markdown = render_processed_results_markdown(payload)

        self.assertIn("**Duplicate confidence:** `probable`", markdown)

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
            repository = SqliteGraphRepository(input_path)
            first_execution = repository.create_execution(
                run_id="r-cli",
                intent={"query_variants": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            latest_execution = repository.create_execution(
                run_id="r-cli",
                intent={"query_variants": ["AQA"]},
                append_sequence=1,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            repository.close()
            with closing(sqlite3.connect(input_path)) as connection:
                connection.execute(
                    "UPDATE search_executions SET status = 'completed' WHERE execution_id IN (?, ?)",
                    (first_execution, latest_execution),
                )
                connection.executemany(
                    """
                    INSERT INTO vacancy_resources (
                        vacancy_id, run_id, target_provider_id, source_listing_id,
                        canonical_url, identity_key, identity_schema_version, created_at
                    ) VALUES (?, 'r-cli', 'test', ?, ?, ?, 1, 0)
                    """,
                    (
                        (
                            "vacancy-old",
                            "old",
                            "https://example.com/jobs/old",
                            "test:old",
                        ),
                        (
                            "vacancy-latest",
                            "latest",
                            "https://example.com/jobs/latest",
                            "test:latest",
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO vacancy_listings (
                        listing_id, run_id, vacancy_id, source_id,
                        source_listing_id, identity_key, created_at
                    ) VALUES (?, 'r-cli', ?, 'test', ?, ?, 0)
                    """,
                    (
                        ("listing-old", "vacancy-old", "old", "test:old"),
                        (
                            "listing-latest",
                            "vacancy-latest",
                            "latest",
                            "test:latest",
                        ),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO final_vacancies (
                        final_vacancy_id, execution_id, listing_id, evaluation_id,
                        snapshot_version, score, payload_json
                    ) VALUES (?, ?, ?, ?, 1, 0, ?)
                    """,
                    (
                        (
                            "final-old",
                            first_execution,
                            "listing-old",
                            "evaluation-old",
                            json.dumps({"title": "Old append snapshot"}),
                        ),
                        (
                            "final-latest",
                            latest_execution,
                            "listing-latest",
                            "evaluation-latest",
                            json.dumps({"title": "Latest full run snapshot"}),
                        ),
                    ),
                )
                connection.commit()

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
    def test_render_processed_results_html_shows_execution_quality_and_source_coverage(self) -> None:
        payload = {
            "record_type": "processed_results",
            "run_id": "r-degraded",
            "execution_quality": "degraded",
            "source_coverage": {
                "planned": 149,
                "complete": 143,
                "degraded": 6,
                "failed": 4,
            },
            "search_request": {"query_variants": ["QA"]},
            "results": [],
            "filtered_out_results": [],
        }

        html = render_processed_results_html(payload)

        self.assertIn('id="execution-summary"', html)
        self.assertIn('summaryField("Quality", executionQuality)', html)
        self.assertIn('summaryField("Sources complete", `${sourceCoverage.complete}/${sourceCoverage.planned}`)', html)
        self.assertIn('summaryField("Sources degraded", sourceCoverage.degraded)', html)
        self.assertIn('summaryField("Sources failed", sourceCoverage.failed)', html)
        self.assertIn('"execution_quality": "degraded"', html)
        self.assertIn('"planned": 149', html)

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
                "work_formats": ["remote"],
                "remote_scopes": ["country:RU"],
                "vacancy_geographies": ["country:RU"],
                "sources": ["hh_ru"],
                "scenarios": [
                    {
                        "work_formats": ["remote"],
                        "remote_scopes": ["global"],
                        "employer_geographies": ["country:RU"],
                        "relocation": None,
                    },
                    {
                        "work_formats": ["remote", "hybrid"],
                        "remote_scopes": [],
                        "employer_geographies": [],
                        "relocation": True,
                    },
                ],
            },
            "results": [
                {
                    "title": "QA Engineer",
                    "company": {"name": "Acme"},
                    "sourceId": "hh_ru",
                    "sourceListingId": "1",
                    "vacancyUrl": "https://example.com/jobs/1",
                    "compensation": {
                        "minimum": 200000,
                        "currency": "RUB",
                        "period": "month",
                        "gross": False,
                    },
                    "grade": {"resolved": ["middle"], "conflict": False},
                    "location": {
                        "rawText": "Moscow",
                        "cities": ["Moscow"],
                        "countries": ["RU"],
                    },
                    "workplace": {
                        "formats": ["remote"],
                        "remoteScopes": ["country:RU"],
                    },
                    "relocation": {
                        "supported": True,
                        "destinations": ["US"],
                    },
                    "applicationChannels": [
                        {
                            "kind": "company_career_page",
                            "label": "Careers",
                            "value": "https://example.com/careers",
                        },
                    ],
                }
            ],
            "filtered_out_results": [
                {
                    "decision": "filtered_out",
                    "decision_reasons": ["grade_mismatch"],
                    "title": "QA Intern",
                    "company": {"name": "Beta"},
                    "sourceId": "habr_career",
                    "description": "Internship",
                },
                {
                    "decision": "filtered_out",
                    "decision_reasons": ["required_provider_terminal"],
                    "title": "AI Evaluation Specialist",
                    "sourceId": "talanto",
                },
            ],
        }

        # Act
        html = render_processed_results_html(payload)

        # Assert
        self.assertIn('<script id="job-harness-payload" type="application/json">', html)
        self.assertIn('"filtered_out_results"', html)
        self.assertIn('"search_request"', html)
        self.assertIn("Search request", html)
        self.assertIn('requestField("Scenarios", scenarioText(request.scenarios))', html)
        self.assertIn('requestField("Scenarios", scenarioText(request.scenarios))', html)
        self.assertIn('fieldText("Employer geography", scenario.employer_geographies)', html)
        self.assertIn('"employer_geographies": ["country:RU"]', html)
        self.assertIn("Compensation", html)
        self.assertIn("Full catalog", html)
        self.assertIn("className = \"ordinal\"", html)
        self.assertIn("renderRow(row, index + 1)", html)
        self.assertIn("className = \"company-line\"", html)
        self.assertIn('cardField("Grade", gradeText(row.grade), highlightedLabels.has("Grade"))', html)
        self.assertIn(
            'cardField("Salary", compensationText(row.compensation), highlightedLabels.has("Salary"))',
            html,
        )
        self.assertIn('cardField("Posted", row.postedAt, highlightedLabels.has("Posted"))', html)
        self.assertIn('cardField("Work format", workplaceFormatText(row.workplace)', html)
        self.assertIn('cardField("Location", locationText(row.location)', html)
        self.assertIn('cardField("Remote scope", remoteScopeText(row.workplace)', html)
        self.assertIn('cardField("Relocation", relocationText(row.relocation)', html)
        self.assertIn('return `yes, to ${destinations.join(", ")}`;', html)
        self.assertIn('valueText || "not specified"', html)
        self.assertIn("field excluded-reason", html)
        self.assertIn("skillsElement(row)", html)
        self.assertIn("applicationChannelsElement(row)", html)
        self.assertIn("className = \"apply-channels\"", html)
        self.assertIn("Apply channels", html)
        self.assertIn('"applicationChannels"', html)
        self.assertIn("link.textContent = text(channel.label) || text(channel.kind) || \"Apply\"", html)
        self.assertIn("className = \"skill\"", html)
        self.assertIn('debugField("Filter reason", removalReasonText(row))', html)
        self.assertIn("terminalStatusElement(row)", html)
        self.assertIn('hasRemovalReason(row, "required_provider_terminal")', html)
        self.assertIn('status.textContent = "Required details could not be retrieved"', html)
        self.assertIn(
            'decisionReasons(row).filter((reason) => reason !== "required_provider_terminal")',
            html,
        )
        self.assertNotIn('debugField("Query"', html)
        self.assertNotIn('debugField("Vacancy geography"', html)
        self.assertIn('["grade_mismatch", "Grade"]', html)
        self.assertIn('["remote_scope_mismatch", "Remote scope"]', html)
        self.assertIn('"decision_reasons": ["grade_mismatch"]', html)
        self.assertIn('"decision_reasons": ["required_provider_terminal"]', html)
        self.assertNotIn("workMode(row)", html)
        self.assertIn("className = \"debug-meta\"", html)
        self.assertIn("debugField(\"Source\", row.sourceId)", html)
        self.assertIn("debugField(\"Source listing ID\", row.sourceListingId)", html)
        self.assertIn("detailTextParts(row)", html)
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
                    "title": "QA Engineer",
                    "contacts": [
                        {
                            "kind": "email",
                            "label": "Email",
                            "value": "hr@example.com",
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
        self.assertIn('"contacts"', html)
        self.assertIn("item.textContent = `${label}: ${value}`", html)
        self.assertIn('if (kind === "email") return `mailto:${value}`', html)

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
        self.assertIn(
            'requestField("Compensation", compensationCriterionText(request.compensation) || "N/a")',
            html,
        )
        self.assertIn("function compensationCriterionText(value)", html)
        self.assertIn('requestField("Published since", text(request.published_since) || "N/a")', html)
        self.assertIn('requestField("Work format", listText(request.work_formats, "N/a"))', html)
        self.assertIn('requestField("Remote scope", listText(request.remote_scopes, "N/a"))', html)
        self.assertIn('requestField("Vacancy geography", listText(request.vacancy_geographies, "N/a"))', html)
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
