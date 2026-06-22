from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tests.v2._support.contract_runtime import listing

from job_harness.v2.contracts import (
    AttemptCounts,
    AttemptEvidence,
    CriteriaDiagnostics,
    RawSearchRecord,
    RetryInfo,
    RetryNextAction,
    SearchCriterion,
    SearchRequest,
    SourceAttemptRecord,
    SourceOutcome,
    SourceType,
    TextExclusion,
)
from job_harness.v2.postprocessing import ResultTablePostProcessor
from job_harness.v2.runtime import RawCorpusWriter


class ResultTablePostProcessorTest(unittest.TestCase):
    def test_builds_filtered_deduped_processed_results(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with RawCorpusWriter(run_dir) as writer:
                writer.append_raw_record(_raw_record("1", company="Acme"))
                writer.append_raw_record(_raw_record("1", company="Acme"))
                writer.append_raw_record(_raw_record("2", company="BlockedCorp"))
                writer.append_attempt_record(_attempt_record())

            output_path = run_dir / "processed-results.json"

            # Act
            result = ResultTablePostProcessor().process(
                request=SearchRequest(
                    query_variants=("QA",),
                    exclude_companies=("blocked",),
                    exclude_text=(TextExclusion("legacy stack"),),
                    max_results=10,
                ),
                run_id="r-test",
                append_sequence=0,
                raw_listings_path=run_dir / "raw-listings.jsonl",
                source_attempts_path=run_dir / "source-attempts.jsonl",
                output_path=output_path,
            )

            # Assert
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(3, result.raw_records_read)
            self.assertEqual(1, payload["result_count"])
            self.assertEqual("1", payload["results"][0]["source_listing_id"])
            self.assertEqual({"excluded_company": 1}, payload["removed_counts"])
            self.assertEqual(
                "none_native_request",
                payload["source_criteria_plan"][0]["actions"][0]["action"],
            )

    def test_marks_text_enrichment_required_from_source_attempt_diagnostics(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with RawCorpusWriter(run_dir) as writer:
                writer.append_raw_record(_raw_record("1", company="Acme"))
                writer.append_attempt_record(
                    _attempt_record(
                        requested=frozenset({SearchCriterion.QUERY, SearchCriterion.REMOTE_GLOBAL}),
                        native=frozenset({SearchCriterion.QUERY}),
                        unsupported=frozenset({SearchCriterion.REMOTE_GLOBAL}),
                        postprocess=frozenset({SearchCriterion.REMOTE_GLOBAL}),
                    )
                )

            output_path = run_dir / "processed-results.json"

            # Act
            ResultTablePostProcessor().process(
                request=SearchRequest(
                    query_variants=("QA",),
                    remote_global=True,
                    max_results=10,
                ),
                run_id="r-test",
                append_sequence=0,
                raw_listings_path=run_dir / "raw-listings.jsonl",
                source_attempts_path=run_dir / "source-attempts.jsonl",
                output_path=output_path,
            )

            # Assert
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            actions = {
                action["criterion"]: action
                for action in payload["source_criteria_plan"][0]["actions"]
            }
            self.assertEqual("text_enrichment_required", actions["remote_global"]["action"])
            self.assertTrue(actions["remote_global"]["requires_enrichment"])

    def test_filters_query_when_source_did_not_apply_native_query(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with RawCorpusWriter(run_dir) as writer:
                writer.append_raw_record(
                    _raw_record("1", company="JetBrains", source="career:jetbrains", title="QA Engineer")
                )
                writer.append_raw_record(
                    _raw_record("2", company="JetBrains", source="career:jetbrains", title="Account Manager")
                )
                writer.append_attempt_record(
                    _attempt_record(
                        source="career:jetbrains",
                        source_type=SourceType.COMPANY_CAREER,
                        requested=frozenset({SearchCriterion.QUERY}),
                        native=frozenset(),
                        structured=frozenset({SearchCriterion.QUERY}),
                        postprocess=frozenset({SearchCriterion.QUERY}),
                    )
                )

            output_path = run_dir / "processed-results.json"

            # Act
            ResultTablePostProcessor().process(
                request=SearchRequest(query_variants=("QA",), max_results=10),
                run_id="r-test",
                append_sequence=0,
                raw_listings_path=run_dir / "raw-listings.jsonl",
                source_attempts_path=run_dir / "source-attempts.jsonl",
                output_path=output_path,
            )

            # Assert
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["result_count"])
            self.assertEqual("QA Engineer", payload["results"][0]["title"])
            self.assertEqual({"query_mismatch": 1}, payload["removed_counts"])

    def test_short_query_token_does_not_match_description_only_mentions(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with RawCorpusWriter(run_dir) as writer:
                writer.append_raw_record(
                    _raw_record(
                        "1",
                        company="JetBrains",
                        source="career:jetbrains",
                        title="AI Lead",
                        description="Works with product managers, developers, and QA specialists.",
                    )
                )
                writer.append_raw_record(
                    _raw_record(
                        "2",
                        company="JetBrains",
                        source="career:jetbrains",
                        title="QA Engineer",
                        description="Tests product behavior.",
                    )
                )
                writer.append_attempt_record(
                    _attempt_record(
                        source="career:jetbrains",
                        source_type=SourceType.COMPANY_CAREER,
                        requested=frozenset({SearchCriterion.QUERY}),
                        native=frozenset(),
                        structured=frozenset({SearchCriterion.QUERY}),
                        postprocess=frozenset({SearchCriterion.QUERY}),
                    )
                )

            output_path = run_dir / "processed-results.json"

            # Act
            ResultTablePostProcessor().process(
                request=SearchRequest(query_variants=("QA",), max_results=10),
                run_id="r-test",
                append_sequence=0,
                raw_listings_path=run_dir / "raw-listings.jsonl",
                source_attempts_path=run_dir / "source-attempts.jsonl",
                output_path=output_path,
            )

            # Assert
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(["QA Engineer"], [row["title"] for row in payload["results"]])


def _raw_record(
    source_listing_id: str,
    *,
    company: str,
    source: str = "hh_ru",
    title: str | None = None,
    description: str | None = None,
) -> RawSearchRecord:
    raw_listing = listing(source, source_listing_id)
    raw_listing = replace(
        raw_listing,
        company=company,
        title=title or raw_listing.title,
        description=description or title or "Modern QA role",
        raw_text=description or title or "Modern QA role",
    )
    return RawSearchRecord(
        run_id="r-test",
        append_sequence=0,
        query_variant="QA",
        source=source,
        source_type=SourceType.COMPANY_CAREER if source.startswith("career:") else SourceType.AGGREGATOR,
        collected_at=datetime(2026, 6, 22, 10, 0, tzinfo=UTC),
        listing=raw_listing,
        source_url=f"https://example.test/{source}/search?q=QA",
    )


def _attempt_record(
    *,
    source: str = "hh_ru",
    source_type: SourceType = SourceType.AGGREGATOR,
    requested: frozenset[SearchCriterion] = frozenset({SearchCriterion.QUERY}),
    native: frozenset[SearchCriterion] = frozenset({SearchCriterion.QUERY}),
    structured: frozenset[SearchCriterion] = frozenset(),
    unsupported: frozenset[SearchCriterion] = frozenset(),
    postprocess: frozenset[SearchCriterion] = frozenset(),
) -> SourceAttemptRecord:
    now = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    return SourceAttemptRecord(
        source=source,
        source_type=source_type,
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
            requested=requested,
            native_applied=native,
            structured_evidence_available=structured,
            unsupported=unsupported,
            postprocess=postprocess,
        ),
        retry=RetryInfo(attempts=1, max_attempts=1, next_action=RetryNextAction.NONE),
        evidence=AttemptEvidence(),
    )


if __name__ == "__main__":
    unittest.main()
