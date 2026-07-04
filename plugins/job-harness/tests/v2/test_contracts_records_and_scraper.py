from __future__ import annotations

import unittest
from datetime import UTC, datetime

from job_harness.v2.contracts import (
    AttemptCounts,
    AttemptEvidence,
    CriteriaDiagnostics,
    CriterionCapability,
    CriterionDeclaration,
    RawListing,
    RawSearchRecord,
    RequiredParserFixtures,
    RetryInfo,
    RetryNextAction,
    SearchCriterion,
    SearchRequest,
    SourceAttemptRecord,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
    SourceSearchParseResult,
    SourceType,
    Transport,
)
from job_harness.v2.contracts.enums import ALL_SEARCH_CRITERIA


def _now() -> datetime:
    return datetime(2026, 6, 22, 10, 0, tzinfo=UTC)


def _listing() -> RawListing:
    return RawListing(
        source_listing_id="123",
        title="QA Engineer",
        url="https://example.test/jobs/123",
        source="hh_ru",
        company="Acme",
    )


def _criteria_diagnostics() -> CriteriaDiagnostics:
    return CriteriaDiagnostics(
        requested=frozenset({SearchCriterion.QUERY, SearchCriterion.WORK_FORMATS}),
        native_applied=frozenset({SearchCriterion.QUERY}),
        unsupported=frozenset({SearchCriterion.WORK_FORMATS}),
        postprocess=frozenset({SearchCriterion.WORK_FORMATS}),
    )


def _retry() -> RetryInfo:
    return RetryInfo(attempts=1, max_attempts=2, next_action=RetryNextAction.NONE)


def _descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        source_id="hh_ru",
        source_type=SourceType.AGGREGATOR,
        transport=Transport.HTTP,
        countries=("RU",),
        source_limit=100,
        criteria=tuple(
            CriterionDeclaration(criterion, CriterionCapability.UNSUPPORTED)
            for criterion in ALL_SEARCH_CRITERIA
        ),
    )


class RawRecordTest(unittest.TestCase):
    def test_raw_listing_requires_absolute_http_url(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "url"):
            RawListing(
                source_listing_id=None,
                title="QA",
                url="/jobs/1",
                source="hh_ru",
            )

    def test_raw_listing_rejects_global_remote_without_explicit_source_evidence(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "remote_global"):
            RawListing(
                source_listing_id="123",
                title="QA Engineer",
                url="https://example.test/jobs/123",
                source="career:jetbrains",
                location_text="Remote",
                remote_in_country=True,
                remote_global=True,
                raw={"locations": ({"city": None, "country": None, "remote": True},)},
            )

    def test_raw_listing_accepts_global_remote_with_explicit_source_evidence(self) -> None:
        # Arrange / Act
        listing = RawListing(
            source_listing_id="123",
            title="QA Engineer",
            url="https://example.test/jobs/123",
            source="hirify",
            remote_in_country=True,
            remote_global=True,
            raw={"remote_type": "global"},
        )

        # Assert
        self.assertTrue(listing.remote_global)

    def test_raw_search_record_requires_listing_source_to_match(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "listing.source"):
            RawSearchRecord(
                run_id="r-1",
                append_sequence=0,
                query_variant="QA",
                source="habr_career",
                source_type=SourceType.AGGREGATOR,
                collected_at=_now(),
                listing=_listing(),
            )


class SourceFetchRequestTest(unittest.TestCase):
    def test_defaults_query_variants_to_query_variant(self) -> None:
        # Arrange / Act
        request = SourceFetchRequest(
            source_id="hh_ru",
            query_variant="QA",
            url="https://example.test/search?q=QA",
        )

        # Assert
        self.assertEqual(("QA",), request.query_variants)

    def test_accepts_grouped_query_variants(self) -> None:
        # Arrange / Act
        request = SourceFetchRequest(
            source_id="geekjob",
            query_variant="Quality Assurance",
            query_variants=("Quality Assurance", "QA Engineer", "SDET"),
            url="https://example.test/search",
        )

        # Assert
        self.assertEqual(("Quality Assurance", "QA Engineer", "SDET"), request.query_variants)

    def test_rejects_empty_grouped_query_variant(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "query_variants"):
            SourceFetchRequest(
                source_id="geekjob",
                query_variant="QA",
                query_variants=("QA", " "),
                url="https://example.test/search",
            )


class SourceAttemptRecordTest(unittest.TestCase):
    def test_success_requires_written_listing(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "success"):
            SourceAttemptRecord(
                source="hh_ru",
                source_type=SourceType.AGGREGATOR,
                query_variant="QA",
                attempt=1,
                outcome=SourceOutcome.SUCCESS,
                started_at=_now(),
                finished_at=_now(),
                elapsed_ms=0,
                source_limit=100,
                limit_reached=False,
                counts=AttemptCounts(raw_listings_written=0),
                criteria=_criteria_diagnostics(),
                retry=_retry(),
            )

    def test_no_results_requires_explicit_evidence(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "evidence"):
            SourceAttemptRecord(
                source="hh_ru",
                source_type=SourceType.AGGREGATOR,
                query_variant="QA",
                attempt=1,
                outcome=SourceOutcome.NO_RESULTS,
                started_at=_now(),
                finished_at=_now(),
                elapsed_ms=0,
                source_limit=100,
                limit_reached=False,
                counts=AttemptCounts(raw_listings_written=0),
                criteria=_criteria_diagnostics(),
                retry=_retry(),
                evidence=AttemptEvidence(no_results=False),
            )

    def test_limit_reached_requires_success(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "limit_reached"):
            SourceAttemptRecord(
                source="hh_ru",
                source_type=SourceType.AGGREGATOR,
                query_variant="QA",
                attempt=1,
                outcome=SourceOutcome.PARTIAL_SUCCESS,
                started_at=_now(),
                finished_at=_now(),
                elapsed_ms=0,
                source_limit=100,
                limit_reached=True,
                counts=AttemptCounts(raw_listings_written=1),
                criteria=_criteria_diagnostics(),
                retry=_retry(),
            )

    def test_valid_success_record(self) -> None:
        # Arrange / Act
        record = SourceAttemptRecord(
            source="hh_ru",
            source_type=SourceType.AGGREGATOR,
            query_variant="QA",
            attempt=1,
            outcome=SourceOutcome.SUCCESS,
            started_at=_now(),
            finished_at=_now(),
            elapsed_ms=0,
            source_limit=100,
            limit_reached=False,
            counts=AttemptCounts(raw_listings_written=1),
            criteria=_criteria_diagnostics(),
            retry=_retry(),
        )

        # Assert
        self.assertEqual(SourceOutcome.SUCCESS, record.outcome)


class SourceSearchParseResultTest(unittest.TestCase):
    def test_success_can_continue_with_parallel_requests_without_current_listings(self) -> None:
        # Arrange / Act
        result = SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=(),
            parallel_requests=(
                SourceFetchRequest(
                    source_id="hh_ru",
                    query_variant="QA",
                    url="https://example.test/search?page=2",
                ),
            ),
        )

        # Assert
        self.assertEqual(1, len(result.parallel_requests))

    def test_parse_result_rejects_mixed_sequential_and_parallel_pagination(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "both next_request and parallel_requests"):
            SourceSearchParseResult(
                outcome=SourceOutcome.SUCCESS,
                listings=(_listing(),),
                next_request=SourceFetchRequest(
                    source_id="hh_ru",
                    query_variant="QA",
                    url="https://example.test/search?page=2",
                ),
                parallel_requests=(
                    SourceFetchRequest(
                        source_id="hh_ru",
                        query_variant="QA",
                        url="https://example.test/search?page=3",
                    ),
                ),
            )

    def test_no_results_rejects_parallel_requests(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "parallel_requests"):
            SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
                parallel_requests=(
                    SourceFetchRequest(
                        source_id="hh_ru",
                        query_variant="QA",
                        url="https://example.test/search?page=2",
                    ),
                ),
            )


class ScraperAbcTest(unittest.TestCase):
    def test_incomplete_scraper_cannot_be_instantiated(self) -> None:
        # Arrange
        incomplete_scraper = type("IncompleteScraper", (SourceScraper,), {})

        # Act / Assert
        with self.assertRaises(TypeError):
            incomplete_scraper()

    def test_concrete_scraper_implements_contract_boundary(self) -> None:
        # Arrange
        class ConcreteScraper(SourceScraper):
            @property
            def descriptor(self) -> SourceDescriptor:
                return _descriptor()

            @property
            def required_fixture_kinds(self) -> RequiredParserFixtures:
                return RequiredParserFixtures()

            def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
                return tuple(
                    SourceFetchRequest(
                        source_id=self.descriptor.source_id,
                        query_variant=query,
                        url=f"https://example.test/search?q={query}",
                    )
                    for query in request.query_variants
                )

            def parse_search_response(
                self,
                _response: SourceResponseArtifact,
                _request: SourceFetchRequest,
            ) -> SourceSearchParseResult:
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(_listing(),),
                )

        scraper = ConcreteScraper()
        request = SearchRequest(query_variants=("QA", "тестировщик"))
        response = SourceResponseArtifact(
            source_id="hh_ru",
            url="https://example.test/search?q=QA",
            media_type="text/html",
            body="<html></html>",
        )

        # Act
        fetch_requests = scraper.build_search_requests(request)
        parsed = scraper.parse_search_response(response, fetch_requests[0])

        # Assert
        self.assertEqual(2, len(fetch_requests))
        self.assertEqual(SourceOutcome.SUCCESS, parsed.outcome)


if __name__ == "__main__":
    unittest.main()
