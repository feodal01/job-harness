"""CLI entrypoint for the v2 contract-first search engine."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from job_harness.v2.application import (
    V2SearchApplication,
    V2SearchConfig,
    V2SearchExecution,
    render_processed_results_markdown_file,
)
from job_harness.v2.contracts import (
    Grade,
    SearchRequest,
    SourceAttemptRecord,
    SourceType,
    TextExclusion,
    TextExclusionMode,
)
from job_harness.v2.runtime import RetryPolicy, implemented_source_ids
from job_harness.v2.runtime.serialization import to_jsonable
from job_harness.v2.source_catalog import country_catalog_entries, source_catalog_entries


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list-sources":
            _print_json(_source_catalog_payload())
            return 0
        if args.command == "search":
            execution = asyncio.run(_run_search(args))
            _print_json(_execution_payload(execution))
            return 0
        if args.command == "format":
            return _run_format(args)
    except Exception as exc:
        print(f"job-harness-v2 failed: {exc}", file=sys.stderr)
        return 1
    parser.error("missing command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-harness-v2")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list-sources", help="Print the v2 source catalog.")

    search = subparsers.add_parser("search", help="Run a v2 job search.")
    search.add_argument("--query", action="append", required=True, help="Search query variant. Repeatable.")
    search.add_argument("--grade", action="append", choices=_grade_values(), default=[])
    search.add_argument("--salary-from", type=int)
    search.add_argument("--published-since", type=_date_arg)
    search.add_argument("--exclude-company", action="append", default=[])
    search.add_argument("--exclude-text", action="append", default=[])
    search.add_argument("--exclude-regex", action="append", default=[])
    search.add_argument("--relocation", choices=("true", "false"))
    search.add_argument("--remote-in-country", choices=("true", "false"))
    search.add_argument("--remote-global", choices=("true", "false"))
    search.add_argument("--country", action="append", default=[])
    search.add_argument("--city", action="append", default=[])
    search.add_argument("--source", action="append", default=[])
    search.add_argument("--source-type", action="append", choices=_source_type_values(), default=[])
    search.add_argument("--append-to-run-id")
    search.add_argument("--run-id")
    search.add_argument("--runs-dir", type=Path, default=Path(".job-harness/v2/runs"))
    search.add_argument("--source-attempt-timeout", type=float, default=30.0)
    search.add_argument("--run-timeout", type=float, default=120.0)
    search.add_argument("--fetch-timeout", type=float, default=15.0)
    search.add_argument("--retry-attempts", type=int, default=1)

    format_cmd = subparsers.add_parser("format", help="Render processed-results.json as readable markdown.")
    format_cmd.add_argument("--input", type=Path, required=True, help="Path to processed-results.json.")
    format_cmd.add_argument("--output", type=Path, help="Write markdown to this file instead of stdout.")
    format_cmd.add_argument(
        "--description-limit",
        type=int,
        default=0,
        help="Max characters per description/requirements section; 0 keeps full text.",
    )
    format_cmd.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max listings to render; 0 includes every processed result.",
    )
    return parser


def _run_format(args: argparse.Namespace) -> int:
    markdown = render_processed_results_markdown_file(
        args.input,
        description_limit=args.description_limit,
        listing_limit=args.limit,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        return 0
    print(markdown)
    return 0


async def _run_search(args: argparse.Namespace) -> V2SearchExecution:
    request = SearchRequest(
        query_variants=tuple(args.query),
        grades=tuple(Grade(value) for value in args.grade),
        salary_from=args.salary_from,
        published_since=args.published_since,
        exclude_companies=tuple(args.exclude_company),
        exclude_text=_text_exclusions(args),
        relocation=_optional_bool(args.relocation),
        remote_in_country=_optional_bool(args.remote_in_country),
        remote_global=_optional_bool(args.remote_global),
        countries=tuple(args.country),
        cities=tuple(args.city),
        sources=tuple(args.source),
        source_types=tuple(SourceType(value) for value in args.source_type),
        append_to_run_id=args.append_to_run_id,
    )
    app = V2SearchApplication(
        config=V2SearchConfig(
            runs_dir=args.runs_dir,
            source_attempt_timeout_seconds=args.source_attempt_timeout,
            run_timeout_seconds=args.run_timeout,
            fetch_timeout_seconds=args.fetch_timeout,
            retry_policy=RetryPolicy(max_attempts=args.retry_attempts),
        )
    )
    return await app.search(request, run_id=args.run_id)


def _source_catalog_payload() -> dict[str, object]:
    implemented = frozenset(implemented_source_ids())
    return {
        "schema_version": 1,
        "record_type": "source_catalog",
        "countries": [
            {
                "country_code": country.country_code,
                "display_name": country.display_name,
                "search_enabled": country.search_enabled,
            }
            for country in country_catalog_entries()
        ],
        "sources": [
            {
                "source_id": entry.source_id,
                "source_type": entry.source_type,
                "transport": entry.transport,
                "countries": entry.countries,
                "source_limit": entry.source_limit,
                "implemented": entry.source_id in implemented,
                "native_request_criteria": tuple(
                    criterion.value for criterion in sorted(entry.native_request_criteria, key=lambda item: item.value)
                ),
                "structured_output_criteria": tuple(
                    criterion.value
                    for criterion in sorted(entry.structured_output_criteria, key=lambda item: item.value)
                ),
                "required_fixture_kinds": tuple(
                    kind.value
                    for kind in sorted(
                        entry.required_fixture_kinds.required_kinds,
                        key=lambda item: item.value,
                    )
                ),
            }
            for entry in source_catalog_entries()
        ],
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


def _text_exclusions(args: argparse.Namespace) -> tuple[TextExclusion, ...]:
    substring_exclusions = tuple(
        TextExclusion(pattern=value, mode=TextExclusionMode.SUBSTRING)
        for value in args.exclude_text
    )
    regex_exclusions = tuple(
        TextExclusion(pattern=value, mode=TextExclusionMode.REGEX)
        for value in args.exclude_regex
    )
    return substring_exclusions + regex_exclusions


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ISO date YYYY-MM-DD") from exc


def _grade_values() -> tuple[str, ...]:
    return tuple(item.value for item in Grade)


def _source_type_values() -> tuple[str, ...]:
    return tuple(item.value for item in SourceType)


def _print_json(payload: object) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
