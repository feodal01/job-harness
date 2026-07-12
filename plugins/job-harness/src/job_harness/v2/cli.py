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
)
from job_harness.v2.contracts import (
    Grade,
    SearchRequest,
    SourceType,
    TextExclusion,
    TextExclusionMode,
    WorkFormat,
)
from job_harness.v2.persistence import read_graph_processed_payload
from job_harness.v2.presentation import render_processed_results_markdown
from job_harness.v2.runtime import fetch_ats_company_listings, implemented_source_ids
from job_harness.v2.serialization import to_jsonable
from job_harness.v2.source_catalog import country_catalog_entries, source_catalog_entries


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list-sources":
            _print_json(_source_catalog_payload())
            return 0
        if args.command == "search":
            request = _request_from_args(args)
            execution = asyncio.run(_run_search(args, request))
            _print_json(_execution_payload(execution))
            return 0
        if args.command == "parse-ats-url":
            result = asyncio.run(_run_ats_url_parse(args))
            _print_json(_ats_url_parse_payload(result))
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
    search.add_argument("--query", action="append", default=[], help="Search query variant. Repeatable.")
    search.add_argument(
        "--queries",
        action="append",
        default=[],
        help='Pipe-separated query variants, for example: "QA | AQA | SDET". Repeatable.',
    )
    search.add_argument("--grade", action="append", choices=_grade_values(), default=[])
    search.add_argument("--salary-from", type=int)
    search.add_argument("--published-since", type=_date_arg)
    search.add_argument("--exclude-company", action="append", default=[])
    search.add_argument("--exclude-text", action="append", default=[])
    search.add_argument("--exclude-regex", action="append", default=[])
    search.add_argument("--relocation", choices=("true", "false"))
    search.add_argument(
        "--work-format",
        action="append",
        choices=_work_format_values(),
        default=[],
        help=(
            "Workplace format: remote, hybrid, office, or unknown. "
            "Repeatable; unknown must be paired with a concrete format."
        ),
    )
    search.add_argument(
        "--remote-scope",
        action="append",
        default=[],
        help=(
            "Remote eligibility scope: global, country:<code>, region:<code>, or unknown. "
            "Repeatable; unknown must be paired with a concrete scope."
        ),
    )
    search.add_argument(
        "--vacancy-geography",
        action="append",
        default=[],
        help=(
            "Vacancy geography scope: country:<code>, region:<code>, city:<name>, or unknown. "
            "Repeatable; unknown must be paired with a concrete geography."
        ),
    )
    search.add_argument("--source", action="append", default=[])
    search.add_argument("--source-type", action="append", choices=_source_type_values(), default=[])
    search.add_argument("--append-to-run-id")
    search.add_argument("--run-id")
    search.add_argument("--runs-dir", type=Path, default=Path(".job-harness/v2/runs"))

    ats = subparsers.add_parser(
        "parse-ats-url",
        help="Parse one ATS career board URL into raw listings.",
    )
    ats.add_argument("url", help="Public ATS board URL or supported ATS API URL.")
    ats.add_argument("--company", help="Company name to attach to parsed listings.")
    ats.add_argument(
        "--source-id",
        default="adhoc:ats",
        help="Synthetic source id for returned listings. Defaults to adhoc:ats.",
    )
    ats.add_argument(
        "--platform",
        choices=_ats_platform_values(),
        help="Force a supported ATS parser for URLs that cannot be detected by pattern.",
    )
    ats.add_argument(
        "--source-limit",
        type=int,
        default=200,
        help="Maximum listings to return across all paginated ATS pages.",
    )
    ats.add_argument(
        "--query-variant",
        default="ats-url",
        help="Query variant label used in fetch requests and diagnostics.",
    )

    format_cmd = subparsers.add_parser("format", help="Render processed results from run.sqlite as markdown.")
    format_cmd.add_argument("--input", type=Path, required=True, help="Path to run.sqlite.")
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
    payload = read_graph_processed_payload(args.input)
    markdown = render_processed_results_markdown(
        payload,
        description_limit=args.description_limit,
        listing_limit=args.limit,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
        return 0
    print(markdown)
    return 0


def _request_from_args(args: argparse.Namespace) -> SearchRequest:
    return SearchRequest(
        query_variants=_query_variants(args),
        grades=tuple(Grade(value) for value in args.grade),
        salary_from=args.salary_from,
        published_since=args.published_since,
        exclude_companies=tuple(args.exclude_company),
        exclude_text=_text_exclusions(args),
        relocation=_optional_bool(args.relocation),
        work_formats=tuple(WorkFormat(value) for value in args.work_format),
        remote_scopes=tuple(args.remote_scope),
        vacancy_geographies=tuple(args.vacancy_geography),
        sources=tuple(args.source),
        source_types=tuple(SourceType(value) for value in args.source_type),
        append_to_run_id=args.append_to_run_id,
    )


async def _run_search(args: argparse.Namespace, request: SearchRequest) -> V2SearchExecution:
    app = V2SearchApplication(
        config=V2SearchConfig(
            runs_dir=args.runs_dir,
        )
    )
    return await app.search(request, run_id=args.run_id)


async def _run_ats_url_parse(args: argparse.Namespace) -> object:
    return await fetch_ats_company_listings(
        args.url,
        company=args.company,
        source_id=args.source_id,
        platform=args.platform,
        source_limit=args.source_limit,
        query_variant=args.query_variant,
    )


def _query_variants(args: argparse.Namespace) -> tuple[str, ...]:
    variants = list(args.query)
    for group in args.queries:
        parts = tuple(item.strip() for item in group.split("|"))
        if not parts or any(not item for item in parts):
            raise ValueError("--queries must contain non-empty variants separated by |")
        variants.extend(parts)
    return tuple(variants)


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
        "execution_id": execution.execution_id,
        "append_sequence": execution.append_sequence,
        "run_dir": str(execution.paths.run_dir),
        "artifacts": {
            "database": str(execution.paths.database_path),
            "listing_observations_table": "listing_observations",
            "parser_invocations_table": "parser_invocations",
            "final_vacancies_table": "final_vacancies",
            "report_html": str(execution.paths.report_html_path),
        },
        "result_count": len(execution.final_items),
    }


def _ats_url_parse_payload(result: object) -> dict[str, object]:
    payload = to_jsonable(result)
    if not isinstance(payload, dict):
        raise TypeError("ATS URL parse result must serialize to a JSON object")
    config = payload.get("config")
    if not isinstance(config, dict):
        raise TypeError("ATS URL parse result config must serialize to a JSON object")
    listings = payload.get("listings")
    if not isinstance(listings, list):
        raise TypeError("ATS URL parse result listings must serialize to a JSON array")
    return {
        "schema_version": 1,
        "record_type": "ats_url_parse",
        "source_id": config.get("source_id"),
        "company": config.get("company"),
        "platform": config.get("platform"),
        "career_url": config.get("career_url"),
        "board_url": config.get("board_url"),
        "pages_visited": payload.get("pages_visited"),
        "limit_reached": payload.get("limit_reached"),
        "listing_count": len(listings),
        "config": config,
        "listings": listings,
    }


def _text_exclusions(args: argparse.Namespace) -> tuple[TextExclusion, ...]:
    substring_exclusions = tuple(
        TextExclusion(pattern=value, mode=TextExclusionMode.SUBSTRING) for value in args.exclude_text
    )
    regex_exclusions = tuple(TextExclusion(pattern=value, mode=TextExclusionMode.REGEX) for value in args.exclude_regex)
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


def _ats_platform_values() -> tuple[str, ...]:
    return (
        "ashby",
        "bamboohr",
        "breezy",
        "comeet",
        "dreamjob",
        "greenhouse",
        "huntflow",
        "icims",
        "jazzhr",
        "jobvite",
        "join",
        "jsonld_jobposting",
        "lever",
        "personio",
        "recruitee",
        "smartrecruiters",
        "successfactors",
        "taleo",
        "teamtailor",
        "workable",
        "workday",
        "ycombinator",
    )


def _work_format_values() -> tuple[str, ...]:
    return tuple(item.value for item in WorkFormat)


def _print_json(payload: object) -> None:
    print(json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
