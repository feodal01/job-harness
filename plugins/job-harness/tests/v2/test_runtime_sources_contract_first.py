from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from job_harness.v2.contracts import (
    DetailEnrichmentScraper,
    Grade,
    ParserFixtureCase,
    ParserFixtureKind,
    RawListing,
    RemoteMode,
    SearchRequest,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
)
from job_harness.v2.geography import normalize_source_geographies
from job_harness.v2.persistence import SqliteRunStore
from job_harness.v2.runtime import (
    ClassifiedSourceError,
    OrchestratorConfig,
    RetryPolicy,
    SearchOrchestrator,
    SourceCatalog,
    SupportedSource,
    build_supported_source_catalog,
)
from job_harness.v2.runtime.sources import (
    FinderWorkSource,
    GeekJobSource,
    GetmatchSource,
    HabrCareerSource,
    HhRuSource,
    HireHiSource,
    HirifySource,
    IBSCareerSource,
    ItJobsUzSource,
    JetBrainsCareerSource,
    JobTurboSource,
    StaffAmSource,
    TalantoSource,
    TalentoSource,
    VKCareerSource,
)
from job_harness.v2.source_catalog import source_fixture_suite

_PLUGIN_ROOT_PARENT_INDEX = 2
_PLUGIN_ROOT = Path(__file__).resolve().parents[_PLUGIN_ROOT_PARENT_INDEX]
_FIXTURES = Path(__file__).parent / "fixtures" / "scrapers"
_E2E_SUCCESS_QUERY = "QA"
_GLOBAL_REMOTE_EVIDENCE_MARKERS = ("global", "worldwide", "anywhere", "весь мир")
_GLOBAL_REMOTE_EVIDENCE_RAW_KEYS = frozenset(
    {
        "eligible_locations",
        "location",
        "locations",
        "remote_locations",
        "remote_restrictions",
        "remote_scope",
        "remote_type",
        "work_format",
    }
)
_REMOTE_IN_COUNTRY_EVIDENCE_RAW_KEYS = frozenset(
    {
        "city",
        "country",
        "country_text",
        "eligible_locations",
        "location",
        "locations",
        "region",
        "regions",
        "remote_locations",
        "remote_restrictions",
    }
)


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


@dataclass(frozen=True)
class _RuntimeNoResultsFixtureCase:
    source_id: str
    query_variant: str
    url: str
    response_path: Path


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
    if source == "career_ibs":
        return "career:ibs"
    return source


def _expected(source: str, case: str) -> dict[str, Any]:
    value = json.loads((_FIXTURES / source / case / "expected.raw.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("expected.raw.json must contain a JSON object")
    return cast(dict[str, Any], value)


def _detail_listing_from_input(source_folder: str, *, case: str = "detail") -> RawListing:
    payload = json.loads((_FIXTURES / source_folder / case / "input.json").read_text(encoding="utf-8"))
    source_id = _source_id(source_folder)
    return RawListing(
        source_listing_id=str(payload["source_listing_id"]),
        title=str(payload["title"]),
        url=str(payload["url"]),
        source=source_id,
    )


def _assert_detail_description_matches_expected(
    test: unittest.TestCase,
    *,
    detailed: RawListing,
    expected: dict[str, Any],
) -> None:
    test.assertIsNotNone(detailed.description)
    min_length = expected.get("min_description_length")
    if isinstance(min_length, int):
        test.assertGreaterEqual(len(detailed.description or ""), min_length)
    for text in expected.get("description_contains", []):
        test.assertIn(text, detailed.description or "")


def _fixture_case(source_id: str, kind: ParserFixtureKind) -> ParserFixtureCase | None:
    for case in source_fixture_suite(source_id).cases:
        if case.kind == kind:
            return case
    return None


def _fixture_cases(source_id: str, kind: ParserFixtureKind) -> tuple[ParserFixtureCase, ...]:
    return tuple(case for case in source_fixture_suite(source_id).cases if case.kind == kind)


def _required_fixture_case(source_id: str, kind: ParserFixtureKind) -> ParserFixtureCase:
    case = _fixture_case(source_id, kind)
    if case is None:
        raise AssertionError(f"{source_id} fixture suite does not include {kind.value}")
    return case


def _fixture_response_path_from_case(case: ParserFixtureCase) -> Path:
    return _PLUGIN_ROOT / case.captured_artifact_path


def _fixture_input_query_variant(case: ParserFixtureCase) -> str:
    input_path = _fixture_response_path_from_case(case).parent / "input.json"
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    query_variants = payload.get("query_variants")
    if not isinstance(query_variants, list) or len(query_variants) != 1 or not isinstance(query_variants[0], str):
        raise ValueError(f"fixture input must declare exactly one query variant: {input_path}")
    return query_variants[0]


def _fixture_captured_url(case: ParserFixtureCase) -> str:
    payload = json.loads((_PLUGIN_ROOT / case.metadata_path).read_text(encoding="utf-8"))
    captured_url = payload.get("captured_url")
    if not isinstance(captured_url, str) or not captured_url:
        raise ValueError(f"fixture metadata must declare captured_url: {case.metadata_path}")
    return captured_url


def _fixture_response_artifact(
    *,
    source_id: str,
    request: SourceFetchRequest,
    path: Path,
) -> SourceResponseArtifact:
    return SourceResponseArtifact(
        source_id=source_id,
        url=request.url,
        media_type="application/json" if path.suffix == ".json" else "text/html",
        body=path.read_text(encoding="utf-8"),
    )


def _extra_fixture_payload_paths(fixture_dir: Path) -> tuple[Path, ...]:
    reserved_names = {
        "expected.raw.json",
        "input.json",
        "meta.json",
        "response.html",
        "response.json",
    }
    return tuple(
        path
        for path in sorted(fixture_dir.iterdir())
        if path.is_file()
        and path.suffix in {".html", ".json"}
        and path.name not in reserved_names
    )


def _same_url_without_query(left: str, right: str) -> bool:
    left_parts = urlparse(left)
    right_parts = urlparse(right)
    return (
        left_parts.scheme,
        left_parts.netloc,
        left_parts.path,
    ) == (
        right_parts.scheme,
        right_parts.netloc,
        right_parts.path,
    )


def _runtime_no_results_fixture_cases() -> tuple[_RuntimeNoResultsFixtureCase, ...]:
    catalog = build_supported_source_catalog()
    cases: list[_RuntimeNoResultsFixtureCase] = []
    for source_id in catalog.source_ids:
        fixture_case = _fixture_case(source_id, ParserFixtureKind.NO_RESULTS)
        if fixture_case is None:
            continue

        query_variant = _fixture_input_query_variant(fixture_case)
        request = SearchRequest(query_variants=(query_variant,))
        fetch_requests = catalog.get(source_id).build_search_requests(request)
        if len(fetch_requests) != 1:
            continue

        fetch_request = fetch_requests[0]
        if fetch_request.url != _fixture_captured_url(fixture_case):
            continue

        cases.append(
            _RuntimeNoResultsFixtureCase(
                source_id=source_id,
                query_variant=query_variant,
                url=fetch_request.url,
                response_path=_PLUGIN_ROOT / fixture_case.captured_artifact_path,
            )
        )
    return tuple(cases)


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


def _has_explicit_global_remote_evidence(listing: RawListing) -> bool:
    if _value_mentions_global_remote((listing.location_text, listing.country, listing.city)):
        return True
    raw = listing.raw
    for key, value in raw.items():
        if key in _GLOBAL_REMOTE_EVIDENCE_RAW_KEYS and _value_mentions_global_remote(value):
            return True
    return False


def _value_mentions_global_remote(value: object) -> bool:
    if isinstance(value, str):
        text = value.casefold()
        return any(marker in text for marker in _GLOBAL_REMOTE_EVIDENCE_MARKERS)
    if isinstance(value, dict):
        return any(_value_mentions_global_remote(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_value_mentions_global_remote(item) for item in value)
    return False


def _has_remote_in_country_scope_evidence(listing: RawListing) -> bool:
    if _value_mentions_source_geography((listing.location_text, listing.country, listing.city)):
        return True
    raw = listing.raw
    for key, value in raw.items():
        if key in _REMOTE_IN_COUNTRY_EVIDENCE_RAW_KEYS and _value_mentions_source_geography(value):
            return True
    return False


def _value_mentions_source_geography(value: object) -> bool:
    if isinstance(value, str):
        return bool(normalize_source_geographies(value))
    if isinstance(value, dict):
        return any(_value_mentions_source_geography(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_value_mentions_source_geography(item) for item in value)
    return False


class RemoteGlobalEvidenceContractTest(unittest.TestCase):
    def test_success_fixtures_only_set_global_remote_with_explicit_source_evidence(self) -> None:
        # Arrange
        cases = (
            ("habr_career", HabrCareerSource()),
            ("hh_ru", HhRuSource()),
            ("career_vk", VKCareerSource()),
            ("career_ibs", IBSCareerSource()),
            ("talanto", TalantoSource()),
            ("geekjob", GeekJobSource()),
            ("talento", TalentoSource()),
            ("finder_work", FinderWorkSource()),
            ("getmatch", GetmatchSource()),
            ("it_jobs_uz", ItJobsUzSource()),
            ("hirify", HirifySource()),
            ("jobturbo", JobTurboSource()),
            ("hirehi", HireHiSource()),
            ("staff_am", StaffAmSource()),
            ("career_jetbrains", JetBrainsCareerSource()),
        )

        for fixture_folder, source in cases:
            with self.subTest(source=source.descriptor.source_id):
                fixture_case = _required_fixture_case(
                    source.descriptor.source_id,
                    ParserFixtureKind.SUCCESS_NON_EMPTY,
                )
                parsed = source.parse_search_response(
                    _fixture_response(fixture_folder, "success"),
                    SourceFetchRequest(
                        source_id=source.descriptor.source_id,
                        query_variant="QA",
                        url=_fixture_captured_url(fixture_case),
                    ),
                )

                # Assert
                for listing in parsed.listings:
                    if listing.remote_global is True:
                        self.assertTrue(
                            _has_explicit_global_remote_evidence(listing),
                            (
                                f"{listing.source}:{listing.source_listing_id} "
                                "sets remote_global without explicit evidence"
                            ),
                        )


class RemoteInCountryEvidenceContractTest(unittest.TestCase):
    def test_success_fixtures_only_set_remote_in_country_with_geography_evidence(self) -> None:
        # Arrange
        cases = (
            ("habr_career", HabrCareerSource()),
            ("hh_ru", HhRuSource()),
            ("career_vk", VKCareerSource()),
            ("career_ibs", IBSCareerSource()),
            ("talanto", TalantoSource()),
            ("geekjob", GeekJobSource()),
            ("talento", TalentoSource()),
            ("finder_work", FinderWorkSource()),
            ("getmatch", GetmatchSource()),
            ("it_jobs_uz", ItJobsUzSource()),
            ("hirify", HirifySource()),
            ("jobturbo", JobTurboSource()),
            ("hirehi", HireHiSource()),
            ("staff_am", StaffAmSource()),
            ("career_jetbrains", JetBrainsCareerSource()),
        )

        for fixture_folder, source in cases:
            with self.subTest(source=source.descriptor.source_id):
                fixture_case = _required_fixture_case(
                    source.descriptor.source_id,
                    ParserFixtureKind.SUCCESS_NON_EMPTY,
                )
                parsed = source.parse_search_response(
                    _fixture_response(fixture_folder, "success"),
                    SourceFetchRequest(
                        source_id=source.descriptor.source_id,
                        query_variant="QA",
                        url=_fixture_captured_url(fixture_case),
                    ),
                )

                # Assert
                for listing in parsed.listings:
                    if listing.remote_in_country is True:
                        self.assertTrue(
                            _has_remote_in_country_scope_evidence(listing),
                            (
                                f"{listing.source}:{listing.source_listing_id} "
                                "sets remote_in_country without geography evidence"
                            ),
                        )


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

    def test_detail_sectioned_fixture_extracts_banner_and_image_urls(self) -> None:
        # Arrange
        source = HabrCareerSource()
        listing = _detail_listing_from_input("habr_career", case="detail_sectioned")
        expected = _expected("habr_career", "detail_sectioned")

        # Act
        detailed = source.parse_detail_response(
            _fixture_response("habr_career", "detail_sectioned"),
            listing,
        )

        # Assert
        _assert_detail_description_matches_expected(self, detailed=detailed, expected=expected)

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

    def test_success_fixture_preserves_hybrid_work_format(self) -> None:
        # Arrange
        source = HhRuSource()

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
        hybrid_listing = _listing_by_id(parsed.listings, "134064926")
        self.assertEqual(("HYBRID",), hybrid_listing.raw["workFormats"])

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

    def test_detail_fixture_extracts_real_description_sections(self) -> None:
        # Arrange
        source = HhRuSource()
        listing = _listing_by_id(
            source.parse_search_response(
                _fixture_response("hh_ru", "success"),
                SourceFetchRequest(
                    source_id="hh_ru",
                    query_variant="QA",
                    url="https://hh.ru/search/vacancy?text=QA&area=113&search_field=name",
                ),
            ).listings,
            "134371846",
        )
        expected = _expected("hh_ru", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("hh_ru", "detail"), listing)

        # Assert
        self.assertIsNotNone(detailed.description)
        for text in expected["description_contains"]:
            self.assertIn(text, detailed.description or "")
        self.assertIn("\n• Полное ручное тестирование iOS, Android и Web-версии.", detailed.description or "")
        self.assertIn("\n1. краткую информацию о себе;", detailed.description or "")
        self.assertEqual(set(expected["additional_sections"]), set(detailed.additional_sections))
        self.assertIn("Опыт работы QA от 2 лет.", detailed.requirements or "")

    def test_detail_fixture_extracts_structured_skills_and_work_facts(self) -> None:
        # Arrange
        source = HhRuSource()
        listing = RawListing(
            source_listing_id="134519442",
            title="Middle QA Automation Engineer (.Net)",
            url="https://spb.hh.ru/vacancy/134519442",
            source="hh_ru",
        )
        expected = _expected("hh_ru", "detail_structured")

        # Act
        detailed = source.parse_detail_response(_fixture_response("hh_ru", "detail_structured"), listing)

        # Assert
        self.assertEqual(tuple(expected["skills"]), detailed.skills)
        for key, value in expected["raw"].items():
            self.assertEqual(value, detailed.raw.get(key), key)
        self.assertIn("1–3 года", detailed.raw_text or "")
        self.assertIn("Pytest", detailed.raw_text or "")

    def test_blocked_detail_fixture_classifies_account_captcha(self) -> None:
        # Arrange
        source = HhRuSource()
        listing = RawListing(
            source_listing_id="134362377",
            title="QA Engineer Java/Инженер-тестировщик Java (ученик)",
            url="https://hh.ru/vacancy/134362377",
            source="hh_ru",
        )
        expected = _expected("hh_ru", "blocked")

        # Act / Assert
        with self.assertRaises(ClassifiedSourceError) as caught:
            source.parse_detail_response(_fixture_response("hh_ru", "blocked"), listing)
        self.assertEqual(SourceOutcome.BLOCKED, caught.exception.outcome)
        for text in expected["error_contains"]:
            self.assertIn(text, caught.exception.evidence.error or str(caught.exception))

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

    def test_request_mapping_fetches_general_vacancy_page_for_all_queries(self) -> None:
        # Arrange
        source = VKCareerSource()

        # Act
        qa_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]
        developer_request = source.build_search_requests(SearchRequest(query_variants=("backend developer",)))[0]
        remote_request = source.build_search_requests(
            SearchRequest(
                query_variants=("backend developer",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("RU",),
            )
        )[0]

        # Assert
        self.assertEqual("https://team.vk.company/career/api/v2/vacancies/?limit=25", qa_request.url)
        self.assertEqual("https://team.vk.company/career/api/v2/vacancies/?limit=25", developer_request.url)
        self.assertEqual(
            "https://team.vk.company/career/api/v2/vacancies/?limit=25&remote=true",
            remote_request.url,
        )

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
                url="https://team.vk.company/career/api/v2/vacancies/?limit=25",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_pagination_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = VKCareerSource()
        expected = _expected("career_vk", "pagination")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("career_vk", "pagination"),
            SourceFetchRequest(
                source_id="career:vk",
                query_variant="QA",
                url="https://team.vk.company/career/api/v2/vacancies/?limit=25&offset=25",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_pagination_fixture_preserves_structured_work_format(self) -> None:
        # Arrange
        source = VKCareerSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("career_vk", "pagination_offset_50"),
            SourceFetchRequest(
                source_id="career:vk",
                query_variant="QA",
                url="https://team.vk.company/career/api/v2/vacancies/?limit=25&offset=50",
            ),
        )

        # Assert
        combined = _listing_by_id(parsed.listings, "45736")
        flexible = _listing_by_id(parsed.listings, "45681")
        self.assertEqual("Комбинированный", combined.raw["work_format"])
        self.assertEqual("гибкий", flexible.raw["work_format"])

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = VKCareerSource()
        listing = _detail_listing_from_input("career_vk")
        expected = _expected("career_vk", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("career_vk", "detail"), listing)

        # Assert
        _assert_detail_description_matches_expected(self, detailed=detailed, expected=expected)


class IBSCareerSourceTest(unittest.TestCase):
    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        # Arrange / Act
        source = SupportedSource(
            scraper=IBSCareerSource(),
            fixture_suite=source_fixture_suite("career:ibs"),
        )

        # Assert
        self.assertEqual("career:ibs", source.scraper.descriptor.source_id)

    def test_request_mapping_discovers_explicit_remote_filter_from_real_html(self) -> None:
        # Arrange
        source = IBSCareerSource()

        # Act
        qa_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]
        remote_request = source.build_search_requests(
            SearchRequest(
                query_variants=("QA",),
                remote_mode=RemoteMode.COMPATIBLE_REMOTE,
                work_from_geographies=("RU",),
            )
        )[0]
        parsed_remote = source.parse_search_response(_fixture_response("career_ibs", "success"), remote_request)

        # Assert
        self.assertEqual("https://ibs.ru/career/vacancies/", qa_request.url)
        self.assertEqual(
            "https://ibs.ru/career/vacancies/#job-harness-remote-in-country",
            remote_request.url,
        )
        self.assertEqual(
            "https://ibs.ru/career/vacancies/filter/format-is-online/apply/",
            parsed_remote.next_request.url if parsed_remote.next_request else None,
        )
        self.assertEqual((), parsed_remote.listings)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = IBSCareerSource()
        expected = _expected("career_ibs", "success")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("career_ibs", "success"),
            SourceFetchRequest(
                source_id="career:ibs",
                query_variant="QA",
                url="https://ibs.ru/career/vacancies/",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_success_fixture_preserves_work_format_tags_under_common_key(self) -> None:
        # Arrange
        source = IBSCareerSource()

        # Act
        success = source.parse_search_response(
            _fixture_response("career_ibs", "success"),
            SourceFetchRequest(
                source_id="career:ibs",
                query_variant="QA",
                url="https://ibs.ru/career/vacancies/",
            ),
        )
        pagination = source.parse_search_response(
            _fixture_response("career_ibs", "pagination"),
            SourceFetchRequest(
                source_id="career:ibs",
                query_variant="QA",
                url="https://ibs.ru/career/vacancies/?PAGEN_1=2",
            ),
        )

        # Assert
        office_listing = _listing_by_id(success.listings, "84616")
        remote_listing = _listing_by_id(pagination.listings, "83747")
        self.assertEqual("В офисе", office_listing.raw["work_format"])
        self.assertEqual("Удаленно", remote_listing.raw["work_format"])

    def test_pagination_fixture_matches_manual_golden_samples(self) -> None:
        # Arrange
        source = IBSCareerSource()
        expected = _expected("career_ibs", "pagination")

        # Act
        parsed = source.parse_search_response(
            _fixture_response("career_ibs", "pagination"),
            SourceFetchRequest(
                source_id="career:ibs",
                query_variant="QA",
                url="https://ibs.ru/career/vacancies/?PAGEN_1=2",
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        self.assertEqual(expected["next_url"], parsed.next_request.url if parsed.next_request else None)
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = IBSCareerSource()
        listing = _detail_listing_from_input("career_ibs")
        expected = _expected("career_ibs", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("career_ibs", "detail"), listing)

        # Assert
        _assert_detail_description_matches_expected(self, detailed=detailed, expected=expected)
        self.assertEqual(set(expected["additional_sections"]), set(detailed.additional_sections))
        for text in expected["requirements_contains"]:
            self.assertIn(text, detailed.requirements or "")


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

    def test_success_fixture_preserves_remote_type_as_work_format(self) -> None:
        # Arrange
        source = TalantoSource()

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
        hybrid_listing = _listing_by_id(parsed.listings, "f8f67364-d103-4564-8549-c6f8baca96ff")
        remote_listing = _listing_by_id(parsed.listings, "6d924d5b-af3e-4259-8ec3-69d0d53904fd")
        self.assertEqual("hybrid", hybrid_listing.raw["work_format"])
        self.assertEqual("remote", remote_listing.raw["work_format"])

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

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = TalantoSource()
        listing = RawListing(
            source_listing_id="41519f55-efc3-4bec-aa54-3adf5e235fef",
            title="Стажер QA Manual [Archops]",
            url="https://talanto.work/jobs/41519f55-efc3-4bec-aa54-3adf5e235fef",
            source="talanto",
        )
        expected = _expected("talanto", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("talanto", "detail"), listing)

        # Assert
        self.assertIsNotNone(detailed.description)
        for text in expected["description_contains"]:
            self.assertIn(text, detailed.description or "")


class GeekJobSourceTest(unittest.TestCase):
    VACANCIES_URL = "https://geekjob.ru/vacancies"
    NO_RESULTS_QUERY = "zzzzzzzzzzzzzzzz"

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
        self.assertEqual(self.VACANCIES_URL, fetch_request.url)

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
                url=self.VACANCIES_URL,
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
                query_variant=self.NO_RESULTS_QUERY,
                url=self.VACANCIES_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class TalentoSourceTest(unittest.TestCase):
    QA_URL = "https://talento.works/?q=QA"

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
        self.assertEqual(self.QA_URL, fetch_request.url)

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
                url=self.QA_URL,
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

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = TalentoSource()
        listing = _detail_listing_from_input("talento")
        expected = _expected("talento", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("talento", "detail"), listing)

        # Assert
        _assert_detail_description_matches_expected(self, detailed=detailed, expected=expected)


class FinderWorkSourceTest(unittest.TestCase):
    QA_URL = "https://api.finder.work/api/v1/vacancies?search=QA"

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
        self.assertEqual(self.QA_URL, fetch_request.url)

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
                url=self.QA_URL,
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

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = FinderWorkSource()
        listing = _detail_listing_from_input("finder_work")
        expected = _expected("finder_work", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("finder_work", "detail"), listing)

        # Assert
        _assert_detail_description_matches_expected(self, detailed=detailed, expected=expected)


class GetmatchSourceTest(unittest.TestCase):
    SPECIALIZATIONS_URL = "https://getmatch.ru/api/specializations"
    QA_AUTO_OFFERS_URL = "https://getmatch.ru/api/offers?sa=any&p=1&offset=0&limit=100&pa=all&sp=qa_auto"

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
        self.assertEqual(self.SPECIALIZATIONS_URL, fetch_request.url)

    def test_specializations_fixture_chains_qa_offer_requests(self) -> None:
        # Arrange
        source = GetmatchSource()
        specializations_path = _FIXTURES / "getmatch" / "success" / "specializations.json"

        # Act
        parsed = source.parse_search_response(
            SourceResponseArtifact(
                source_id="getmatch",
                url=self.SPECIALIZATIONS_URL,
                media_type="application/json",
                body=specializations_path.read_text(encoding="utf-8"),
            ),
            SourceFetchRequest(
                source_id="getmatch",
                query_variant="QA",
                url=self.SPECIALIZATIONS_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertIsNotNone(parsed.next_request)
        assert parsed.next_request is not None
        self.assertEqual(self.QA_AUTO_OFFERS_URL, parsed.next_request.url)
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
                url=self.QA_AUTO_OFFERS_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)
        listing_with_sections = next(
            listing
            for listing in parsed.listings
            if "Что делать" in listing.additional_sections
        )
        self.assertIn("О компании", listing_with_sections.additional_sections)

    def test_success_fixture_extracts_work_format_from_location_metadata(self) -> None:
        # Arrange
        source = GetmatchSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("getmatch", "success"),
            SourceFetchRequest(
                source_id="getmatch",
                query_variant="QA",
                url=self.QA_AUTO_OFFERS_URL,
            ),
        )

        # Assert
        mixed_format = _listing_by_id(parsed.listings, "34397")
        self.assertEqual("remote, hybrid", mixed_format.raw["work_format"])
        hybrid_only = _listing_by_id(parsed.listings, "34245")
        self.assertEqual("hybrid", hybrid_only.raw["work_format"])
        self.assertIsNone(hybrid_only.remote_in_country)
        self.assertIsNone(hybrid_only.remote_global)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        # Arrange
        source = GetmatchSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("getmatch", "no_results"),
            SourceFetchRequest(
                source_id="getmatch",
                query_variant="zzzzzzzzzzzzzzzz",
                url=self.QA_AUTO_OFFERS_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class ItJobsUzSourceTest(unittest.TestCase):
    QA_URL = "https://www.it-jobs.uz/api/jobs?search=QA&limit=100&page=1&category=qa"

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
        self.assertEqual(self.QA_URL, fetch_request.url)

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
                url=self.QA_URL,
                media_type="application/json",
                body=json.dumps(payload),
            ),
            SourceFetchRequest(
                source_id="it_jobs_uz",
                query_variant="QA",
                url=self.QA_URL,
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
                url=self.QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)
        listing_with_sections = next(
            listing
            for listing in parsed.listings
            if listing.additional_sections
        )
        self.assertIn("responsibilities", listing_with_sections.additional_sections)
        self.assertIn("benefits", listing_with_sections.additional_sections)

    def test_success_fixture_preserves_work_type_as_work_format(self) -> None:
        # Arrange
        source = ItJobsUzSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("it_jobs_uz", "success"),
            SourceFetchRequest(
                source_id="it_jobs_uz",
                query_variant="QA",
                url=self.QA_URL,
            ),
        )

        # Assert
        hybrid_listing = _listing_by_id(parsed.listings, "cmmqnouqw000fdm3r19ht6u69")
        remote_listing = _listing_by_id(parsed.listings, "cmq9caz82000ldce2h3nxr6db")
        self.assertEqual("hybrid", hybrid_listing.raw["work_format"])
        self.assertEqual("remote", remote_listing.raw["work_format"])

    def test_success_fixture_merges_split_description_fields(self) -> None:
        # Arrange
        source = ItJobsUzSource()

        # Act
        parsed = source.parse_search_response(
            _fixture_response("it_jobs_uz", "success"),
            SourceFetchRequest(
                source_id="it_jobs_uz",
                query_variant="QA",
                url=self.QA_URL,
            ),
        )
        uzum_listing = _listing_by_id(parsed.listings, "cmmq8ycrn0007x3drm0yzpiuh")

        # Assert
        self.assertIsNotNone(uzum_listing.description)
        self.assertIn("Тестировать Backend", uzum_listing.description or "")
        self.assertIn("Опыт автоматизации тестирования на Java", uzum_listing.description or "")

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
    QA_URL = "https://api.hirify.me/api/vacancies?search=QA&page=1"

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
        self.assertEqual(self.QA_URL, fetch_request.url)

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
                url=self.QA_URL,
                media_type="application/json",
                body=json.dumps(payload),
            ),
            SourceFetchRequest(
                source_id="hirify",
                query_variant="QA",
                url=self.QA_URL,
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
                url=self.QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)
        hybrid_listing = _listing_by_id(parsed.listings, "670332")
        self.assertIn("hybrid", hybrid_listing.raw["work_format"])

    def test_search_card_metadata_extracts_country_grade_work_format_and_skills(self) -> None:
        # Arrange
        source = HirifySource()
        payload = {
            "data": [
                {
                    "id": 673690,
                    "slug": "673690-qa-analyst-gamedev",
                    "title": "QA Analyst (Gamedev)",
                    "company_title": "%hirify_global%",
                    "work_format": ["hybrid"],
                    "remote_type": None,
                    "remote_restrictions": [],
                    "excluded_locations": [],
                    "work_type": "fulltime",
                    "grades": [{"id": 3, "name": "middle"}],
                    "regions": [{"id": 8, "code": "argentina", "name": None, "name_en": None}],
                    "tags": [
                        {"id": 25, "name": "qa"},
                        {"id": 149, "name": "gamedev"},
                        {"id": 234, "name": "jira"},
                    ],
                    "specializations": [{"id": 9, "code": "qa_testing", "name_en": "QA Testing"}],
                    "updated_at": "2026-06-23T19:08:28.000000Z",
                }
            ],
            "total": 1,
            "current_page": 1,
            "last_page": 1,
        }

        # Act
        parsed = source.parse_search_response(
            SourceResponseArtifact(
                source_id="hirify",
                url=self.QA_URL,
                media_type="application/json",
                body=json.dumps(payload),
            ),
            SourceFetchRequest(
                source_id="hirify",
                query_variant="QA",
                url=self.QA_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(1, len(parsed.listings))
        listing = parsed.listings[0]
        self.assertEqual("argentina", listing.country)
        self.assertEqual("middle", listing.native_grade)
        self.assertFalse(listing.remote_in_country)
        self.assertFalse(listing.remote_global)
        self.assertEqual(("qa", "gamedev", "jira"), listing.skills)
        self.assertEqual("hybrid", listing.raw["work_format"])

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

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = HirifySource()
        listing = RawListing(
            source_listing_id="548415",
            title="Junior QA Automation Engineer (Kotlin)",
            url="https://hirify.me/jobs/548415-junior-qa-automation-engineer-kotlin",
            source="hirify",
        )
        expected = _expected("hirify", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("hirify", "detail"), listing)

        # Assert
        self.assertIsNotNone(detailed.description)
        for text in expected["description_contains"]:
            self.assertIn(text, detailed.description or "")


class JobTurboSourceTest(unittest.TestCase):
    REMOTE_LISTINGS_URL = "https://jobturbo.ru/vakansii/remote"

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
        self.assertEqual(self.REMOTE_LISTINGS_URL, fetch_request.url)

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
                url=self.REMOTE_LISTINGS_URL,
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
                url=self.REMOTE_LISTINGS_URL,
            ),
        )

        # Assert
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)


class HireHiSourceTest(unittest.TestCase):
    QA_URL = "https://hirehi.ru/jobs_new?query=QA"

    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        source = SupportedSource(
            scraper=HireHiSource(),
            fixture_suite=source_fixture_suite("hirehi"),
        )
        self.assertEqual("hirehi", source.scraper.descriptor.source_id)

    def test_request_mapping_uses_native_query_parameter(self) -> None:
        source = HireHiSource()
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]
        self.assertEqual("hirehi", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(self.QA_URL, fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        source = HireHiSource()
        expected = _expected("hirehi", "success")
        parsed = source.parse_search_response(
            _fixture_response("hirehi", "success"),
            SourceFetchRequest(source_id="hirehi", query_variant="QA", url=self.QA_URL),
        )
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        source = HireHiSource()
        parsed = source.parse_search_response(
            _fixture_response("hirehi", "no_results"),
            SourceFetchRequest(
                source_id="hirehi",
                query_variant="zzzzzzzzzzzzzzzz",
                url="https://hirehi.ru/jobs_new?query=zzzzzzzzzzzzzzzz",
            ),
        )
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = HireHiSource()
        listing = _detail_listing_from_input("hirehi")
        expected = _expected("hirehi", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("hirehi", "detail"), listing)

        # Assert
        _assert_detail_description_matches_expected(self, detailed=detailed, expected=expected)


class StaffAmSourceTest(unittest.TestCase):
    QA_URL = "https://staff.am/en/jobs/quality-assurance"

    def test_supported_source_contract_accepts_real_fixture_suite(self) -> None:
        source = SupportedSource(
            scraper=StaffAmSource(),
            fixture_suite=source_fixture_suite("staff_am"),
        )
        self.assertEqual("staff_am", source.scraper.descriptor.source_id)

    def test_request_mapping_routes_qa_to_quality_assurance(self) -> None:
        source = StaffAmSource()
        fetch_request = source.build_search_requests(SearchRequest(query_variants=("QA",)))[0]
        self.assertEqual("staff_am", fetch_request.source_id)
        self.assertEqual("QA", fetch_request.query_variant)
        self.assertEqual(self.QA_URL, fetch_request.url)

    def test_success_fixture_matches_manual_golden_samples(self) -> None:
        source = StaffAmSource()
        expected = _expected("staff_am", "success")
        parsed = source.parse_search_response(
            _fixture_response("staff_am", "success"),
            SourceFetchRequest(source_id="staff_am", query_variant="QA", url=self.QA_URL),
        )
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)
        self.assertEqual(expected["expected_count"], len(parsed.listings))
        for sample in expected["sample_listings"]:
            _assert_listing_matches(self, _listing_by_id(parsed.listings, sample["source_listing_id"]), sample)

    def test_no_results_fixture_is_explicit_no_results(self) -> None:
        source = StaffAmSource()
        parsed = source.parse_search_response(
            _fixture_response("staff_am", "no_results"),
            SourceFetchRequest(
                source_id="staff_am",
                query_variant="zzzzzzzzzzzzzzzz",
                url=self.QA_URL,
            ),
        )
        self.assertEqual(SourceOutcome.NO_RESULTS, parsed.outcome)
        self.assertEqual((), parsed.listings)
        self.assertTrue(parsed.evidence.no_results)

    def test_detail_fixture_extracts_full_description_text(self) -> None:
        # Arrange
        source = StaffAmSource()
        listing = _detail_listing_from_input("staff_am")
        expected = _expected("staff_am", "detail")

        # Act
        detailed = source.parse_detail_response(_fixture_response("staff_am", "detail"), listing)

        # Assert
        _assert_detail_description_matches_expected(self, detailed=detailed, expected=expected)


class JetBrainsCareerSourceTest(unittest.TestCase):
    GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/jetbrains/jobs?content=true"

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
        self.assertEqual(self.GREENHOUSE_BOARD_URL, fetch_request.url)

    def test_bare_remote_location_does_not_mean_global_remote(self) -> None:
        # Arrange
        source = JetBrainsCareerSource()
        response = SourceResponseArtifact(
            source_id="career:jetbrains",
            url=self.GREENHOUSE_BOARD_URL,
            media_type="application/json",
            body=json.dumps(
                {
                    "jobs": [
                        {
                            "id": 4696941101,
                            "title": "Campus Ambassador (Universities in Europe)",
                            "absolute_url": "https://job-boards.eu.greenhouse.io/jetbrains/jobs/4696941101",
                            "company_name": "JetBrains",
                            "first_published": "2025-10-30T14:30:59-04:00",
                            "location": {"name": "Remote"},
                            "content": "<p>Represent JetBrains at the university campus.</p>",
                            "departments": [{"name": "University Relations"}],
                            "offices": [{"name": "Amsterdam"}, {"name": "London"}],
                            "metadata": [{"name": "Team", "value": "Education"}],
                            "internal_job_id": 4391016101,
                            "requisition_id": None,
                            "updated_at": "2026-06-15T10:53:26-04:00",
                        }
                    ],
                    "meta": {"total": 1},
                }
            ),
        )

        # Act
        parsed = source.parse_search_response(
            response,
            SourceFetchRequest(
                source_id="career:jetbrains",
                query_variant="QA",
                url=self.GREENHOUSE_BOARD_URL,
            ),
        )

        # Assert
        listing = parsed.listings[0]
        self.assertEqual("Remote", listing.location_text)
        self.assertIsNone(listing.remote_in_country)
        self.assertIsNone(listing.remote_global)
        self.assertEqual(("Amsterdam", "London"), listing.raw["offices"])

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
                url=self.GREENHOUSE_BOARD_URL,
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
        sectioned_listing = parsed.listings[0]
        self.assertIn("In this role, you will", sectioned_listing.additional_sections)
        self.assertIn("We offer", sectioned_listing.additional_sections)
        hybrid_listing = _listing_by_id(parsed.listings, "4884023101")
        self.assertNotIn("work_format", hybrid_listing.raw)
        self.assertEqual(("#LI-HYBRID",), hybrid_listing.raw["linkedin_workplace_tags"])
        self.assertNotIn("#LI-HYBRID", hybrid_listing.description or "")


def _e2e_success_fixture_mapping(catalog: SourceCatalog, request: SearchRequest) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for source_id in catalog.source_ids:
        source_mapping = _e2e_success_source_fixture_mapping(catalog.get(source_id), request)
        for url, path in source_mapping.items():
            existing = mapping.get(url)
            if existing is not None and existing != path:
                raise AssertionError(f"fixture URL maps to multiple payloads: {url}")
            mapping[url] = path
    return mapping


def _e2e_success_source_fixture_mapping(
    scraper: SourceScraper,
    request: SearchRequest,
) -> dict[str, Path]:
    source_id = scraper.descriptor.source_id
    success_case = _required_fixture_case(source_id, ParserFixtureKind.SUCCESS_NON_EMPTY)
    fixture_cases = (success_case, *_fixture_cases(source_id, ParserFixtureKind.PAGINATION))
    mapping = {
        _fixture_captured_url(case): _fixture_response_path_from_case(case)
        for case in fixture_cases
    }

    fetch_requests = scraper.build_search_requests(request)
    for fetch_request in fetch_requests:
        _map_initial_request_if_needed(mapping, fetch_request, success_case)
    _discover_next_request_fixture_mappings(
        scraper=scraper,
        mapping=mapping,
        initial_requests=fetch_requests,
    )
    return mapping


def _map_initial_request_if_needed(
    mapping: dict[str, Path],
    request: SourceFetchRequest,
    success_case: ParserFixtureCase,
) -> None:
    if request.url in mapping:
        return

    extra_paths = _extra_fixture_payload_paths(_fixture_response_path_from_case(success_case).parent)
    if len(extra_paths) != 1:
        raise AssertionError(
            f"no captured fixture payload for initial request URL: {request.source_id} {request.url}"
        )
    mapping[request.url] = extra_paths[0]


def _discover_next_request_fixture_mappings(
    *,
    scraper: SourceScraper,
    mapping: dict[str, Path],
    initial_requests: tuple[SourceFetchRequest, ...],
) -> None:
    queued = list(initial_requests)
    visited: set[str] = set()
    collected_listings = 0
    while queued:
        request = queued.pop(0)
        if request.url in visited:
            continue
        visited.add(request.url)

        response_path = mapping[request.url]
        parsed = scraper.parse_search_response(
            _fixture_response_artifact(
                source_id=scraper.descriptor.source_id,
                request=request,
                path=response_path,
            ),
            request,
        )
        remaining = scraper.descriptor.source_limit - collected_listings
        if remaining <= 0:
            continue
        page_listings = parsed.listings[:remaining]
        _map_detail_requests_if_needed(
            scraper=scraper,
            mapping=mapping,
            listings=page_listings,
        )
        collected_listings += len(page_listings)
        if collected_listings >= scraper.descriptor.source_limit:
            continue

        next_request = parsed.next_request
        if next_request is None:
            continue

        if next_request.url not in mapping:
            mapping[next_request.url] = _replay_payload_for_next_request(
                scraper=scraper,
                current_request=request,
                current_response_path=response_path,
                next_request=next_request,
            )
        queued.append(next_request)


def _replay_payload_for_next_request(
    *,
    scraper: SourceScraper,
    current_request: SourceFetchRequest,
    current_response_path: Path,
    next_request: SourceFetchRequest,
) -> Path:
    if scraper.required_fixture_kinds.pagination:
        raise AssertionError(
            f"{scraper.descriptor.source_id} emitted uncaptured pagination URL: {next_request.url}"
        )
    if not _same_url_without_query(current_request.url, next_request.url):
        raise AssertionError(
            f"{scraper.descriptor.source_id} emitted uncaptured next_request URL: {next_request.url}"
        )
    return current_response_path


def _map_detail_requests_if_needed(
    *,
    scraper: SourceScraper,
    mapping: dict[str, Path],
    listings: tuple[Any, ...],
) -> None:
    if not isinstance(scraper, DetailEnrichmentScraper):
        return

    detail_case = _required_fixture_case(scraper.descriptor.source_id, ParserFixtureKind.DETAIL)
    detail_path = _fixture_response_path_from_case(detail_case)
    for listing in listings:
        detail_request = scraper.build_detail_request(listing)
        mapping.setdefault(detail_request.url, detail_path)


class ContractFirstRuntimeE2ETest(unittest.IsolatedAsyncioTestCase):
    async def test_new_runtime_runs_real_parser_fixtures(self) -> None:
        # Arrange
        catalog = build_supported_source_catalog()
        request = SearchRequest(query_variants=(_E2E_SUCCESS_QUERY,))
        fetcher = FixtureFetcher(_e2e_success_fixture_mapping(catalog, request))
        with tempfile.TemporaryDirectory() as tmp:
            with SqliteRunStore(Path(tmp) / "run.sqlite", run_id="r-test") as writer:
                writer.reserve_append_attempt({"query_variants": [_E2E_SUCCESS_QUERY]})
                orchestrator = SearchOrchestrator(
                    catalog=catalog,
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(request, run_id="r-test")
                raw_records = writer.read_raw_records()

            # Assert
            outcomes = {attempt.source: attempt for attempt in result.attempts}
            self.assertEqual(set(catalog.source_ids), set(outcomes))
            for source_id, attempt in outcomes.items():
                self.assertIn(attempt.outcome, {SourceOutcome.SUCCESS, SourceOutcome.NO_RESULTS}, source_id)
                self.assertGreater(attempt.counts.pages_visited, 0, source_id)
                if attempt.outcome == SourceOutcome.SUCCESS:
                    self.assertGreater(attempt.counts.raw_listings_written, 0, source_id)
                else:
                    self.assertEqual(0, attempt.counts.raw_listings_written, source_id)

            self.assertEqual(result.raw_records_written, len(raw_records))
            self.assertEqual(
                {
                    source_id
                    for source_id, attempt in outcomes.items()
                    if attempt.outcome is SourceOutcome.SUCCESS
                },
                {record["source"] for record in raw_records},
            )
            self.assertIn(
                "Тестировщик (QA) мобильного приложения Windi Messenger",
                {record["listing"]["title"] for record in raw_records},
            )

    async def test_new_runtime_records_explicit_no_results_without_raw_records(self) -> None:
        # Arrange
        cases = _runtime_no_results_fixture_cases()

        # Assert
        self.assertGreater(len(cases), 0)
        for case in cases:
            with self.subTest(source_id=case.source_id):
                fetcher = FixtureFetcher({case.url: case.response_path})
                with tempfile.TemporaryDirectory() as tmp:
                    with SqliteRunStore(Path(tmp) / "run.sqlite", run_id="r-test") as writer:
                        writer.reserve_append_attempt({"query_variants": [case.query_variant]})
                        orchestrator = SearchOrchestrator(
                            catalog=build_supported_source_catalog((case.source_id,)),
                            fetcher=fetcher,
                            writer=writer,
                            config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                        )

                        # Act
                        result = await orchestrator.run(
                            SearchRequest(query_variants=(case.query_variant,)),
                            run_id="r-test",
                        )
                        raw_records = writer.read_raw_records()

                    # Assert
                    self.assertEqual(0, result.raw_records_written)
                    self.assertEqual(
                        {case.source_id: SourceOutcome.NO_RESULTS},
                        {attempt.source: attempt.outcome for attempt in result.attempts},
                    )
                    self.assertEqual((), raw_records)


if __name__ == "__main__":
    unittest.main()
