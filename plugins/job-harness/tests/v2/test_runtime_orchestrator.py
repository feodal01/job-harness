from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
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
    RawSearchRecord,
    SearchRequest,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceType,
)
from job_harness.v2.persistence import SqliteRunStore
from job_harness.v2.ports import RunStore
from job_harness.v2.postprocessing import ResultTablePostProcessor
from job_harness.v2.runtime import (
    ClassifiedSourceError,
    DetailEnrichmentRunner,
    DetailServiceConfig,
    DetailWorkItem,
    OrchestratorConfig,
    RetryPolicy,
    RetryServiceConfig,
    SearchOrchestrator,
    SearchPipeline,
    SearchPipelineConfig,
    SearchServiceConfig,
    SourceCatalog,
)


def _store(run_dir: Path, *, query_variants: tuple[str, ...] = ("QA",)) -> SqliteRunStore:
    store = SqliteRunStore(run_dir / "run.sqlite", run_id="r-test")
    store.reserve_append_attempt({"query_variants": list(query_variants)})
    return store


def _store_factory(database_path: Path, *, run_id: str) -> RunStore:
    return SqliteRunStore(database_path, run_id=run_id)


def _read_raw_records(run_dir: Path) -> list[dict[str, Any]]:
    with SqliteRunStore(run_dir / "run.sqlite", run_id="r-test") as store:
        return list(store.read_raw_records())


def _read_source_attempts(run_dir: Path) -> list[dict[str, Any]]:
    with SqliteRunStore(run_dir / "run.sqlite", run_id="r-test") as store:
        return list(store.read_source_attempts())


def _detail_config() -> DetailServiceConfig:
    return DetailServiceConfig(
        per_source_concurrency=1,
        default_request_delay_seconds=0.0,
        request_delay_seconds_by_source={},
        stop_on_blocked=True,
        stop_on_rate_limited=True,
    )


def _service_config() -> SearchServiceConfig:
    return SearchServiceConfig(
        source_attempt_timeout_seconds=30.0,
        run_timeout_seconds=60.0,
        fetch_timeout_seconds=15.0,
        retry=RetryServiceConfig(max_attempts=1, backoff_seconds=0.0),
        detail=_detail_config(),
    )


def _raw_search_record(raw_listing: RawListing, *, append_sequence: int = 0) -> RawSearchRecord:
    return RawSearchRecord(
        run_id="r-test",
        append_sequence=append_sequence,
        query_variant="QA",
        source=raw_listing.source,
        source_type=SourceType.AGGREGATOR,
        collected_at=datetime(2026, 6, 24, 10, 0, tzinfo=UTC),
        listing=raw_listing,
        source_url=f"https://example.test/{raw_listing.source}/search?q=QA",
    )


def _append_detail_work_items(
    store: SqliteRunStore,
    listings: tuple[RawListing, ...],
) -> tuple[DetailWorkItem, ...]:
    for raw_listing in listings:
        store.append_raw_record(_raw_search_record(raw_listing))
    return tuple(
        DetailWorkItem(
            raw_record_id=row.raw_record_id,
            source=str(row.payload["source"]),
            query_variant=str(row.payload["query_variant"]),
            listing=listings[index],
        )
        for index, row in enumerate(store.read_raw_record_rows())
    )


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

    async def test_detail_enrichment_scraper_search_phase_writes_search_only_raw_record(self) -> None:
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
                ["https://example.test/detail_jobs/search?q=QA"],
                [call.url for call in fetcher.calls],
            )
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual(1, len(raw_records))
            self.assertEqual("not_requested", raw_records[0]["description_availability"])
            self.assertFalse(raw_records[0]["detail_fetched"])
            self.assertEqual("https://example.test/detail_jobs/jobs/1", raw_records[0]["listing"]["url"])
            self.assertIsNone(raw_records[0]["listing"]["description"])
            self.assertIsNone(raw_records[0]["listing"]["requirements"])

    async def test_detail_runner_enriches_every_work_item_without_count_budget(self) -> None:
        # Arrange
        listings = tuple(
            replace(listing("detail_jobs", str(index)), description=None, raw_text=f"QA Engineer {index}")
            for index in range(1, 6)
        )
        scraper = FakeDetailScraper(
            source_descriptor=descriptor("detail_jobs"),
            raw_listings=listings,
        )
        fetcher = FakeFetcher()
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                work_items = _append_detail_work_items(writer, listings)
                runner = DetailEnrichmentRunner(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=_detail_config(),
                )

                # Act
                result = await runner.run(work_items)

            # Assert
            self.assertEqual(5, result.attempted)
            self.assertEqual(5, result.enriched)
            self.assertEqual(0, result.failed)
            self.assertEqual(
                [f"https://example.test/detail_jobs/detail/{index}" for index in range(1, 6)],
                [call.url for call in fetcher.calls],
            )
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual(
                [f"Full detail description for QA Engineer {index}" for index in range(1, 6)],
                [record["listing"]["description"] for record in raw_records],
            )

    async def test_detail_runner_failure_preserves_search_listing_diagnostics(self) -> None:
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
                    ClassifiedSourceError(
                        SourceOutcome.NETWORK_ERROR,
                        "detail connection reset",
                    )
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                work_items = _append_detail_work_items(writer, listings)
                runner = DetailEnrichmentRunner(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=_detail_config(),
                )

                # Act
                result = await runner.run(work_items)

            # Assert
            self.assertEqual(2, result.attempted)
            self.assertEqual(1, result.enriched)
            self.assertEqual(1, result.failed)
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual("detail_parse_error", raw_records[0]["description_availability"])
            self.assertTrue(raw_records[0]["detail_fetched"])
            self.assertEqual("detail connection reset", raw_records[0]["detail_parse_error"])
            self.assertIsNone(raw_records[0]["listing"]["description"])
            self.assertEqual(
                "Full detail description for QA Engineer 2",
                raw_records[1]["listing"]["description"],
            )

    async def test_detail_runner_blocked_stops_source_and_preserves_remaining_rows(self) -> None:
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
                    ClassifiedSourceError(
                        SourceOutcome.BLOCKED,
                        "hh.ru account captcha on vacancy detail",
                    )
                ],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with _store(Path(tmp)) as writer:
                work_items = _append_detail_work_items(writer, listings)
                runner = DetailEnrichmentRunner(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=_detail_config(),
                )

                # Act
                result = await runner.run(work_items)

            # Assert
            self.assertEqual(1, result.attempted)
            self.assertEqual(0, result.enriched)
            self.assertEqual(1, result.failed)
            self.assertEqual(("detail_jobs",), result.stopped_sources)
            self.assertEqual(["https://example.test/detail_jobs/detail/1"], [call.url for call in fetcher.calls])
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual("detail_blocked", raw_records[0]["description_availability"])
            self.assertTrue(raw_records[0]["detail_fetched"])
            self.assertEqual(
                "hh.ru account captcha on vacancy detail",
                raw_records[0]["detail_parse_error"],
            )
            self.assertEqual("not_requested", raw_records[1]["description_availability"])
            self.assertFalse(raw_records[1]["detail_fetched"])

    async def test_detail_runner_rate_limited_preserves_listing_and_status(self) -> None:
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
                work_items = _append_detail_work_items(writer, listings)
                runner = DetailEnrichmentRunner(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=fetcher,
                    writer=writer,
                    config=_detail_config(),
                )

                # Act
                result = await runner.run(work_items)

            # Assert
            self.assertEqual(1, result.attempted)
            self.assertEqual(("detail_jobs",), result.stopped_sources)
            raw_records = _read_raw_records(Path(tmp))
            self.assertEqual("detail_rate_limited", raw_records[0]["description_availability"])
            self.assertEqual("HTTP Error 429: Too Many Requests", raw_records[0]["detail_parse_error"])

    async def test_pipeline_fetches_detail_only_for_pre_enrichment_kept_rows(self) -> None:
        # Arrange
        kept = replace(
            listing("detail_jobs", "1"),
            company="Acme",
            description=None,
            raw_text="QA Engineer 1",
        )
        filtered = replace(
            listing("detail_jobs", "2"),
            company="BlockedCorp",
            description=None,
            raw_text="QA Engineer 2",
        )
        scraper = FakeDetailScraper(
            source_descriptor=descriptor("detail_jobs"),
            raw_listings=(kept, filtered),
        )
        fetcher = FakeFetcher()
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = SearchPipeline(
                config=SearchPipelineConfig(
                    runs_dir=Path(tmp),
                    service_config=_service_config(),
                ),
                fetcher=fetcher,
                postprocessor=ResultTablePostProcessor(),
                run_store_factory=_store_factory,
                catalog=SourceCatalog((supported(scraper),)),
            )

            # Act
            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    exclude_companies=("Blocked",),
                ),
                run_id="r-test",
            )

            # Assert
            self.assertEqual(1, execution.detail_summary["total_detail_work_items"])
            self.assertEqual(1, execution.detail_summary["attempted"])
            self.assertEqual(
                [
                    "https://example.test/detail_jobs/search?q=QA",
                    "https://example.test/detail_jobs/detail/1",
                ],
                [call.url for call in fetcher.calls],
            )
            with SqliteRunStore(execution.paths.database_path, run_id="r-test") as store:
                raw_records = store.read_raw_records()
                pre_processed = store.read_processed_results(append_sequence=0, phase="pre_enrichment")
                final_processed = store.read_processed_results(append_sequence=0)
            by_id = {record["listing"]["source_listing_id"]: record for record in raw_records}
            self.assertTrue(by_id["1"]["detail_fetched"])
            self.assertEqual("Full detail description for QA Engineer 1", by_id["1"]["listing"]["description"])
            self.assertFalse(by_id["2"]["detail_fetched"])
            self.assertIsNone(by_id["2"]["listing"]["description"])
            self.assertEqual("pre_enrichment", pre_processed["phase"])
            self.assertEqual("final", final_processed["phase"])
            self.assertEqual(1, final_processed["result_count"])
            self.assertEqual(1, len(final_processed["filtered_out_results"]))


if __name__ == "__main__":
    unittest.main()
