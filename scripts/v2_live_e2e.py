#!/usr/bin/env python3
"""Live end-to-end scenario for the v2 search engine.

Companion runner for scripts/verify_v2.py. Invoked under the plugin environment:

    uv --directory plugins/job-harness run python scripts/v2_live_e2e.py --runs-dir /tmp/runs
    uv --directory plugins/job-harness run python scripts/v2_live_e2e.py --runs-dir /tmp/runs --phase initial
    uv --directory plugins/job-harness run python scripts/v2_live_e2e.py --runs-dir /tmp/runs --phase append \\
        --append-to-run-id r-...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.application import V2SearchApplication, V2SearchConfig, V2SearchExecution
from job_harness.v2.contracts import Grade, SearchRequest, SourceAttemptRecord, TextExclusion, TextExclusionMode
from job_harness.v2.runtime import RetryPolicy, implemented_source_ids
from job_harness.v2.runtime.serialization import to_jsonable
from job_harness.v2.source_catalog import source_catalog_entries

LIVE_SOURCE_ATTEMPT_TIMEOUT_SECONDS = 90.0
LIVE_RUN_TIMEOUT_SECONDS = 180.0
LIVE_FETCH_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class LiveE2EReport:
    catalog_source_count: int
    expected_source_ids: tuple[str, ...]
    first: V2SearchExecution
    second: V2SearchExecution


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir",
        type=Path,
        required=True,
        help="Directory for v2 run artifacts.",
    )
    parser.add_argument(
        "--phase",
        choices=("initial", "append", "both"),
        default="both",
        help="Run only the initial search, only an append, or both in one process.",
    )
    parser.add_argument(
        "--append-to-run-id",
        help="Existing run id for --phase append.",
    )
    args = parser.parse_args(argv)
    if args.phase == "append" and not args.append_to_run_id:
        parser.error("--append-to-run-id is required for --phase append")
    try:
        payload = asyncio.run(
            run_live_e2e_phase(
                runs_dir=args.runs_dir,
                phase=args.phase,
                append_to_run_id=args.append_to_run_id,
            )
        )
    except Exception as exc:
        print(f"job-harness-v2 live e2e failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True))
    return 0


async def run_live_e2e_phase(
    *,
    runs_dir: Path,
    phase: str,
    append_to_run_id: str | None,
) -> dict[str, object]:
    expected_source_ids = implemented_source_ids()
    catalog_source_count = len(source_catalog_entries())
    app = V2SearchApplication(config=_live_search_config(runs_dir))

    if phase == "initial":
        first = await app.search(_initial_live_request())
        return _initial_report_payload(
            catalog_source_count=catalog_source_count,
            expected_source_ids=expected_source_ids,
            execution=first,
        )

    if phase == "append":
        if append_to_run_id is None:
            raise ValueError("append_to_run_id is required for append phase")
        second = await app.search(_append_live_request(append_to_run_id=append_to_run_id))
        return _append_report_payload(execution=second)

    first = await app.search(_initial_live_request())
    second = await app.search(_append_live_request(append_to_run_id=first.run_id))
    return report_payload(
        LiveE2EReport(
            catalog_source_count=catalog_source_count,
            expected_source_ids=expected_source_ids,
            first=first,
            second=second,
        )
    )


def report_payload(report: LiveE2EReport) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "v2_live_e2e_report",
        "catalog_source_count": report.catalog_source_count,
        "expected_source_ids": report.expected_source_ids,
        "first": _execution_payload(report.first),
        "second": _execution_payload(report.second),
    }


def _initial_report_payload(
    *,
    catalog_source_count: int,
    expected_source_ids: tuple[str, ...],
    execution: V2SearchExecution,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "v2_live_e2e_initial_report",
        "catalog_source_count": catalog_source_count,
        "expected_source_ids": expected_source_ids,
        "execution": _execution_payload(execution),
    }


def _append_report_payload(*, execution: V2SearchExecution) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "v2_live_e2e_append_report",
        "execution": _execution_payload(execution),
    }


def _execution_payload(execution: V2SearchExecution) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "v2_search_execution",
        "run_id": execution.run_id,
        "append_sequence": execution.append_sequence,
        "run_dir": str(execution.paths.run_dir),
        "artifacts": {
            "raw_listings": str(execution.paths.raw_listings_path),
            "source_attempts": str(execution.paths.source_attempts_path),
            "run_manifest": str(execution.paths.run_manifest_path),
            "processed_results": str(execution.paths.processed_results_path),
        },
        "raw_records_written_this_call": execution.raw_records_written,
        "processed_result_count": execution.processed_results.result_count,
        "attempts": [_attempt_payload(attempt) for attempt in execution.attempts],
    }


def _attempt_payload(attempt: SourceAttemptRecord) -> dict[str, object]:
    return {
        "source": attempt.source,
        "query_variant": attempt.query_variant,
        "attempt": attempt.attempt,
        "outcome": attempt.outcome,
        "raw_listings_written": attempt.counts.raw_listings_written,
        "pages_visited": attempt.counts.pages_visited,
        "elapsed_ms": attempt.elapsed_ms,
        "limit_reached": attempt.limit_reached,
    }


def _live_search_config(runs_dir: Path) -> V2SearchConfig:
    return V2SearchConfig(
        runs_dir=runs_dir,
        source_ids=(),
        source_attempt_timeout_seconds=LIVE_SOURCE_ATTEMPT_TIMEOUT_SECONDS,
        run_timeout_seconds=LIVE_RUN_TIMEOUT_SECONDS,
        fetch_timeout_seconds=LIVE_FETCH_TIMEOUT_SECONDS,
        retry_policy=RetryPolicy(max_attempts=1),
    )


def _initial_live_request() -> SearchRequest:
    return SearchRequest(
        query_variants=("QA",),
        grades=(Grade.MIDDLE,),
        salary_from=150_000,
        countries=("RU", "AM"),
    )


def _append_live_request(*, append_to_run_id: str) -> SearchRequest:
    return SearchRequest(
        query_variants=("тестировщик",),
        countries=("RU", "AM"),
        exclude_text=(
            TextExclusion(
                pattern="zzzzzz-no-live-e2e-match",
                mode=TextExclusionMode.SUBSTRING,
            ),
        ),
        append_to_run_id=append_to_run_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
