from __future__ import annotations

import unittest
from pathlib import Path

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanySiteInput,
    ParserType,
    SearchRequest,
    SearchResultOutcome,
    SingletonResultOutcome,
    VacancyDetailInput,
)
from job_harness.v2.ports import HttpAction, HttpResponse, ParserRuntime
from job_harness.v2.runtime.source_bundles import (
    detail_bundle,
    generic_company_site_bundle,
    hh_company_profile_bundle,
    search_bundle,
)
from job_harness.v2.runtime.sources import HhRuSource
from job_harness.v2.serialization import to_jsonable

_FIXTURES = Path(__file__).parent / "fixtures" / "scrapers" / "hh_ru"


class _Runtime(ParserRuntime):
    def __init__(self, response_body: str) -> None:
        self._response_body = response_body
        self.actions: list[HttpAction] = []

    @property
    def reserved_collection_units(self) -> int:
        return 1

    async def http(self, action: HttpAction) -> HttpResponse:
        self.actions.append(action)
        return HttpResponse(
            requested_url=action.url,
            final_url=action.url,
            status_code=200,
            media_type="text/html",
            body=self._response_body.encode(),
        )


class IndependentSourceBundleTest(unittest.IsolatedAsyncioTestCase):
    async def test_hh_listing_bundle_uses_clean_typed_contract(self) -> None:
        bundle = search_bundle(HhRuSource())
        inputs = bundle.plan_initial(SearchRequest(query_variants=("QA",)), {"kind": "catalog"})
        runtime = _Runtime((_FIXTURES / "success" / "response.html").read_text(encoding="utf-8"))

        result = await bundle.execute(inputs[0], runtime)

        self.assertEqual(bundle.manifest.parser_type, ParserType.SEARCH_LISTING)
        self.assertEqual(result.outcome, SearchResultOutcome.SUCCESS)
        self.assertEqual(len(result.items), 50)
        self.assertEqual(len(result.continuations), 1)
        first = result.items[0]
        assert first.salary is not None
        assert first.company is not None
        self.assertEqual(first.target_provider_id, "hh_ru")
        self.assertEqual(first.salary.currency, "RUB")
        self.assertIn("remote", first.work_formats)
        self.assertEqual(first.company.profile_url, "https://hh.ru/employer/5174681")
        self.assertEqual(
            set(to_jsonable(first)),
            {
                "source_id",
                "target_provider_id",
                "source_listing_id",
                "title",
                "company",
                "location",
                "salary",
                "work_formats",
                "remote_scopes",
                "native_grade",
                "posted_at",
                "vacancy_url",
                "apply_url",
                "summary",
            },
        )
        self.assertNotIn("timeout", to_jsonable(inputs[0]))
        self.assertNotIn("request_delay", to_jsonable(inputs[0]))

    async def test_hh_detail_bundle_does_not_require_listing_snapshot(self) -> None:
        bundle = detail_bundle(HhRuSource())
        runtime = _Runtime((_FIXTURES / "detail" / "response.html").read_text(encoding="utf-8"))

        result = await bundle.execute(
            VacancyDetailInput(
                target_provider_id="hh_ru",
                vacancy_url="https://hh.ru/vacancy/123",
                source_listing_id="123",
            ),
            runtime,
        )

        self.assertEqual(bundle.manifest.parser_type, ParserType.VACANCY_DETAIL)
        self.assertEqual(result.outcome, SingletonResultOutcome.SUCCESS)
        self.assertIsNotNone(result.item)
        self.assertIn("description", to_jsonable(result.item))
        self.assertNotIn("raw", to_jsonable(result.item))

    async def test_hh_company_profile_is_an_independent_url_scraper(self) -> None:
        bundle = hh_company_profile_bundle()
        runtime = _Runtime(
            (_FIXTURES / "employer_profile_official_site" / "response.html").read_text(
                encoding="utf-8"
            )
        )

        result = await bundle.execute(
            CompanyProfileInput(
                target_provider_id="hh_ru",
                company_profile_url="https://hh.ru/employer/9498112",
                source_company_id="9498112",
            ),
            runtime,
        )

        self.assertEqual(bundle.manifest.parser_type, ParserType.COMPANY_PROFILE)
        self.assertEqual(result.outcome, SingletonResultOutcome.SUCCESS)
        assert result.item is not None
        self.assertEqual(result.item.official_site_url, "https://crowd.yandex.ru/vacancies")

    async def test_company_site_is_an_independent_url_scraper(self) -> None:
        bundle = generic_company_site_bundle()
        runtime = _Runtime(
            '<html><a href="/careers">Careers</a><a href="mailto:jobs@example.com">Email</a></html>'
        )

        result = await bundle.execute(
            CompanySiteInput(site_url="https://example.com"),
            runtime,
        )

        self.assertEqual(bundle.manifest.parser_type, ParserType.COMPANY_SITE)
        self.assertEqual(result.outcome, SingletonResultOutcome.SUCCESS)
        assert result.item is not None
        self.assertEqual(result.item.career_endpoints[0].url, "https://example.com/careers")
        self.assertEqual(result.item.contacts[0].value, "jobs@example.com")


if __name__ == "__main__":
    unittest.main()
