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
from job_harness.v2.runtime import DetailServiceConfig, RetryServiceConfig, SearchServiceConfig, implemented_source_ids
from job_harness.v2.serialization import to_jsonable
from job_harness.v2.source_catalog import source_catalog_entries

LIVE_SOURCE_ATTEMPT_TIMEOUT_SECONDS = 240.0
LIVE_RUN_TIMEOUT_SECONDS = 480.0
LIVE_FETCH_TIMEOUT_SECONDS = 30.0
LIGHT_SOURCE_ATTEMPT_TIMEOUT_SECONDS = 60.0
LIGHT_RUN_TIMEOUT_SECONDS = 120.0
LIGHT_FETCH_TIMEOUT_SECONDS = 15.0
LIGHT_SOURCE_IDS = ("career:jetbrains", "jobturbo")
LIVE_PROFILES = ("full", "light")


@dataclass(frozen=True)
class LiveE2EReport:
    catalog_source_count: int
    source_profile: str
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
    parser.add_argument(
        "--profile",
        choices=LIVE_PROFILES,
        default="full",
        help="Live source profile to run. full probes every implemented source; light probes a bounded subset.",
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
                profile=args.profile,
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
    profile: str,
) -> dict[str, object]:
    expected_source_ids = _profile_source_ids(profile)
    catalog_source_count = len(source_catalog_entries())
    app = V2SearchApplication(config=_live_search_config(runs_dir, profile=profile))

    if phase == "initial":
        first = await app.search(_initial_live_request(profile))
        return _initial_report_payload(
            catalog_source_count=catalog_source_count,
            source_profile=profile,
            expected_source_ids=expected_source_ids,
            execution=first,
        )

    if phase == "append":
        if append_to_run_id is None:
            raise ValueError("append_to_run_id is required for append phase")
        second = await app.search(_append_live_request(profile, append_to_run_id=append_to_run_id))
        return _append_report_payload(execution=second)

    first = await app.search(_initial_live_request(profile))
    second = await app.search(_append_live_request(profile, append_to_run_id=first.run_id))
    return report_payload(
        LiveE2EReport(
            catalog_source_count=catalog_source_count,
            source_profile=profile,
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
        "source_profile": report.source_profile,
        "selected_source_count": len(report.expected_source_ids),
        "expected_source_ids": report.expected_source_ids,
        "first": _execution_payload(report.first),
        "second": _execution_payload(report.second),
    }


def _initial_report_payload(
    *,
    catalog_source_count: int,
    source_profile: str,
    expected_source_ids: tuple[str, ...],
    execution: V2SearchExecution,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "v2_live_e2e_initial_report",
        "catalog_source_count": catalog_source_count,
        "source_profile": source_profile,
        "selected_source_count": len(expected_source_ids),
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
            "database": str(execution.paths.database_path),
            "raw_listings_table": "raw_listings",
            "source_attempts_table": "source_attempts",
            "run_manifest_table": "run_manifest",
            "processed_results_table": "processed_results",
            "report_html": str(execution.paths.report_html_path),
        },
        "raw_records_written_this_call": execution.raw_records_written,
        "processed_result_count": execution.processed_results.result_count,
        "detail_summary": execution.detail_summary,
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


def _live_search_config(runs_dir: Path, *, profile: str) -> V2SearchConfig:
    if profile == "light":
        return V2SearchConfig(
            runs_dir=runs_dir,
            source_ids=LIGHT_SOURCE_IDS,
            service_config=_live_service_config(
                source_attempt_timeout_seconds=LIGHT_SOURCE_ATTEMPT_TIMEOUT_SECONDS,
                run_timeout_seconds=LIGHT_RUN_TIMEOUT_SECONDS,
                fetch_timeout_seconds=LIGHT_FETCH_TIMEOUT_SECONDS,
            ),
        )
    if profile != "full":
        raise ValueError(f"unknown live e2e profile: {profile}")
    return V2SearchConfig(
        runs_dir=runs_dir,
        source_ids=(),
        service_config=_live_service_config(
            source_attempt_timeout_seconds=LIVE_SOURCE_ATTEMPT_TIMEOUT_SECONDS,
            run_timeout_seconds=LIVE_RUN_TIMEOUT_SECONDS,
            fetch_timeout_seconds=LIVE_FETCH_TIMEOUT_SECONDS,
        ),
    )


def _live_service_config(
    *,
    source_attempt_timeout_seconds: float,
    run_timeout_seconds: float,
    fetch_timeout_seconds: float,
) -> SearchServiceConfig:
    return SearchServiceConfig(
        source_attempt_timeout_seconds=source_attempt_timeout_seconds,
        run_timeout_seconds=run_timeout_seconds,
        fetch_timeout_seconds=fetch_timeout_seconds,
        retry=RetryServiceConfig(max_attempts=1, backoff_seconds=0.0),
        detail=DetailServiceConfig(
            per_source_concurrency=1,
            default_request_delay_seconds=0.75,
            request_delay_seconds_by_source={
                "hh_ru": 1.5,
                "hirify": 0.75,
            },
            stop_on_blocked=True,
            stop_on_rate_limited=True,
        ),
    )


def _initial_live_request(profile: str) -> SearchRequest:
    if profile == "light":
        return SearchRequest(query_variants=("Developer",))
    if profile != "full":
        raise ValueError(f"unknown live e2e profile: {profile}")
    return SearchRequest(
        query_variants=("QA",),
        grades=(Grade.MIDDLE,),
        salary_from=150_000,
        vacancy_geographies=("country:RU", "country:AM"),
    )


def _append_live_request(profile: str, *, append_to_run_id: str) -> SearchRequest:
    if profile == "light":
        return SearchRequest(
            query_variants=("QA",),
            exclude_text=(
                TextExclusion(
                    pattern="zzzzzz-no-live-e2e-match",
                    mode=TextExclusionMode.SUBSTRING,
                ),
            ),
            append_to_run_id=append_to_run_id,
        )
    if profile != "full":
        raise ValueError(f"unknown live e2e profile: {profile}")
    return SearchRequest(
        query_variants=("тестировщик",),
        vacancy_geographies=("country:RU", "country:AM"),
        exclude_text=(
            TextExclusion(
                pattern="zzzzzz-no-live-e2e-match",
                mode=TextExclusionMode.SUBSTRING,
            ),
        ),
        append_to_run_id=append_to_run_id,
    )


def _profile_source_ids(profile: str) -> tuple[str, ...]:
    if profile == "full":
        return implemented_source_ids()
    if profile == "light":
        implemented = frozenset(implemented_source_ids())
        missing = tuple(source_id for source_id in LIGHT_SOURCE_IDS if source_id not in implemented)
        if missing:
            raise ValueError(f"light live e2e sources are not implemented: {', '.join(missing)}")
        return tuple(source_id for source_id in implemented_source_ids() if source_id in LIGHT_SOURCE_IDS)
    raise ValueError(f"unknown live e2e profile: {profile}")


if __name__ == "__main__":
    raise SystemExit(main())
