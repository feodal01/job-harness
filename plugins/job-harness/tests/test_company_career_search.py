from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.company_career_search import (
    CompanyVacancyHit,
    _dedupe_hits,
    _extract_page_links,
    _find_matching_ats_jobs,
    _find_matching_links_from_html,
    _is_non_vacancy_link,
    _query_terms,
    _score_text,
    search_company_careers,
)
from job_harness.company_directory import CompanyProfile


class _FakeLink:
    def __init__(self, href: str, text: str):
        self.href = href
        self.text = text

    def get_attribute(self, name: str) -> str | None:
        return self.href if name == "href" else None

    def inner_text(self) -> str:
        return self.text


class _FakeLocator:
    def __init__(self, links: list[_FakeLink]):
        self.links = links

    def count(self) -> int:
        return len(self.links)

    def nth(self, index: int) -> _FakeLink:
        return self.links[index]


class _FakePage:
    def __init__(self, links: list[_FakeLink], *, fail: bool = False):
        self.links = links
        self.fail = fail
        self.url = ""
        self.closed = False

    def goto(self, url: str, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("navigation failed")
        self.url = url

    def wait_for_timeout(self, timeout: int) -> None:
        self.timeout = timeout

    def locator(self, selector: str) -> _FakeLocator:
        self.selector = selector
        return _FakeLocator(self.links)

    def close(self) -> None:
        self.closed = True


class _EvaluatePage:
    def __init__(self):
        self.evaluate_calls = 0

    def evaluate(self, script: str) -> list[dict[str, str]]:
        self.evaluate_calls += 1
        return [
            {"href": "/jobs/qa", "text": "QA Engineer"},
            {"href": "/about", "text": "About"},
        ]


class _FakeContext:
    def __init__(self, pages: list[_FakePage]):
        self.pages = pages
        self.index = 0

    def new_page(self) -> _FakePage:
        page = self.pages[self.index]
        self.index += 1
        return page


class CompanyCareerSearchTest(unittest.TestCase):
    def test_extract_page_links_uses_single_evaluate_snapshot(self) -> None:
        page = _EvaluatePage()

        links = _extract_page_links(page)

        self.assertEqual(1, page.evaluate_calls)
        self.assertEqual("/jobs/qa", links[0]["href"])

    def test_qa_query_terms_match_roles_not_generic_testing_words(self) -> None:
        terms = _query_terms("QA")

        self.assertGreater(_score_text("senior qa engineer", terms), 0)
        self.assertGreater(_score_text("testing engineer", terms), 0)
        self.assertGreater(_score_text("test automation engineer", terms), 0)
        self.assertGreater(_score_text("manual tester", terms), 0)
        self.assertGreater(_score_text("software tester", terms), 0)
        self.assertGreater(_score_text("senior quality assurance engineer", terms), 0)
        self.assertEqual(0, _score_text("diversity equality inclusion", terms))
        self.assertEqual(0, _score_text("product designer for web testing", terms))
        self.assertEqual(0, _score_text("penetration tester", terms))
        self.assertEqual(0, _score_text("vip quality assurance manager french speaking", terms))

    def test_known_training_pages_are_not_vacancy_links(self) -> None:
        self.assertTrue(_is_non_vacancy_link("https://www.careerist.com/qa", "QA Engineering"))
        self.assertTrue(_is_non_vacancy_link("https://www.careerist.com/automation", "QA Automation"))

    def test_dedupe_hits_normalizes_trailing_slashes_and_keeps_best_score(self) -> None:
        low_score = CompanyVacancyHit(
            company="Alpha",
            title="https://alpha.test/jobs/qa",
            vacancy_url="https://alpha.test/jobs/qa/",
            careers_url="https://alpha.test/careers",
            matched_text="",
            score=3,
            countries=[],
            stack=[],
            job_types=[],
        )
        high_score = CompanyVacancyHit(
            company="Alpha",
            title="QA Engineer",
            vacancy_url="https://alpha.test/jobs/qa",
            careers_url="https://alpha.test/careers",
            matched_text="QA Engineer",
            score=6,
            countries=[],
            stack=[],
            job_types=[],
        )

        hits = _dedupe_hits([low_score, high_score])

        self.assertEqual([high_score], hits)

    def test_html_snapshot_dedupes_duplicate_links_and_keeps_descriptive_title(self) -> None:
        company = CompanyProfile(name="Alpha", careers_url="https://alpha.test/careers")

        hits = _find_matching_links_from_html(
            html="""
                <a href="/careers/qa-engineer">https://alpha.test/careers/qa-engineer</a>
                <a href="/careers/qa-engineer">QA Engineer</a>
            """,
            base_url="https://alpha.test/careers",
            careers_url="https://alpha.test/careers",
            company=company,
            query_terms=_query_terms("QA"),
        )

        self.assertEqual(1, len(hits))
        self.assertEqual("QA Engineer", hits[0].title)
        self.assertEqual("https://alpha.test/careers/qa-engineer", hits[0].vacancy_url)

    def test_lever_ats_api_returns_matching_postings_without_browser(self) -> None:
        company = CompanyProfile(name="Alpha", careers_url="https://jobs.lever.co/alpha")
        postings = [
            {
                "text": "QA Automation Engineer",
                "hostedUrl": "https://jobs.lever.co/alpha/123",
                "categories": {"location": "Remote"},
                "descriptionPlain": "Test automation for backend services",
            },
            {
                "text": "Product Manager",
                "hostedUrl": "https://jobs.lever.co/alpha/456",
                "categories": {"location": "Remote"},
            },
        ]

        with patch("job_harness.company_career_search.fetch_json", return_value=postings):
            hits = _find_matching_ats_jobs(company, _query_terms("QA"))

        self.assertIsNotNone(hits)
        assert hits is not None
        self.assertEqual(1, len(hits or []))
        self.assertEqual("QA Automation Engineer Remote", hits[0].title)
        self.assertEqual("https://jobs.lever.co/alpha/123", hits[0].vacancy_url)

    def test_ashby_ats_api_returns_matching_postings_without_browser(self) -> None:
        company = CompanyProfile(name="Alpha", careers_url="https://jobs.ashbyhq.com/alpha")
        payload = {
            "jobs": [
                {
                    "title": "Senior QA Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/alpha/123",
                    "location": "Cyprus",
                    "team": "Engineering",
                    "descriptionHtml": "<p>Own test automation.</p>",
                },
                {
                    "title": "Product Manager",
                    "jobUrl": "https://jobs.ashbyhq.com/alpha/456",
                    "location": "Remote",
                },
            ]
        }

        with patch("job_harness.company_career_search.fetch_json", return_value=payload):
            hits = _find_matching_ats_jobs(company, _query_terms("QA"))

        self.assertIsNotNone(hits)
        assert hits is not None
        self.assertEqual(1, len(hits or []))
        self.assertEqual("Senior QA Engineer Cyprus", hits[0].title)
        self.assertEqual("https://jobs.ashbyhq.com/alpha/123", hits[0].vacancy_url)

    def test_ats_api_does_not_match_qa_only_in_description_body(self) -> None:
        company = CompanyProfile(name="Alpha", careers_url="https://jobs.ashbyhq.com/alpha")
        payload = {
            "jobs": [
                {
                    "title": "Senior Product Designer",
                    "jobUrl": "https://jobs.ashbyhq.com/alpha/123",
                    "location": "Remote",
                    "descriptionHtml": "<p>Work with quality assurance and engineering teams.</p>",
                }
            ]
        }

        with patch("job_harness.company_career_search.fetch_json", return_value=payload):
            hits = _find_matching_ats_jobs(company, _query_terms("QA"))

        self.assertEqual([], hits)

    def test_search_company_careers_checks_pages_and_returns_matching_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory_path = Path(tmpdir) / "companies.json"
            directory_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "Alpha",
                            "careers_url": "https://alpha.test/careers",
                            "countries": ["Armenia"],
                            "stack": ["Python"],
                            "job_types": ["Developers"],
                            "sources": ["test"],
                        },
                        {
                            "name": "No Careers",
                            "countries": ["Armenia"],
                            "sources": ["test"],
                        },
                    ]
                ),
                encoding="utf-8",
            )
            context = _FakeContext(
                [
                    _FakePage(
                        [
                            _FakeLink("/jobs/python-developer", "Senior Python Developer"),
                            _FakeLink("/training/python-course", "Python Training Course"),
                            _FakeLink("/paywall-ab-testing", "A/B testing"),
                            _FakeLink("/reviews", "Testimonials"),
                            _FakeLink("/diversity", "Diversity, Equality, Inclusion"),
                            _FakeLink("/careers/email-automation-specialist", "Email Automation Specialist"),
                            _FakeLink("/services_software-quality-assurance", "Software testing and QA"),
                            _FakeLink("/data-quality-platform", "Data Quality platform"),
                            _FakeLink("https://linkedin.com/company/alpha", "LinkedIn"),
                            _FakeLink("/about", "About us"),
                        ]
                    )
                ]
            )

            result = search_company_careers(
                "Python developer",
                context,
                country="Armenia",
                directory_path=directory_path,
            )

        data = result.to_dict()
        self.assertEqual(2, data["companies_considered"])
        self.assertEqual(1, data["companies_checked"])
        self.assertEqual(1, data["companies_skipped"])
        self.assertEqual([{"company": "No Careers", "reason": "missing careers_url", "linkedin_jobs_url": None}], data["skipped_companies"])
        self.assertEqual("ok", data["checked_companies"][0]["status"])
        self.assertEqual(1, data["checked_companies"][0]["hits"])
        self.assertEqual([], data["errors"])
        self.assertEqual(1, data["total"])
        self.assertEqual("Alpha", data["hits"][0]["company"])
        self.assertEqual("https://alpha.test/jobs/python-developer", data["hits"][0]["vacancy_url"])

    def test_search_company_careers_records_navigation_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory_path = Path(tmpdir) / "companies.json"
            directory_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "Broken",
                            "careers_url": "https://broken.test/careers",
                            "sources": ["test"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            context = _FakeContext([_FakePage([], fail=True)])

            with patch("job_harness.company_career_search._find_matching_links_http", side_effect=RuntimeError("http failed")):
                result = search_company_careers("QA", context, directory_path=directory_path)

        data = result.to_dict()
        self.assertEqual(1, data["companies_checked"])
        self.assertEqual("error", data["checked_companies"][0]["status"])
        self.assertEqual(1, len(data["errors"]))
        self.assertEqual("Broken", data["errors"][0]["company"])
        self.assertEqual("http failed", data["errors"][0]["error"])
        self.assertEqual(0, data["total"])

    def test_search_company_careers_marks_known_no_open_positions_as_checked_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            directory_path = Path(tmpdir) / "companies.json"
            directory_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "Closed Roles",
                            "careers_url": "https://closed.test/careers",
                            "job_types": ["There are no open positions at the moment"],
                            "sources": ["test"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            context = _FakeContext([])

            result = search_company_careers("QA", context, directory_path=directory_path)

        data = result.to_dict()
        self.assertEqual(1, data["companies_checked"])
        self.assertEqual(0, data["companies_skipped"])
        self.assertEqual([], data["errors"])
        self.assertEqual("ok", data["checked_companies"][0]["status"])
        self.assertEqual("known_no_open_positions", data["checked_companies"][0]["method"])
        self.assertEqual(0, data["total"])


if __name__ == "__main__":
    unittest.main()
