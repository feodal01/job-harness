from __future__ import annotations

import csv
import io
import json
import unittest
from typing import Any

from job_harness.filters import apply_filters, has_salary, min_experience, no_keywords, remote_only
from job_harness.formatters import CsvFormatter, JsonFormatter, MarkdownFormatter
from job_harness.models import JobListing, SearchParams, SearchResults


def _listing(**overrides: Any) -> JobListing:
    data: dict[str, Any] = {
        "title": "QA Engineer",
        "url": "https://example.test/jobs/1",
        "company": "Example",
        "salary": "200 000 ₽",
        "experience": "senior",
        "remote": True,
        "description": "Manual testing and API checks.",
        "requirements": "Python будет плюсом.",
        "skills": ["Python", "SQL"],
        "source": "habr_career",
    }
    data.update(overrides)
    return JobListing(**data)


class FiltersAndFormattersTest(unittest.TestCase):
    def test_apply_filters_keeps_only_matching_listings(self) -> None:
        listings = [
            _listing(title="Remote Senior QA"),
            _listing(title="Office Junior QA", remote=False, experience="junior"),
            _listing(title="Remote Without Salary", salary=None),
        ]

        filtered = apply_filters(listings, [remote_only, min_experience("middle"), has_salary])

        self.assertEqual(["Remote Senior QA"], [listing.title for listing in filtered])

    def test_no_keywords_honors_ignore_context(self) -> None:
        listing = _listing(requirements="Java обязателен. Python будет плюсом.")

        self.assertTrue(no_keywords("Python", ignore_context=["будет плюсом"])(listing))
        self.assertFalse(no_keywords("Java", ignore_context=["будет плюсом"])(listing))

    def test_json_formatter_preserves_unicode_and_total(self) -> None:
        results = SearchResults(
            params=SearchParams(query="ручной тестировщик", max_results=1),
            listings=[_listing(company="Банк России")],
            timestamp="2026-06-02 13:00",
            errors=[],
        )

        payload = json.loads(JsonFormatter().format(results))

        self.assertEqual("ручной тестировщик", payload["params"]["query"])
        self.assertEqual("Банк России", payload["listings"][0]["company"])
        self.assertEqual(1, payload["total"])
        self.assertEqual([], payload["errors"])

    def test_csv_formatter_writes_stable_header_and_skill_list(self) -> None:
        results = SearchResults(
            params=SearchParams(query="QA", max_results=1),
            listings=[_listing()],
            timestamp="2026-06-02 13:00",
        )

        rows = list(csv.reader(io.StringIO(CsvFormatter().format(results))))

        self.assertEqual(
            [
                "title",
                "url",
                "company",
                "country",
                "salary",
                "experience",
                "remote",
                "location",
                "skills",
                "source",
                "posted_date",
            ],
            rows[0],
        )
        self.assertEqual("Python; SQL", rows[1][8])
        self.assertEqual("habr_career", rows[1][9])

    def test_markdown_formatter_includes_summary_and_listing(self) -> None:
        results = SearchResults(
            params=SearchParams(query="QA", max_results=1),
            listings=[_listing()],
            timestamp="2026-06-02 13:00",
        )

        markdown = MarkdownFormatter().format(results)

        self.assertIn("# Job Search: QA", markdown)
        self.assertIn("### 1. QA Engineer", markdown)
        self.assertIn("## Summary", markdown)
        self.assertIn("| 1 | Example | not specified | 200 000 ₽ | remote | senior |", markdown)


if __name__ == "__main__":
    unittest.main()
