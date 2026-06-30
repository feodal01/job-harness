from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tests.v2._support.contract_runtime import listing

from job_harness.v2.contracts import (
    AttemptCounts,
    CriteriaDiagnostics,
    DescriptionAvailability,
    RetryInfo,
    RetryNextAction,
    SearchCriterion,
    SourceAttemptRecord,
    SourceOutcome,
    SourceType,
)
from job_harness.v2.contracts.records import RawSearchRecord
from job_harness.v2.persistence import SqliteRunStore, read_processed_results_payload


def _raw_record(index: int, *, append_sequence: int = 0) -> RawSearchRecord:
    return RawSearchRecord(
        run_id="r-test",
        append_sequence=append_sequence,
        query_variant="QA",
        source="hh_ru",
        source_type=SourceType.AGGREGATOR,
        collected_at=datetime(2026, 6, 22, 10, 0, tzinfo=UTC),
        listing=listing("hh_ru", str(index)),
        source_url="https://example.test/hh_ru/search?q=QA",
    )


def _attempt_record() -> SourceAttemptRecord:
    now = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    return SourceAttemptRecord(
        source="hh_ru",
        source_type=SourceType.AGGREGATOR,
        query_variant="QA",
        attempt=1,
        outcome=SourceOutcome.SUCCESS,
        started_at=now,
        finished_at=now,
        elapsed_ms=0,
        source_limit=10,
        limit_reached=False,
        counts=AttemptCounts(raw_listings_written=1, pages_visited=1),
        criteria=CriteriaDiagnostics(
            requested=frozenset({SearchCriterion.QUERY}),
            native_applied=frozenset({SearchCriterion.QUERY}),
        ),
        retry=RetryInfo(attempts=1, max_attempts=1, next_action=RetryNextAction.NONE),
    )


class SqliteRunStoreTest(unittest.TestCase):
    def test_read_processed_results_rejects_missing_database(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "missing.sqlite"

            # Act / Assert
            with self.assertRaises(FileNotFoundError):
                read_processed_results_payload(database_path)

    def test_writes_run_tables(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp, SqliteRunStore(Path(tmp) / "run.sqlite", run_id="r-test") as store:
            store.reserve_append_attempt({"query_variants": ["QA"]})

            # Act
            store.append_raw_record(_raw_record(1))
            store.append_attempt_record(_attempt_record())
            store.replace_run_manifest({"run_id": "r-test", "source_attempts": [SourceOutcome.SUCCESS]})
            store.write_processed_results(
                {
                    "record_type": "processed_results",
                    "phase": "final",
                    "run_id": "r-test",
                    "append_sequence": 0,
                    "raw_records_read": 1,
                    "result_count": 1,
                    "results": [],
                }
            )

            # Assert
            raw_records = store.read_raw_records()
            attempt_records = store.read_source_attempts()
            manifest = store.read_run_manifest()
            processed = store.read_processed_results()
            self.assertEqual("raw_listing", raw_records[0]["record_type"])
            self.assertEqual("source_attempt", attempt_records[0]["record_type"])
            self.assertEqual(["success"], manifest["source_attempts"])
            self.assertEqual("processed_results", processed["record_type"])
            self.assertEqual("final", processed["phase"])

    def test_updates_raw_record_detail_fields(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp, SqliteRunStore(Path(tmp) / "run.sqlite", run_id="r-test") as store:
            store.reserve_append_attempt({"query_variants": ["QA"]})
            store.append_raw_record(_raw_record(1))
            raw_record_id = store.read_raw_record_rows()[0].raw_record_id
            detailed = replace(
                listing("hh_ru", "1"),
                description="Full detail description",
                requirements="Full detail requirements",
            )

            # Act
            store.update_raw_record_detail(
                raw_record_id=raw_record_id,
                listing=detailed,
                description_availability=DescriptionAvailability.PRESENT,
                detail_fetched=True,
                detail_parse_error=None,
            )

            # Assert
            raw_records = store.read_raw_records()
            raw_rows = store.read_raw_record_rows()
            self.assertEqual(raw_record_id, raw_rows[0].raw_record_id)
            self.assertEqual("present", raw_records[0]["description_availability"])
            self.assertTrue(raw_records[0]["detail_fetched"])
            self.assertEqual("Full detail description", raw_records[0]["listing"]["description"])
            self.assertEqual("Full detail requirements", raw_records[0]["listing"]["requirements"])

    def test_updates_raw_record_listing_metadata_without_touching_detail_status(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp, SqliteRunStore(Path(tmp) / "run.sqlite", run_id="r-test") as store:
            store.reserve_append_attempt({"query_variants": ["QA"]})
            store.append_raw_record(_raw_record(1))
            raw_record_id = store.read_raw_record_rows()[0].raw_record_id
            enriched = replace(
                listing("hh_ru", "1"),
                raw={
                    "application_channels": [
                        {
                            "type": "company_career_page",
                            "label": "Careers",
                            "url": "https://example.test/careers",
                        }
                    ]
                },
            )

            # Act
            store.update_raw_record_listing(raw_record_id=raw_record_id, listing=enriched)

            # Assert
            raw_records = store.read_raw_records()
            self.assertEqual("not_requested", raw_records[0]["description_availability"])
            self.assertFalse(raw_records[0]["detail_fetched"])
            self.assertEqual(
                "https://example.test/careers",
                raw_records[0]["listing"]["raw"]["application_channels"][0]["url"],
            )

    def test_concurrent_raw_writes_preserve_records(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp, SqliteRunStore(Path(tmp) / "run.sqlite", run_id="r-test") as store:
            store.reserve_append_attempt({"query_variants": ["QA"]})
            records = tuple(_raw_record(index) for index in range(25))

            # Act
            with ThreadPoolExecutor(max_workers=5) as pool:
                tuple(pool.map(store.append_raw_record, records))

            # Assert
            raw_records = store.read_raw_records()
            self.assertEqual(25, len(raw_records))
            self.assertEqual(
                {str(index) for index in range(25)},
                {record["listing"]["source_listing_id"] for record in raw_records},
            )

    def test_append_attempt_sequences_are_allocated_atomically_per_run(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            database_path = Path(tmp) / "run.sqlite"
            with SqliteRunStore(database_path, run_id="r-test") as first:
                first_sequence = first.reserve_append_attempt({"query_variants": ["QA"]})
                first.mark_append_attempt_completed()
            with SqliteRunStore(database_path, run_id="r-test") as second:
                second_sequence = second.reserve_append_attempt({"query_variants": ["AQA"]})

            # Assert
            self.assertEqual(0, first_sequence)
            self.assertEqual(1, second_sequence)


if __name__ == "__main__":
    unittest.main()
