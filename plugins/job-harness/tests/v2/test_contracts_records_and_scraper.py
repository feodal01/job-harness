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
        requested=frozenset({SearchCriterion.QUERY, SearchCriterion.REMOTE_GLOBAL}),
        native_applied=frozenset({SearchCriterion.QUERY}),
        unsupported=frozenset({SearchCriterion.REMOTE_GLOBAL}),
        postprocess=frozenset({SearchCriterion.REMOTE_GLOBAL}),
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
