from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from job_harness.v2.contracts import (
    Grade,
    SearchRequest,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
)
from job_harness.v2.runtime import (
    OrchestratorConfig,
    RawCorpusWriter,
    RetryPolicy,
    SearchOrchestrator,
    SourceCatalog,
    SupportedSource,
)
from job_harness.v2.runtime.sources import (
    FinderWorkSource,
    GeekJobSource,
    GetmatchSource,
    HabrCareerSource,
    HhRuSource,
    HirifySource,
    ItJobsUzSource,
    JetBrainsCareerSource,
    JobTurboSource,
    TalantoSource,
    TalentoSource,
    VKCareerSource,
)
from job_harness.v2.source_catalog import source_fixture_suite

_FIXTURES = Path(__file__).parent / "fixtures" / "scrapers"
_HABR_QA_URL = "https://career.habr.com/vacancies?q=QA&type=all"
_HABR_QA_PAGE_2_URL = "https://career.habr.com/vacancies?q=QA&type=all&page=2"
_HH_QA_URL = "https://hh.ru/search/vacancy?text=QA&area=113&search_field=name"
_HH_QA_PAGE_1_URL = "https://hh.ru/search/vacancy?text=QA&area=113&search_field=name&page=1"
_TALANTO_QA_URL = "https://talanto.work/?q=QA"
_VK_QA_URL = "https://team.vk.company/vacancy/?specialty=284"
_JETBRAINS_URL = "https://boards-api.greenhouse.io/v1/boards/jetbrains/jobs?content=true"
_GEEKJOB_URL = "https://geekjob.ru/vacancies"
_TALENTO_WORKS_QA_URL = "https://talento.works/?q=QA"
_FINDER_WORK_QA_URL = "https://api.finder.work/api/v1/vacancies?search=QA"
_GETMATCH_SPECIALIZATIONS_URL = "https://getmatch.ru/api/specializations"
_GETMATCH_OFFERS_QA_AUTO_URL = "https://getmatch.ru/api/offers?sa=any&p=1&offset=0&limit=100&pa=all&sp=qa_auto"
_GETMATCH_OFFERS_QA_MANUAL_URL = "https://getmatch.ru/api/offers?sa=any&p=1&offset=0&limit=100&pa=all&sp=qa_manual"
_GETMATCH_OFFERS_LOAD_PERFORMANCE_URL = (
    "https://getmatch.ru/api/offers?sa=any&p=1&offset=0&limit=100&pa=all&sp=load_performance"
)
_IT_JOBS_UZ_QA_URL = "https://www.it-jobs.uz/api/jobs?search=QA&limit=100&page=1&category=qa"
_HIRIFY_QA_URL = "https://api.hirify.me/api/vacancies?search=QA&page=1"
_JOBTURBO_URL = "https://jobturbo.ru/vakansii/remote"
_NO_RESULTS_QUERY = "zzzzzz-no-such-job-20260622"
_GEEKJOB_NO_RESULTS_QUERY = "zzzzzzzzzzzzzzzz"
_HABR_NO_RESULTS_URL = f"https://career.habr.com/vacancies?q={_NO_RESULTS_QUERY}&type=all"
_HH_NO_RESULTS_URL = f"https://hh.ru/search/vacancy?text={_NO_RESULTS_QUERY}&area=113&search_field=name"
_TALANTO_NO_RESULTS_URL = f"https://talanto.work/?q={_NO_RESULTS_QUERY}"
_VK_NO_RESULTS_URL = f"https://team.vk.company/vacancy/?search={_NO_RESULTS_QUERY}"


class FixtureFetcher:
    def __init__(self, mapping: dict[str, Path]) -> None:
        self._mapping = mapping
        self.calls: list[SourceFetchRequest] = []

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        self.calls.append(request)
        try:
            path = self._mapping[request.url]
        except KeyError as exc:
            raise AssertionError(f"unexpected fixture URL: {request.url}") from exc
        return SourceResponseArtifact(
            source_id=request.source_id,
            url=request.url,
            media_type="text/html",
            body=path.read_text(encoding="utf-8"),
        )


def _fixture_response(source: str, case: str) -> SourceResponseArtifact:
    path = _fixture_response_path(source, case)
    source_id = _source_id(source)
    return SourceResponseArtifact(
        source_id=source_id,
        url=f"https://fixture.test/{source}/{case}",
        media_type="application/json" if path.suffix == ".json" else "text/html",
        body=path.read_text(encoding="utf-8"),
    )


def _fixture_response_path(source: str, case: str) -> Path:
    fixture_dir = _FIXTURES / source / case
    json_path = fixture_dir / "response.json"
    if json_path.exists():
        return json_path
    return fixture_dir / "response.html"


def _source_id(source: str) -> str:
    if source == "career_vk":
        return "career:vk"
    if source == "career_jetbrains":
        return "career:jetbrains"
    return source


def _expected(source: str, case: str) -> dict[str, Any]:
    value = json.loads((_FIXTURES / source / case / "expected.raw.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("expected.raw.json must contain a JSON object")
    return cast(dict[str, Any], value)


def _listing_by_id(listings: tuple[Any, ...], source_listing_id: str) -> Any:
    for listing in listings:
        if listing.source_listing_id == source_listing_id:
            return listing
    raise AssertionError(f"listing not found: {source_listing_id}")


def _assert_listing_matches(test: unittest.TestCase, listing: Any, expected: dict[str, Any]) -> None:
    for field in (
        "source_listing_id",
        "title",
        "url",
        "company",
        "country",
        "city",
        "location_text",
        "salary_text",
        "salary_min",
        "salary_max",
        "salary_currency",
        "posted_at",
        "remote_in_country",
        "remote_global",
        "native_grade",
    ):
        if field in expected:
            test.assertEqual(expected[field], getattr(listing, field), field)
    if "skills" in expected:
        test.assertEqual(tuple(expected["skills"]), listing.skills)


class HabrCareerSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=HabrCareerSource(),
            fixture_suite=source_fixture_suite("habr_career"),
        )

        # Assert
        self.assertEqual("habr_career", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query_grade_and_salary(self) -> None:
        # Arrange
        source = HabrCareerSource()
        request = SearchRequest(
            query_variants=("QA Engineer",),
            grades=(Grade.MIDDLE,),
            salary_from=200000,
        )

        # Act
        fetch_request = source.build_search_requests(request)[0]

        # Assert
        self.assertEqual(
            "https://career.habr.com/vacancies?q=QA+Engineer&type=all&qualification=middle&salary=200000",
            fetch_request.url,
        )

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = HabrCareerSource()
        response = _fixture_response("habr_career", "success")
        fetch_request = SourceFetchRequest(
            source_id="habr_career",
            query_variant="QA",
            url="https://career.habr.com/vacancies?q=QA&type=all",
        )
        expected = _expected("habr_career", "success")

        # Act
        parsed = source.parse_search_response(response, fetch_request)

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertIsNotNone(parsed.next_request)
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_pagination_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = HabrCareerSource()
        response = _fixture_response("habr_career", "pagination")
        fetch_request = SourceFetchRequest(
            source_id="habr_career",
            query_variant="QA",
            url="https://career.habr.com/vacancies?q=QA&type=all&page=2",
        )
        expected = _expected("habr_career", "pagination")

        # Act
        parsed = source.parse_search_response(response, fetch_request)

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = HabrCareerSource()
        response = _fixture_response("habr_career", "no_results")
        fetch_request = SourceFetchRequest(
            source_id="habr_career",
            query_variant="zzzzzz-no-such-job-20260622",
            url="https://career.habr.com/vacancies?q=zzzzzz-no-such-job-20260622&type=all",
        )

        # Act
        parsed = source.parse_search_response(response, fetch_request)

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)

    def test_detail_fixture_extracts_real_description_text(self) -> None:
        # Arrange
        source = HabrCareerSource()
        listing = _listing_by_id(
            source.parse_search_response(
                _fixture_response("habr_career", "success"),
                SourceFetchRequest(
                    source_id="habr_career",
                    query_variant="QA",
                    url="https://career.habr.com/vacancies?q=QA&type=all",
                ),
            ).listings,
            "1000165585",
        )
        expected = _expected("habr_career", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("habr_career", "detail"), listing)

        # Assert
        self.assertIsNotNone(detailed.description)
        for text in expected["description_contains"]:
            self.assertIn(text, detailed.description or "")

    def test_optional_fields_are_preserved_as_source_facts(self) -> None:
        # Arrange
        source = HabrCareerSource()
        expected = _expected("habr_career", "optional_fields")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("habr_career", "success"),
            SourceFetchRequest(
                source_id="habr_career",
                query_variant="QA",
                url="https://career.habr.com/vacancies?q=QA&type=all",
            ),
        )

        # Assert
        self.assertIsNotNone(_listing_by_id(parsed.listings, expected["salary_present_listing_id"]).salary_text)
        self.assertIsNone(_listing_by_id(parsed.listings, expected["salary_absent_listing_id"]).salary_text)
        self.assertIsNotNone(_listing_by_id(parsed.listings, expected["location_present_listing_id"]).location_text)
        self.assertIsNone(_listing_by_id(parsed.listings, expected["location_absent_listing_id"]).location_text)


class HhRuSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=HhRuSource(),
            fixture_suite=source_fixture_suite("hh_ru"),
        )

        # Assert
        self.assertEqual("hh_ru", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query_and_salary(self) -> None:
        # Arrange
        source = HhRuSource()
        request = SearchRequest(query_variants=("QA Engineer",), salary_from=200000)

        # Act
        fetch_request = source.build_search_requests(request)[0]

        # Assert
        self.assertEqual(
            "https://hh.ru/search/vacancy?text=QA+Engineer&area=113&search_field=name&salary=200000&only_with_salary=true",
            fetch_request.url,
        )

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = HhRuSource()
        response = _fixture_response("hh_ru", "success")
        fetch_request = SourceFetchRequest(
            source_id="hh_ru",
            query_variant="QA",
            url="https://hh.ru/search/vacancy?text=QA&area=113&search_field=name",
        )
        expected = _expected("hh_ru", "success")

        # Act
        parsed = source.parse_search_response(response, fetch_request)

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertIsNotNone(parsed.next_request)
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_pagination_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = HhRuSource()
        response = _fixture_response("hh_ru", "pagination")
        fetch_request = SourceFetchRequest(
            source_id="hh_ru",
            query_variant="QA",
            url="https://hh.ru/search/vacancy?text=QA&area=113&search_field=name&page=1",
        )
        expected = _expected("hh_ru", "pagination")

        # Act
        parsed = source.parse_search_response(response, fetch_request)

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = HhRuSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("hh_ru", "no_results"),
            SourceFetchRequest(
                source_id="hh_ru",
                query_variant="zzzzzz-no-such-job-20260622",
                url="https://hh.ru/search/vacancy?text=zzzzzz-no-such-job-20260622&area=113&search_field=name",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)

    def test_optional_fields_are_preserved_as_source_facts(self) -> None:
        # Arrange
        source = HhRuSource()
        expected = _expected("hh_ru", "optional_fields")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("hh_ru", "success"),
            SourceFetchRequest(
                source_id="hh_ru",
                query_variant="QA",
                url="https://hh.ru/search/vacancy?text=QA&area=113&search_field=name",
            ),
        )

        # Assert
        self.assertIsNotNone(_listing_by_id(parsed.listings, expected["salary_present_listing_id"]).salary_text)
        self.assertIsNone(_listing_by_id(parsed.listings, expected["salary_absent_listing_id"]).salary_text)
        self.assertTrue(_listing_by_id(parsed.listings, expected["remote_present_listing_id"]).remote_in_country)
        self.assertFalse(_listing_by_id(parsed.listings, expected["remote_absent_listing_id"]).remote_in_country)
        self.assertIn(",", _listing_by_id(parsed.listings, expected["address_present_listing_id"]).location_text or "")
        self.assertEqual(
            _listing_by_id(parsed.listings, expected["city_only_listing_id"]).city,
            _listing_by_id(parsed.listings, expected["city_only_listing_id"]).location_text,
        )


class VKCareerSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=VKCareerSource(),
            fixture_suite=source_fixture_suite("career:vk"),
        )

        # Assert
        self.assertEqual("career:vk", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_specialty_for_qa_and_search_for_unknown_query(self) -> None:
        # Arrange
        source = VKCareerSource()

        # Act
        qa_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]
        unknown_request = source.build_search_requests(SearchRequest(query_variants=("exotic role",)))[0]

        # Assert
        self.assertEqual("https://team.vk.company/vacancy/?specialty=284", qa_request.url)
        self.assertEqual("https://team.vk.company/vacancy/?search=exotic+role", unknown_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = VKCareerSource()
        expected = _expected("career_vk", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("career_vk", "success"),
            SourceFetchRequest(
                source_id="career:vk",
                query_variant="QA",
                url="https://team.vk.company/vacancy/?specialty=284",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = VKCareerSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("career_vk", "no_results"),
            SourceFetchRequest(
                source_id="career:vk",
                query_variant="zzzzzz-no-such-job-20260622",
                url="https://team.vk.company/vacancy/?search=zzzzzz-no-such-job-20260622",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class TalantoSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=TalantoSource(),
            fixture_suite=source_fixture_suite("talanto"),
        )

        # Assert
        self.assertEqual("talanto", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query(self) -> None:
        # Arrange
        source = TalantoSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA Engineer",)))[0]

        # Assert
        self.assertEqual("talanto", fetch_request.source_id)
        self.assertEqual("QA Engineer", fetch_request.query_variant)
        self.assertEqual("https://talanto.work/?q=QA+Engineer", fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = TalantoSource()
        expected = _expected("talanto", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("talanto", "success"),
            SourceFetchRequest(
                source_id="talanto",
                query_variant="QA",
                url="https://talanto.work/?q=QA",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = TalantoSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("talanto", "no_results"),
            SourceFetchRequest(
                source_id="talanto",
                query_variant="zzzzzz-no-such-job-20260622",
                url="https://talanto.work/?q=zzzzzz-no-such-job-20260622",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class GeekJobSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=GeekJobSource(),
            fixture_suite=source_fixture_suite("geekjob"),
        )

        # Assert
        self.assertEqual("geekjob", source.scraper.descriptor.source_id)

    def test_request_mapping_fetches_the_public_vacancies_page(self) -> None:
        # Arrange
        source = GeekJobSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("Cloud",)))[0]

        # Assert
        self.assertEqual("geekjob", fetch_request.source_id)
        self.assertEqual("Cloud", fetch_request.query_variant)
        self.assertEqual(_GEEKJOB_URL, fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = GeekJobSource()
        expected = _expected("geekjob", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("geekjob", "success"),
            SourceFetchRequest(
                source_id="geekjob",
                query_variant="Cloud",
                url=_GEEKJOB_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = GeekJobSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("geekjob", "no_results"),
            SourceFetchRequest(
                source_id="geekjob",
                query_variant=_GEEKJOB_NO_RESULTS_QUERY,
                url=_GEEKJOB_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class TalentoSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=TalentoSource(),
            fixture_suite=source_fixture_suite("talento"),
        )

        # Assert
        self.assertEqual("talento", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query(self) -> None:
        # Arrange
        source = TalentoSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]

        # Assert
        self.assertEqual("talento", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(_TALENTO_WORKS_QA_URL, fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = TalentoSource()
        expected = _expected("talento", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("talento", "success"),
            SourceFetchRequest(
                source_id="talento",
                query_variant="QA",
                url=_TALENTO_WORKS_QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = TalentoSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("talento", "no_results"),
            SourceFetchRequest(
                source_id="talento",
                query_variant="zzzzzzzzzzzzzzzz",
                url="https://talento.works/?q=zzzzzzzzzzzzzzzz",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class FinderWorkSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=FinderWorkSource(),
            fixture_suite=source_fixture_suite("finder_work"),
        )

        # Assert
        self.assertEqual("finder_work", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query(self) -> None:
        # Arrange
        source = FinderWorkSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]

        # Assert
        self.assertEqual("finder_work", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(_FINDER_WORK_QA_URL, fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = FinderWorkSource()
        expected = _expected("finder_work", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("finder_work", "success"),
            SourceFetchRequest(
                source_id="finder_work",
                query_variant="QA",
                url=_FINDER_WORK_QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = FinderWorkSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("finder_work", "no_results"),
            SourceFetchRequest(
                source_id="finder_work",
                query_variant="zzzzzzzzzzzzzzzz",
                url="https://api.finder.work/api/v1/vacancies?search=zzzzzzzzzzzzzzzz",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class GetmatchSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=GetmatchSource(),
            fixture_suite=source_fixture_suite("getmatch"),
        )

        # Assert
        self.assertEqual("getmatch", source.scraper.descriptor.source_id)

    def test_request_mapping_starts_with_specializations(self) -> None:
        # Arrange
        source = GetmatchSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]

        # Assert
        self.assertEqual("getmatch", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(_GETMATCH_SPECIALIZATIONS_URL, fetch_request.url)

    def test_specializations_fixture_chains_qa_offer_requests(self) -> None:
        # Arrange
        source = GetmatchSource()
        specializations_path = _FIXTURES / "getmatch" / "success" / "specializations.json"

        # Act
        parsed = source.parse_search_response(
            SourceResponseArtifact(
                source_id="getmatch",
                url=_GETMATCH_SPECIALIZATIONS_URL,
                media_type="application/json",
                body=specializations_path.read_text(encoding="utf-8"),
            ),
            SourceFetchRequest(
                source_id="getmatch",
                query_variant="QA",
                url=_GETMATCH_SPECIALIZATIONS_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertIsNotNone(parsed.next_request)
        assert parsed.next_request is not None
        self.assertEqual(_GETMATCH_OFFERS_QA_AUTO_URL, parsed.next_request.url)
        self.assertEqual("qa_manual,load_performance", parsed.next_request.headers["x-getmatch-pending-slugs"])

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = GetmatchSource()
        expected = _expected("getmatch", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("getmatch", "success"),
            SourceFetchRequest(
                source_id="getmatch",
                query_variant="QA",
                url=_GETMATCH_OFFERS_QA_AUTO_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = GetmatchSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("getmatch", "no_results"),
            SourceFetchRequest(
                source_id="getmatch",
                query_variant="zzzzzzzzzzzzzzzz",
                url=_GETMATCH_OFFERS_QA_AUTO_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class ItJobsUzSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=ItJobsUzSource(),
            fixture_suite=source_fixture_suite("it_jobs_uz"),
        )

        # Assert
        self.assertEqual("it_jobs_uz", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query_and_category(self) -> None:
        # Arrange
        source = ItJobsUzSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]

        # Assert
        self.assertEqual("it_jobs_uz", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(_IT_JOBS_UZ_QA_URL, fetch_request.url)

    def test_success_fixture_chains_next_page_when_api_reports_more_pages(self) -> None:
        # Arrange
        source = ItJobsUzSource()
        payload = {
            "data": [],
            "total": 40,
            "page": 1,
            "limit": 100,
            "totalPages": 2,
        }

        # Act
        parsed = source.parse_search_response(
            SourceResponseArtifact(
                source_id="it_jobs_uz",
                url=_IT_JOBS_UZ_QA_URL,
                media_type="application/json",
                body=json.dumps(payload),
            ),
            SourceFetchRequest(
                source_id="it_jobs_uz",
                query_variant="QA",
                url=_IT_JOBS_UZ_QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertIsNotNone(parsed.next_request)
        assert parsed.next_request is not None
        self.assertIn("page=2", parsed.next_request.url)
        self.assertIn("limit=100", parsed.next_request.url)

    def test_client_filter_rejects_page_without_chaining_more_pages(self) -> None:
        # Arrange
        source = ItJobsUzSource()
        payload = {
            "data": [{"id": 1, "title": "Backend Dev", "slug": "backend-dev"}],
            "total": 331,
            "page": 1,
            "limit": 100,
            "totalPages": 4,
        }

        # Act
        parsed = source.parse_search_response(
            SourceResponseArtifact(
                source_id="it_jobs_uz",
                url="https://www.it-jobs.uz/api/jobs?search=zzzzzzzzzzzzzzzz&limit=100&page=1",
                media_type="application/json",
                body=json.dumps(payload),
            ),
            SourceFetchRequest(
                source_id="it_jobs_uz",
                query_variant="zzzzzzzzzzzzzzzz",
                url="https://www.it-jobs.uz/api/jobs?search=zzzzzzzzzzzzzzzz&limit=100&page=1",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertIsNone(parsed.next_request)
        self.assertTrue(parsed.evidence.no_results)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = ItJobsUzSource()
        expected = _expected("it_jobs_uz", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("it_jobs_uz", "success"),
            SourceFetchRequest(
                source_id="it_jobs_uz",
                query_variant="QA",
                url=_IT_JOBS_UZ_QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = ItJobsUzSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("it_jobs_uz", "no_results"),
            SourceFetchRequest(
                source_id="it_jobs_uz",
                query_variant="zzzzzzzzzzzzzzzz",
                url="https://www.it-jobs.uz/api/jobs?search=zzzzzzzzzzzzzzzz&limit=100&page=1",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class HirifySourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=HirifySource(),
            fixture_suite=source_fixture_suite("hirify"),
        )

        # Assert
        self.assertEqual("hirify", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query_and_page(self) -> None:
        # Arrange
        source = HirifySource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]

        # Assert
        self.assertEqual("hirify", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(_HIRIFY_QA_URL, fetch_request.url)

    def test_success_fixture_chains_next_page_when_api_reports_more_pages(self) -> None:
        # Arrange
        source = HirifySource()
        payload = {
            "data": [],
            "total": 40,
            "current_page": 1,
            "last_page": 3,
        }

        # Act
        parsed = source.parse_search_response(
            SourceResponseArtifact(
                source_id="hirify",
                url=_HIRIFY_QA_URL,
                media_type="application/json",
                body=json.dumps(payload),
            ),
            SourceFetchRequest(
                source_id="hirify",
                query_variant="QA",
                url=_HIRIFY_QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertIsNotNone(parsed.next_request)
        assert parsed.next_request is not None
        self.assertIn("page=2", parsed.next_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = HirifySource()
        expected = _expected("hirify", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("hirify", "success"),
            SourceFetchRequest(
                source_id="hirify",
                query_variant="QA",
                url=_HIRIFY_QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = HirifySource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("hirify", "no_results"),
            SourceFetchRequest(
                source_id="hirify",
                query_variant="zzzzzzzzzzzzzzzz",
                url="https://api.hirify.me/api/vacancies?search=zzzzzzzzzzzzzzzz&page=1",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class JobTurboSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=JobTurboSource(),
            fixture_suite=source_fixture_suite("jobturbo"),
        )

        # Assert
        self.assertEqual("jobturbo", source.scraper.descriptor.source_id)

    def test_request_mapping_fetches_the_remote_listings_page(self) -> None:
        # Arrange
        source = JobTurboSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]

        # Assert
        self.assertEqual("jobturbo", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(_JOBTURBO_URL, fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = JobTurboSource()
        expected = _expected("jobturbo", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("jobturbo", "success"),
            SourceFetchRequest(
                source_id="jobturbo",
                query_variant="QA",
                url=_JOBTURBO_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = JobTurboSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("jobturbo", "no_results"),
            SourceFetchRequest(
                source_id="jobturbo",
                query_variant="zzzzzzzzzzzzzzzz",
                url=_JOBTURBO_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class JetBrainsCareerSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=JetBrainsCareerSource(),
            fixture_suite=source_fixture_suite("career:jetbrains"),
        )

        # Assert
        self.assertEqual("career:jetbrains", source.scraper.descriptor.source_id)

    def test_request_mapping_fetches_the_real_greenhouse_board(self) -> None:
        # Arrange
        source = JetBrainsCareerSource()

        # Act
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]

        # Assert
        self.assertEqual("career:jetbrains", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(_JETBRAINS_URL, fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = JetBrainsCareerSource()
        expected = _expected("career_jetbrains", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("career_jetbrains", "success"),
            SourceFetchRequest(
                source_id="career:jetbrains",
                query_variant="QA",
                url=_JETBRAINS_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

        description_expectations = expected["description_contains"]
        for source_listing_id, phrases in description_expectations.items():
            with self.subTest(source_listing_id=source_listing_id):
                listing = _listing_by_id(parsed.listings, source_listing_id)
                self.assertIsNotNone(listing.description)
                for phrase in phrases:
                    self.assertIn(phrase, listing.description or "")


class ContractFirstRuntimeE2ETest(unittest.IsolatedAsyncioTestCase):
    async def test_new_runtime_runs_real_parser_fixtures(self) -> None:
        # Arrange
        habr = HabrCareerSource()
        hh = HhRuSource()
        talanto = TalantoSource()
        vk = VKCareerSource()
        jetbrains = JetBrainsCareerSource()
        geekjob = GeekJobSource()
        talento = TalentoSource()
        finder_work = FinderWorkSource()
        getmatch = GetmatchSource()
        it_jobs_uz = ItJobsUzSource()
        hirify = HirifySource()
        jobturbo = JobTurboSource()
        fetcher = FixtureFetcher(
            {
                _HABR_QA_URL: _FIXTURES / "habr_career" / "success" / "response.html",
                _HABR_QA_PAGE_2_URL: _FIXTURES / "habr_career" / "pagination" / "response.html",
                _HH_QA_URL: _FIXTURES / "hh_ru" / "success" / "response.html",
                _HH_QA_PAGE_1_URL: _FIXTURES / "hh_ru" / "pagination" / "response.html",
                _TALANTO_QA_URL: _FIXTURES / "talanto" / "success" / "response.html",
                _VK_QA_URL: _FIXTURES / "career_vk" / "success" / "response.html",
                _JETBRAINS_URL: _FIXTURES / "career_jetbrains" / "success" / "response.json",
                _GEEKJOB_URL: _FIXTURES / "geekjob" / "success" / "response.html",
                _TALENTO_WORKS_QA_URL: _FIXTURES / "talento" / "success" / "response.html",
                _FINDER_WORK_QA_URL: _FIXTURES / "finder_work" / "success" / "response.json",
                _GETMATCH_SPECIALIZATIONS_URL: _FIXTURES / "getmatch" / "success" / "specializations.json",
                _GETMATCH_OFFERS_QA_AUTO_URL: _FIXTURES / "getmatch" / "success" / "response.json",
                _GETMATCH_OFFERS_QA_MANUAL_URL: _FIXTURES / "getmatch" / "success" / "response.json",
                _GETMATCH_OFFERS_LOAD_PERFORMANCE_URL: _FIXTURES / "getmatch" / "success" / "response.json",
                _IT_JOBS_UZ_QA_URL: _FIXTURES / "it_jobs_uz" / "success" / "response.json",
                **{
                    f"https://api.hirify.me/api/vacancies?search=QA&page={page}": _FIXTURES
                    / "hirify"
                    / "success"
                    / "response.json"
                    for page in range(1, 8)
                },
                _JOBTURBO_URL: _FIXTURES / "jobturbo" / "success" / "response.html",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with RawCorpusWriter(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog(
                        (
                            SupportedSource(scraper=habr, fixture_suite=source_fixture_suite("habr_career")),
                            SupportedSource(scraper=hh, fixture_suite=source_fixture_suite("hh_ru")),
                            SupportedSource(scraper=talanto, fixture_suite=source_fixture_suite("talanto")),
                            SupportedSource(scraper=vk, fixture_suite=source_fixture_suite("career:vk")),
                            SupportedSource(
                                scraper=jetbrains,
                                fixture_suite=source_fixture_suite("career:jetbrains"),
                            ),
                            SupportedSource(
                                scraper=geekjob,
                                fixture_suite=source_fixture_suite("geekjob"),
                            ),
                            SupportedSource(
                                scraper=talento,
                                fixture_suite=source_fixture_suite("talento"),
                            ),
                            SupportedSource(
                                scraper=finder_work,
                                fixture_suite=source_fixture_suite("finder_work"),
                            ),
                            SupportedSource(
                                scraper=getmatch,
                                fixture_suite=source_fixture_suite("getmatch"),
                            ),
                            SupportedSource(
                                scraper=it_jobs_uz,
                                fixture_suite=source_fixture_suite("it_jobs_uz"),
                            ),
                            SupportedSource(
                                scraper=hirify,
                                fixture_suite=source_fixture_suite("hirify"),
                            ),
                            SupportedSource(
                                scraper=jobturbo,
                                fixture_suite=source_fixture_suite("jobturbo"),
                            ),
                        )
                    ),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            outcomes = {attempt.source: attempt for attempt in result.attempts}
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["habr_career"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["hh_ru"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["talanto"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["career:vk"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["career:jetbrains"].outcome)
            self.assertEqual(SourceOutcome.NO_RESULTS, outcomes["geekjob"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["talento"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["finder_work"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["getmatch"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["it_jobs_uz"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["hirify"].outcome)
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["jobturbo"].outcome)
            self.assertEqual(2, outcomes["habr_career"].counts.pages_visited)
            self.assertEqual(2, outcomes["hh_ru"].counts.pages_visited)
            self.assertEqual(1, outcomes["talanto"].counts.pages_visited)
            self.assertEqual(1, outcomes["career:vk"].counts.pages_visited)
            self.assertEqual(1, outcomes["career:jetbrains"].counts.pages_visited)
            self.assertEqual(1, outcomes["geekjob"].counts.pages_visited)
            self.assertEqual(1, outcomes["talento"].counts.pages_visited)
            self.assertEqual(1, outcomes["finder_work"].counts.pages_visited)
            self.assertEqual(4, outcomes["getmatch"].counts.pages_visited)
            self.assertEqual(1, outcomes["it_jobs_uz"].counts.pages_visited)
            self.assertEqual(7, outcomes["hirify"].counts.pages_visited)
            self.assertEqual(1, outcomes["jobturbo"].counts.pages_visited)
            self.assertEqual(380, result.raw_records_written)

            raw_records = [
                json.loads(line)
                for line in (Path(tmp) / "raw-listings.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(380, len(raw_records))
            self.assertEqual(
                {
                    "habr_career",
                    "hh_ru",
                    "talanto",
                    "career:vk",
                    "career:jetbrains",
                    "talento",
                    "finder_work",
                    "getmatch",
                    "it_jobs_uz",
                    "hirify",
                    "jobturbo",
                },
                {record["source"] for record in raw_records},
            )
            self.assertIn(
                "Тестировщик (QA) мобильного приложения Windi Messenger",
                {record["listing"]["title"] for record in raw_records},
            )

    async def test_new_runtime_records_explicit_no_results_without_raw_records(self) -> None:
        # Arrange
        habr = HabrCareerSource()
        hh = HhRuSource()
        talanto = TalantoSource()
        vk = VKCareerSource()
        fetcher = FixtureFetcher(
            {
                _HABR_NO_RESULTS_URL: _FIXTURES / "habr_career" / "no_results" / "response.html",
                _HH_NO_RESULTS_URL: _FIXTURES / "hh_ru" / "no_results" / "response.html",
                _TALANTO_NO_RESULTS_URL: _FIXTURES / "talanto" / "no_results" / "response.html",
                _VK_NO_RESULTS_URL: _FIXTURES / "career_vk" / "no_results" / "response.html",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with RawCorpusWriter(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog(
                        (
                            SupportedSource(scraper=habr, fixture_suite=source_fixture_suite("habr_career")),
                            SupportedSource(scraper=hh, fixture_suite=source_fixture_suite("hh_ru")),
                            SupportedSource(scraper=talanto, fixture_suite=source_fixture_suite("talanto")),
                            SupportedSource(scraper=vk, fixture_suite=source_fixture_suite("career:vk")),
                        )
                    ),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=(_NO_RESULTS_QUERY,)), run_id="r-test")

            # Assert
            self.assertEqual(0, result.raw_records_written)
            self.assertEqual(
                {
                    "habr_career": SourceOutcome.NO_RESULTS,
                    "hh_ru": SourceOutcome.NO_RESULTS,
                    "talanto": SourceOutcome.NO_RESULTS,
                    "career:vk": SourceOutcome.NO_RESULTS,
                },
                {attempt.source: attempt.outcome for attempt in result.attempts},
            )
            self.assertEqual("", (Path(tmp) / "raw-listings.jsonl").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
