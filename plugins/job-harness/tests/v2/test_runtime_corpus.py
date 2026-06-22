from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.v2._support.contract_runtime import listing

from job_harness.v2.contracts import (
    AttemptCounts,
    CriteriaDiagnostics,
    RetryInfo,
    RetryNextAction,
    SearchCriterion,
    SourceAttemptRecord,
    SourceOutcome,
    SourceType,
)
from job_harness.v2.contracts.records import RawSearchRecord
from job_harness.v2.runtime import RawCorpusWriter


def _raw_record(index: int) -> RawSearchRecord:
    return RawSearchRecord(
        run_id="r-test",
        append_sequence=0,
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class RawCorpusWriterTest(unittest.TestCase):
    def test_writes_raw_records_attempt_records_and_atomic_manifest(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            with RawCorpusWriter(Path(tmp)) as writer:
                # Act
                writer.append_raw_record(_raw_record(1))
                writer.append_attempt_record(_attempt_record())
                writer.replace_run_manifest({"run_id": "r-test", "source_attempts": [SourceOutcome.SUCCESS]})

            # Assert
            raw_records = _read_jsonl(Path(tmp) / "raw-listings.jsonl")
            attempt_records = _read_jsonl(Path(tmp) / "source-attempts.jsonl")
            manifest = json.loads((Path(tmp) / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("raw_listing", raw_records[0]["record_type"])
            self.assertEqual("source_attempt", attempt_records[0]["record_type"])
            self.assertEqual(["success"], manifest["source_attempts"])

    def test_concurrent_raw_writes_preserve_line_integrity(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            with RawCorpusWriter(Path(tmp)) as writer:
                records = tuple(_raw_record(index) for index in range(25))

                # Act
                with ThreadPoolExecutor(max_workers=5) as pool:
                    tuple(pool.map(writer.append_raw_record, records))

            # Assert
            raw_records = _read_jsonl(Path(tmp) / "raw-listings.jsonl")
            self.assertEqual(25, len(raw_records))
            self.assertEqual(
                {str(index) for index in range(25)},
                {record["listing"]["source_listing_id"] for record in raw_records},
            )


if __name__ == "__main__":
    unittest.main()
