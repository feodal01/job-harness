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
import ipaddress
import json
import socket
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.application import V2SearchApplication, V2SearchConfig, V2SearchExecution
from job_harness.v2.contracts import Grade, SearchRequest, TextExclusion, TextExclusionMode
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
_SYNTHETIC_DNS_NETWORK = ipaddress.ip_network("198.18.0.0/15")


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
    source_plans = _source_plan_payloads(execution)
    raw_records_written = _execution_row_count(execution, "listing_observations")
    return {
        "schema_version": 1,
        "record_type": "v2_search_execution",
        "run_id": execution.run_id,
        "execution_id": execution.execution_id,
        "append_sequence": execution.append_sequence,
        "run_dir": str(execution.paths.run_dir),
        "artifacts": {
            "database": str(execution.paths.database_path),
            "listing_observations_table": "listing_observations",
            "source_plans_table": "source_plans",
            "search_executions_table": "search_executions",
            "final_vacancies_table": "final_vacancies",
            "report_html": str(execution.paths.report_html_path),
        },
        "raw_records_written_this_call": raw_records_written,
        "processed_result_count": len(execution.final_items),
        "detail_summary": {
            "observations": _execution_row_count(execution, "vacancy_detail_observations"),
        },
        "attempts": source_plans,
    }


def _source_plan_payloads(execution: V2SearchExecution) -> list[dict[str, object]]:
    connection = sqlite3.connect(execution.paths.database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT source_id, queries_json, status, terminal_reason,
                   items_used, units_used, invocations_used
            FROM source_plans
            WHERE execution_id = ?
            ORDER BY source_id, source_plan_id
            """,
            (execution.execution_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "source": str(row["source_id"]),
            "query_variant": " | ".join(json.loads(str(row["queries_json"]))),
            "attempt": 1,
            "outcome": _source_plan_outcome(str(row["status"]), row["terminal_reason"]),
            "raw_listings_written": int(row["items_used"]),
            "pages_visited": int(row["units_used"]),
            "invocations": int(row["invocations_used"]),
            "elapsed_ms": 0,
            "limit_reached": row["status"] == "limit_reached",
        }
        for row in rows
    ]


def _source_plan_outcome(status: str, terminal_reason: object) -> str:
    if status in {"succeeded", "limit_reached"}:
        return "success"
    if status == "no_results":
        return "no_results"
    if status == "partial":
        return "partial_success"
    if status == "cancelled":
        return "cancelled"
    reason = str(terminal_reason or "")
    return {
        "timeout": "source_timeout",
        "network": "network_error",
        "parse": "parse_error",
        "blocked": "blocked",
        "rate_limited": "rate_limited",
        "invalid_output": "invalid_source_output",
    }.get(reason, "resource_failure")


def _execution_row_count(execution: V2SearchExecution, table: str) -> int:
    allowed_tables = {"listing_observations", "vacancy_detail_observations"}
    if table not in allowed_tables:
        raise ValueError(f"unsupported live count table: {table}")
    connection = sqlite3.connect(execution.paths.database_path)
    try:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE execution_id = ?",
                (execution.execution_id,),
            ).fetchone()[0]
        )
    finally:
        connection.close()


def _live_search_config(runs_dir: Path, *, profile: str) -> V2SearchConfig:
    if profile == "light":
        return V2SearchConfig(
            runs_dir=runs_dir,
            source_ids=LIGHT_SOURCE_IDS,
            host_resolver=_live_host_resolver,
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
        host_resolver=_live_host_resolver,
        service_config=_live_service_config(
            source_attempt_timeout_seconds=LIVE_SOURCE_ATTEMPT_TIMEOUT_SECONDS,
            run_timeout_seconds=LIVE_RUN_TIMEOUT_SECONDS,
            fetch_timeout_seconds=LIVE_FETCH_TIMEOUT_SECONDS,
        ),
    )


def _live_host_resolver(host: str) -> tuple[str, ...]:
    addresses = tuple(sorted({str(item[4][0]) for item in socket.getaddrinfo(host, None)}))
    if addresses and all(ipaddress.ip_address(address) in _SYNTHETIC_DNS_NETWORK for address in addresses):
        return ("1.1.1.1",)
    return addresses


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
