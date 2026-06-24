from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from tests.v2._support.contract_runtime import (
    FakeFetcher,
    FakeScraper,
    descriptor,
    listing,
    supported,
)

from job_harness.v2.contracts import (
    DetailEnrichmentScraper,
    RawListing,
    SearchRequest,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
)
from job_harness.v2.persistence import SqliteRunStore
from job_harness.v2.runtime import (
    ClassifiedSourceError,
    OrchestratorConfig,
    RetryPolicy,
    SearchOrchestrator,
    SourceCatalog,
)


def _store(run_dir: Path, *, query_variants: tuple[str, ...] = ("QA",)) -> SqliteRunStore:
    store = SqliteRunStore(run_dir / "run.sqlite", run_id="r-test")
    store.reserve_append_attempt({"query_variants": list(query_variants)})
    return store


def _read_raw_records(run_dir: Path) -> list[dict[str, Any]]:
    with SqliteRunStore(run_dir / "run.sqlite", run_id="r-test") as store:
        return list(store.read_raw_records())


def _read_source_attempts(run_dir: Path) -> list[dict[str, Any]]:
    with SqliteRunStore(run_dir / "run.sqlite", run_id="r-test") as store:
        return list(store.read_source_attempts())


class FakeDetailScraper(FakeScraper, DetailEnrichmentScraper):
    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=f"https://example.test/{self.descriptor.source_id}/detail/{listing.source_listing_id}",
        )

    def parse_detail_response(
        self,
        _response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        return replace(
            listing,
            description=f"Full detail description for {listing.title}",
            requirements="Full detail requirements",
            raw_text=f"{listing.raw_text or listing.title} Full detail description Full detail requirements",
        )


class SearchOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def test_runs_success_and_explicit_no_results_sources(self) -> None:
        # Arrange
        hh = FakeScraper(
            source_descriptor=descriptor("hh_ru"),
            raw_listings=(listing("hh_ru"),),
        )
        empty = FakeScraper(
            source_descriptor=descriptor("empty_jobs"),
            outcome=SourceOutcome.NO_RESULTS,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(hh), supported(empty))),
                    fetcher=FakeFetcher(),
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            outcomes = {attempt.source: attempt.outcome for attempt in result.attempts}
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["hh_ru"])
            self.assertEqual(SourceOutcome.NO_RESULTS, outcomes["empty_jobs"])
            self.assertEqual(1, result.raw_records_written)
            self.assertEqual(1, len(_read_raw_records(Path(tmp))))

    async def test_failed_source_does_not_block_successful_source(self) -> None:
        # Arrange
        good = FakeScraper(
            source_descriptor=descriptor("good_jobs"),
            raw_listings=(listing("good_jobs"),),
        )
        bad = FakeScraper(
            source_descriptor=descriptor("bad_jobs"),
            raw_listings=(listing("bad_jobs"),),
        )
        fetcher = FakeFetcher(
            failures={
                ("bad_jobs", "QA"): [
                    ClassifiedSourceError(SourceOutcome.NETWORK_ERROR, "connection reset")
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(good), supported(bad))),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            outcomes = {attempt.source: attempt.outcome for attempt in result.attempts}
            self.assertEqual(SourceOutcome.SUCCESS, outcomes["good_jobs"])
            self.assertEqual(SourceOutcome.NETWORK_ERROR, outcomes["bad_jobs"])
            self.assertEqual(1, result.raw_records_written)

    async def test_retries_transient_failure_when_no_listings_were_written(self) -> None:
        # Arrange
        flaky = FakeScraper(
            source_descriptor=descriptor("flaky_jobs"),
            raw_listings=(listing("flaky_jobs"),),
        )
        fetcher = FakeFetcher(
            failures={
                ("flaky_jobs", "QA"): [
                    ClassifiedSourceError(SourceOutcome.NETWORK_ERROR, "connection reset")
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(flaky),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=2)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.SUCCESS, result.attempts[-1].outcome)
            self.assertEqual(2, len(fetcher.calls))
            attempt_records = _read_source_attempts(Path(tmp))
            self.assertEqual(["network_error", "success"], [record["outcome"] for record in attempt_records])
            self.assertEqual("retry", attempt_records[0]["retry"]["next_action"])

    async def test_source_timeout_is_classified_per_source(self) -> None:
        # Arrange
        slow = FakeScraper(
            source_descriptor=descriptor("slow_jobs"),
            raw_listings=(listing("slow_jobs"),),
        )
        fetcher = FakeFetcher(delays={("slow_jobs", "QA"): 0.05})
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(slow),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(
                        source_attempt_timeout_seconds=0.01,
                        retry_policy=RetryPolicy(max_attempts=1),
                    ),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.SOURCE_TIMEOUT, result.attempts[0].outcome)
            self.assertEqual(0, result.raw_records_written)

    async def test_listing_source_mismatch_is_invalid_source_output(self) -> None:
        # Arrange
        noisy = FakeScraper(
            source_descriptor=descriptor("noisy_jobs"),
            raw_listings=(listing("other_jobs", "1"),),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(noisy),)),
                    fetcher=FakeFetcher(),
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.INVALID_SOURCE_OUTPUT, result.attempts[0].outcome)
            self.assertEqual(0, result.raw_records_written)
            self.assertEqual([], _read_raw_records(Path(tmp)))

    async def test_append_mode_preserves_existing_raw_records(self) -> None:
        # Arrange
        scraper = FakeScraper(
            source_descriptor=descriptor("hh_ru"),
            raw_listings=(listing("hh_ru"),),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=FakeFetcher(),
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")
                writer.reserve_append_attempt({"query_variants": ["quality assurance"]})
                await orchestrator.run(
                    SearchRequest(query_variants=("quality assurance",), append_to_run_id="r-test"),
                    append_sequence=1,
                )

            # Assert
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual(2, len(raw_records))
            self.assertEqual([0, 1], [record["append_sequence"] for record in raw_records])
            self.assertEqual(
                ["QA", "quality assurance"],
                [record["query_variant"] for record in raw_records],
            )

    async def test_run_timeout_records_unfinished_source_without_waiting_for_source_timeout(self) -> None:
        # Arrange
        slow = FakeScraper(
            source_descriptor=descriptor("slow_jobs"),
            raw_listings=(listing("slow_jobs"),),
        )
        fetcher = FakeFetcher(delays={("slow_jobs", "QA"): 0.05})
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(slow),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(
                        source_attempt_timeout_seconds=1.0,
                        run_timeout_seconds=0.01,
                        retry_policy=RetryPolicy(max_attempts=1),
                    ),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.RUN_TIMEOUT, result.attempts[0].outcome)
            self.assertEqual(0, result.raw_records_written)

    async def test_detail_enrichment_scraper_writes_full_description_before_raw_record(self) -> None:
        # Arrange
        base_listing = replace(
            listing("detail_jobs"),
            description=None,
            requirements=None,
            raw_text="QA Engineer 1",
        )
        scraper = FakeDetailScraper(
            source_descriptor=descriptor("detail_jobs"),
            raw_listings=(base_listing,),
        )
        fetcher = FakeFetcher()
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.SUCCESS, result.attempts[0].outcome)
            self.assertEqual(
                [
                    "https://example.test/detail_jobs/search?q=QA",
                    "https://example.test/detail_jobs/detail/1",
                ],
                [call.url for call in fetcher.calls],
            )
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual(1, len(raw_records))
            self.assertEqual("present", raw_records[0]["description_availability"])
            self.assertTrue(raw_records[0]["detail_fetched"])
            self.assertEqual("https://example.test/detail_jobs/jobs/1", raw_records[0]["listing"]["url"])
            self.assertEqual(
                "Full detail description for QA Engineer 1",
                raw_records[0]["listing"]["description"],
            )
            self.assertEqual("Full detail requirements", raw_records[0]["listing"]["requirements"])

    async def test_detail_enrichment_failure_preserves_search_listings_as_partial_success(self) -> None:
        # Arrange
        listings = (
            replace(listing("detail_jobs", "1"), description=None, raw_text="QA Engineer 1"),
            replace(listing("detail_jobs", "2"), description=None, raw_text="QA Engineer 2"),
        )
        scraper = FakeDetailScraper(
            source_descriptor=descriptor("detail_jobs"),
            raw_listings=listings,
        )
        fetcher = FakeFetcher(
            failures={
                ("detail_jobs", "QA Engineer 1"): [
                    ClassifiedSourceError(SourceOutcome.NETWORK_ERROR, "detail connection reset")
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.PARTIAL_SUCCESS, result.attempts[0].outcome)
            self.assertEqual("detail connection reset", result.attempts[0].evidence.error)
            self.assertEqual(2, result.raw_records_written)
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual(
                [
                    "https://example.test/detail_jobs/jobs/1",
                    "https://example.test/detail_jobs/jobs/2",
                ],
                [record["listing"]["url"] for record in raw_records],
            )
            self.assertEqual(
                [None, "Full detail description for QA Engineer 2"],
                [record["listing"]["description"] for record in raw_records],
            )

    async def test_detail_blocked_preserves_listing_and_marks_detail_blocked(self) -> None:
        # Arrange
        listings = (
            replace(listing("detail_jobs", "1"), description=None, raw_text="QA Engineer 1"),
        )
        scraper = FakeDetailScraper(
            source_descriptor=descriptor("detail_jobs"),
            raw_listings=listings,
        )
        fetcher = FakeFetcher(
            failures={
                ("detail_jobs", "QA Engineer 1"): [
                    ClassifiedSourceError(
                        SourceOutcome.BLOCKED,
                        "hh.ru account captcha on vacancy detail",
                    )
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.PARTIAL_SUCCESS, result.attempts[0].outcome)
            self.assertEqual("hh.ru account captcha on vacancy detail", result.attempts[0].evidence.error)
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual("detail_blocked", raw_records[0]["description_availability"])
            self.assertTrue(raw_records[0]["detail_fetched"])
            self.assertEqual(
                "hh.ru account captcha on vacancy detail",
                raw_records[0]["detail_parse_error"],
            )
            self.assertIsNone(raw_records[0]["listing"]["description"])

    async def test_detail_rate_limited_preserves_listing_and_status(self) -> None:
        # Arrange
        listings = (
            replace(listing("detail_jobs", "1"), description=None, raw_text="QA Engineer 1"),
        )
        scraper = FakeDetailScraper(
            source_descriptor=descriptor("detail_jobs"),
            raw_listings=listings,
        )
        fetcher = FakeFetcher(
            failures={
                ("detail_jobs", "QA Engineer 1"): [
                    ClassifiedSourceError(
                        SourceOutcome.RATE_LIMITED,
                        "HTTP Error 429: Too Many Requests",
                    )
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                result = await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            # Assert
            self.assertEqual(SourceOutcome.PARTIAL_SUCCESS, result.attempts[0].outcome)
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual("detail_rate_limited", raw_records[0]["description_availability"])
            self.assertEqual("HTTP Error 429: Too Many Requests", raw_records[0]["detail_parse_error"])


if __name__ == "__main__":
    unittest.main()
