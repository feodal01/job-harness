from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.v2._support.contract_runtime import (
    FakeFetcher,
    FakeScraper,
    descriptor,
    listing,
    supported,
)

from job_harness.v2.contracts import SearchRequest, SourceOutcome
from job_harness.v2.runtime import (
    ClassifiedSourceError,
    OrchestratorConfig,
    RawCorpusWriter,
    RetryPolicy,
    SearchOrchestrator,
    SourceCatalog,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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
            with RawCorpusWriter(Path(tmp)) as writer:
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
            self.assertEqual(1, len(_read_jsonl(Path(tmp) / "raw-listings.jsonl")))

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
            with RawCorpusWriter(Path(tmp)) as writer:
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
            with RawCorpusWriter(Path(tmp)) as writer:
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
            attempt_records = _read_jsonl(Path(tmp) / "source-attempts.jsonl")
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
            with RawCorpusWriter(Path(tmp)) as writer:
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
            with RawCorpusWriter(Path(tmp)) as writer:
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
            self.assertEqual([], _read_jsonl(Path(tmp) / "raw-listings.jsonl"))

    async def test_append_mode_preserves_existing_raw_records(self) -> None:
        # Arrange
        scraper = FakeScraper(
            source_descriptor=descriptor("hh_ru"),
            raw_listings=(listing("hh_ru"),),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with RawCorpusWriter(Path(tmp)) as writer:
                orchestrator = SearchOrchestrator(
                    catalog=SourceCatalog((supported(scraper),)),
                    fetcher=FakeFetcher(),
                    writer=writer,
                    config=OrchestratorConfig(retry_policy=RetryPolicy(max_attempts=1)),
                )

                # Act
                await orchestrator.run(SearchRequest(query_variants=("QA",)), run_id="r-test")
                await orchestrator.run(
                    SearchRequest(query_variants=("quality assurance",), append_to_run_id="r-test"),
                    append_sequence=1,
                )

            # Assert
            raw_records = _read_jsonl(Path(tmp) / "raw-listings.jsonl")
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
            with RawCorpusWriter(Path(tmp)) as writer:
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


if __name__ == "__main__":
    unittest.main()
