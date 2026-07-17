from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import cast

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanySiteInput,
    CompensationCriterion,
    CompensationPeriod,
    ParserFixtureKind,
    ParserType,
    RawListing,
    RemoteScope,
    SearchRequest,
    SearchResultOutcome,
    SearchScraperBundle,
    SingletonResultOutcome,
    SourceScraper,
    VacancyDetailInput,
    VacancyDetailResult,
)
from job_harness.v2.ports import HttpAction, HttpResponse, ParserAttemptMetrics, ParserRuntime, RetrySafety
from job_harness.v2.runtime.errors import HttpStatusError
from job_harness.v2.runtime.source_bundles import (
    _detail_output,
    _listing_output,
    detail_bundle,
    discovered_ats_search_bundle,
    generic_company_site_bundle,
    hh_company_profile_bundle,
    search_bundle,
)
from job_harness.v2.runtime.source_registry import build_independent_parser_registry
from job_harness.v2.runtime.sources import HhRuSource, TalantoSource, TalentoSource
from job_harness.v2.runtime.sources.aggregators.hh_ru import hh_employer_profile_locations
from job_harness.v2.serialization import to_jsonable
from job_harness.v2.source_catalog import source_catalog_entry, source_fixture_suite

_FIXTURES = Path(__file__).parent / "fixtures" / "scrapers" / "hh_ru"
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _search_bundle(source: SourceScraper) -> SearchScraperBundle:
    source_id = source.descriptor.source_id
    return cast(
        SearchScraperBundle,
        search_bundle(source, source_catalog_entry(source_id).listing_parser_ref),
    )


class _Runtime(ParserRuntime):
    def __init__(self, response_body: str, *, media_type: str = "text/html") -> None:
        self._response_body = response_body
        self._media_type = media_type
        self.actions: list[HttpAction] = []

    @property
    def reserved_collection_units(self) -> int:
        return 1

    @property
    def attempt_metrics(self) -> ParserAttemptMetrics:
        return ParserAttemptMetrics()

    async def http(self, action: HttpAction) -> HttpResponse:
        self.actions.append(action)
        return HttpResponse(
            requested_url=action.url,
            final_url=action.url,
            status_code=200,
            media_type=self._media_type,
            body=self._response_body.encode(),
        )


class _HttpFailureRuntime(ParserRuntime):
    @property
    def reserved_collection_units(self) -> int:
        return 1

    @property
    def attempt_metrics(self) -> ParserAttemptMetrics:
        return ParserAttemptMetrics()

    async def http(self, action: HttpAction) -> HttpResponse:
        raise HttpStatusError(status_code=404, final_url=action.url)


class IndependentSourceBundleTest(unittest.IsolatedAsyncioTestCase):
    def test_listing_adapter_preserves_all_structured_locations(self) -> None:
        output = _listing_output(
            RawListing(
                source_listing_id="1",
                title="Data Analyst",
                url="https://example.com/jobs/1",
                source="test",
                location_text="London | Vilnius",
                location_cities=("London", "Vilnius"),
                location_countries=("GB", "LT"),
                location_regions=("EU",),
            ),
            target_provider_id="test",
        )

        assert output.location is not None
        self.assertEqual("London | Vilnius", output.location.text)
        self.assertEqual(("London", "Vilnius"), output.location.cities)
        self.assertEqual(("GB", "LT"), output.location.countries)
        self.assertEqual(("EU",), output.location.regions)

    def test_physical_locations_do_not_widen_remote_scope(self) -> None:
        output = _listing_output(
            RawListing(
                source_listing_id="2",
                title="Data Analyst",
                url="https://example.com/jobs/2",
                source="test",
                location_text="London, Vilnius; Remote, Germany",
                location_cities=("London", "Vilnius"),
                location_countries=("GB", "LT"),
                remote_in_country=True,
                remote_scope_countries=("DE",),
            ),
            target_provider_id="test",
        )

        self.assertEqual(
            (RemoteScope(kind="country", code="DE"),),
            output.remote_scopes,
        )

    def test_listing_adapter_preserves_relocation_support_and_destinations(self) -> None:
        output = _listing_output(
            RawListing(
                source_listing_id="relocation-1",
                title="AI Evaluation Engineer",
                url="https://example.com/jobs/relocation-1",
                source="test",
                relocation=True,
                relocation_destinations=("United States",),
            ),
            target_provider_id="test",
        )

        self.assertIs(output.relocation, True)
        self.assertEqual(1, len(output.relocation_destinations))
        self.assertEqual(("US",), output.relocation_destinations[0].countries)

    def test_listing_adapter_keeps_compensation_dimensions_and_normalizes_rur(self) -> None:
        output = _listing_output(
            RawListing(
                source_listing_id="3",
                title="QA Lead",
                url="https://example.com/jobs/3",
                source="test",
                salary_min=300_000,
                salary_max=400_000,
                salary_currency="RUR",
                salary_period="month",
                salary_gross=True,
            ),
            target_provider_id="test",
        )

        assert output.salary is not None
        self.assertEqual(300_000, output.salary.salary_from)
        self.assertEqual("RUB", output.salary.currency)
        self.assertEqual("month", output.salary.period)
        self.assertIs(output.salary.gross, True)

    def test_detail_adapter_preserves_provider_grade_evidence(self) -> None:
        detail = _detail_output(
            RawListing(
                source_listing_id="4",
                title="QA Engineer",
                url="https://example.com/jobs/4",
                source="test",
                native_grade="senior",
            ),
            VacancyDetailInput(
                target_provider_id="test",
                vacancy_url="https://example.com/jobs/4",
                source_listing_id="4",
            ),
        )

        self.assertEqual("senior", detail.native_grade)

    def test_query_mode_and_native_filters_follow_source_capabilities(self) -> None:
        registry = build_independent_parser_registry()
        manifests = {manifest.parser_id: manifest for manifest in registry.manifests()}

        self.assertEqual("per_query", manifests["hh_ru.search"].query_mode)
        self.assertEqual("downstream_only", manifests["career:vk.search"].query_mode)

        vk_bundle = cast(
            SearchScraperBundle,
            registry.get(manifests["career:vk.search"].ref),
        )
        inputs = vk_bundle.plan_initial(
            SearchRequest(
                query_variants=("Manual QA", "QA tester", "Тестировщик"),
                compensation=CompensationCriterion(100_000, "RUB", CompensationPeriod.MONTH),
            ),
            {"kind": "catalog"},
        )

        self.assertEqual(1, len(inputs))
        self.assertEqual(
            ("Manual QA", "QA tester", "Тестировщик"),
            inputs[0].queries,
        )
        self.assertEqual({}, inputs[0].native_filters)

    def test_production_manifests_declare_serialized_fact_paths(self) -> None:
        registry = build_independent_parser_registry()

        for manifest in registry.manifests():
            with self.subTest(parser_id=manifest.parser_id):
                self.assertTrue(all(item == item.casefold() for item in manifest.output_facts))
        detail_facts = {
            fact
            for manifest in registry.manifests()
            if manifest.parser_type == ParserType.VACANCY_DETAIL
            for fact in manifest.output_facts
        }
        self.assertIn("company", detail_facts)
        self.assertIn("application_channels", detail_facts)

    def test_mirrored_boards_share_one_vacancy_identity_namespace(self) -> None:
        talanto = _search_bundle(TalantoSource())
        talento = _search_bundle(TalentoSource())
        request = SearchRequest(query_variants=("AI quality",))

        talanto_input = talanto.plan_initial(request, {"kind": "catalog"})[0]
        talento_input = talento.plan_initial(request, {"kind": "catalog"})[0]

        self.assertEqual("talento-network", talanto_input.target_provider_id)
        self.assertEqual(talanto_input.target_provider_id, talento_input.target_provider_id)

    async def test_hh_listing_bundle_uses_clean_typed_contract(self) -> None:
        bundle = _search_bundle(HhRuSource())
        inputs = bundle.plan_initial(SearchRequest(query_variants=("QA",)), {"kind": "catalog"})
        runtime = _Runtime((_FIXTURES / "success" / "response.html").read_text(encoding="utf-8"))

        result = await bundle.execute(inputs[0], runtime)

        self.assertEqual(runtime.actions[0].retry_safety, RetrySafety.SAFE)
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
        self.assertEqual(
            (("country", "RU"),),
            tuple((scope.kind, scope.code) for scope in first.remote_scopes),
        )
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
                "relocation",
                "relocation_destinations",
            },
        )
        self.assertNotIn("timeout", to_jsonable(inputs[0]))
        self.assertNotIn("request_delay", to_jsonable(inputs[0]))

    async def test_hh_listing_ignores_malformed_optional_company_site_url(self) -> None:
        bundle = _search_bundle(HhRuSource())
        parser_input = bundle.plan_initial(
            SearchRequest(query_variants=("QA",)),
            {"kind": "catalog"},
        )[0]
        body = (_FIXTURES / "success" / "response.html").read_text(encoding="utf-8")
        body = body.replace(
            '"companySiteUrl":"https://windi.com"',
            '"companySiteUrl":"http://"',
            1,
        )

        try:
            result = await bundle.execute(parser_input, _Runtime(body))
        except ValueError as exc:
            self.fail(f"one malformed optional company URL rejected the HH page: {exc}")

        self.assertEqual(result.outcome, SearchResultOutcome.SUCCESS)
        self.assertEqual(len(result.items), 50)
        assert result.items[0].company is not None
        self.assertIsNone(result.items[0].company.official_site_url)

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

        self.assertEqual(runtime.actions[0].retry_safety, RetrySafety.SAFE)
        self.assertEqual(bundle.manifest.parser_type, ParserType.VACANCY_DETAIL)
        self.assertEqual(result.outcome, SingletonResultOutcome.SUCCESS)
        self.assertIsNotNone(result.item)
        assert result.item is not None
        detail_payload = to_jsonable(result.item)
        self.assertIn("description", detail_payload)
        self.assertIsNotNone(result.item.location)
        self.assertIn("location", bundle.manifest.output_facts)
        self.assertNotIn("raw", detail_payload)

    async def test_every_detail_bundle_executes_from_public_url_without_listing_snapshot(self) -> None:
        registry = build_independent_parser_registry()
        detail_manifests = tuple(
            manifest
            for manifest in registry.manifests()
            if manifest.parser_type == ParserType.VACANCY_DETAIL
        )

        self.assertGreater(len(detail_manifests), 1)
        for manifest in detail_manifests:
            source_id = manifest.parser_id.removesuffix(".detail")
            fixture = next(
                case
                for case in source_fixture_suite(source_id).cases
                if case.kind == ParserFixtureKind.DETAIL
            )
            response_path = _PLUGIN_ROOT / fixture.captured_artifact_path
            metadata = json.loads((_PLUGIN_ROOT / fixture.metadata_path).read_text(encoding="utf-8"))
            input_path = response_path.parent / "input.json"
            fixture_input = (
                json.loads(input_path.read_text(encoding="utf-8")) if input_path.exists() else metadata
            )
            vacancy_url = fixture_input["url"] if "url" in fixture_input else fixture_input["captured_url"]
            runtime = _Runtime(
                response_path.read_text(encoding="utf-8"),
                media_type="application/json" if response_path.suffix == ".json" else "text/html",
            )

            with self.subTest(parser_id=manifest.parser_id):
                result = await registry.get(manifest.ref).execute(
                    VacancyDetailInput(
                        target_provider_id=source_id,
                        vacancy_url=vacancy_url,
                        source_listing_id=None,
                    ),
                    runtime,
                )

                assert isinstance(result, VacancyDetailResult)
                self.assertEqual(SingletonResultOutcome.SUCCESS, result.outcome)
                self.assertIsNotNone(result.item)

    async def test_detail_bundle_maps_missing_page_to_not_found(self) -> None:
        bundle = detail_bundle(HhRuSource())

        result = await bundle.execute(
            VacancyDetailInput(
                target_provider_id="hh_ru",
                vacancy_url="https://hh.ru/vacancy/missing",
                source_listing_id="missing",
            ),
            _HttpFailureRuntime(),
        )

        self.assertEqual(result.outcome, SingletonResultOutcome.NOT_FOUND)
        self.assertIsNone(result.item)

    async def test_hh_company_profile_is_an_independent_url_scraper(self) -> None:
        bundle = hh_company_profile_bundle(hh_employer_profile_locations)
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

        self.assertEqual(runtime.actions[0].retry_safety, RetrySafety.SAFE)
        self.assertEqual(bundle.manifest.parser_type, ParserType.COMPANY_PROFILE)
        self.assertEqual(result.outcome, SingletonResultOutcome.SUCCESS)
        assert result.item is not None
        self.assertEqual(result.item.official_site_url, "https://crowd.yandex.ru/vacancies")
        self.assertEqual(
            {"Москва", "RU"},
            {location.text for location in result.item.locations},
        )
        self.assertIn("locations", bundle.manifest.output_facts)

    async def test_company_site_is_an_independent_url_scraper(self) -> None:
        bundle = generic_company_site_bundle()
        runtime = _Runtime(
            '<html><a href="/careers">Careers</a><a href="mailto:jobs@example.com">Email</a></html>'
        )

        result = await bundle.execute(
            CompanySiteInput(site_url="https://example.com"),
            runtime,
        )

        self.assertEqual(runtime.actions[0].retry_safety, RetrySafety.SAFE)
        self.assertEqual(bundle.manifest.parser_type, ParserType.COMPANY_SITE)
        self.assertEqual(result.outcome, SingletonResultOutcome.SUCCESS)
        assert result.item is not None
        self.assertEqual(result.item.career_endpoints[0].url, "https://example.com/careers")
        self.assertEqual(result.item.contacts[0].value, "jobs@example.com")

    async def test_discovered_ats_bundle_executes_from_the_discovered_board_url(self) -> None:
        response_body = (
            _PLUGIN_ROOT
            / "tests/v2/fixtures/scrapers/career_appfollow/success/response.json"
        ).read_text(encoding="utf-8")
        bundle = discovered_ats_search_bundle()
        target = {"kind": "discovered_url", "url": "https://jobs.lever.co/appfollow"}
        parser_input = bundle.plan_initial(
            SearchRequest(query_variants=("AI lead",)),
            target,
        )[0]
        runtime = _Runtime(response_body, media_type="application/json")

        result = await bundle.execute(parser_input, runtime)

        self.assertEqual(result.outcome, SearchResultOutcome.SUCCESS)
        self.assertEqual(len(result.items), 3)
        self.assertEqual(
            runtime.actions[0].url,
            "https://api.lever.co/v0/postings/appfollow?mode=json",
        )
        self.assertTrue(all(item.source_id == parser_input.source_id for item in result.items))
        self.assertTrue(
            all(item.target_provider_id == parser_input.target_provider_id for item in result.items)
        )


if __name__ == "__main__":
    unittest.main()
