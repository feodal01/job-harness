"""CLI entry point for job-harness.

Four commands, all dispatched through the same async SearchEngine that
the MCP server uses:

  • search              — run one search and print results
  • list-sources        — list registered scrapers with capabilities
  • company-search      — search the bundled company directory
  • company-live-batch  — resumable concurrent pass over career pages
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

# Register every scraper so the registry is populated for the CLI.
import job_harness.scrapers  # noqa: F401
import job_harness.scrapers.career  # noqa: F401
from job_harness.browser_pool import BrowserPool
from job_harness.company_directory import search_company_directory
from job_harness.countries import format_country_codes, normalize_country_code
from job_harness.experience_engine import parse_experience_levels_csv
from job_harness.formatters import get_formatter
from job_harness.registry import get_scraper_metadata
from job_harness.run_journal import RunJournalWriter, generate_run_id
from job_harness.search_engine import SearchEngine
from job_harness.types import SearchRequest

# ---------------------------------------------------------------------------
# search — full run end-to-end
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> None:
    if not args.query:
        print("Error: --query is required", file=sys.stderr)
        sys.exit(1)

    sources_tuple: tuple[str, ...] | None
    if args.sources in (None, "", "all"):
        sources_tuple = None
    else:
        sources_tuple = tuple(s.strip() for s in args.sources.split(",") if s.strip())

    try:
        experience_levels = parse_experience_levels_csv(args.experience_levels)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    request = SearchRequest(
        query=args.query,
        country=normalize_country_code(args.country),
        remote_only=args.remote_only,
        experience_levels=experience_levels,
        location=args.location,
        max_results=args.max_results,
        sources=sources_tuple,
        profile=args.profile,
        detail=args.detail,
        resolve=False,  # resolve phase is not yet implemented in the new engine
        cache=args.cache,
        exclude_keywords=tuple(
            k.strip()
            for k in (args.exclude_keywords or "").split(",")
            if k.strip()
        ),
        exclude_keywords_context=tuple(
            k.strip()
            for k in (args.exclude_keywords_context or "").split(",")
            if k.strip()
        ),
        exclude_companies=tuple(
            c.strip()
            for c in (args.exclude_companies or "").split(",")
            if c.strip()
        ),
        has_salary=args.has_salary,
        strict_flags=not args.lenient_flags,
        dedupe=args.dedupe,
        source_timeout_ms=args.source_timeout_ms,
        total_timeout_ms=args.total_timeout_ms,
    )

    async def _run() -> None:
        pool = BrowserPool(max_contexts=2)
        engine = SearchEngine(browser_pool=pool)
        with tempfile.TemporaryDirectory(prefix="job-harness-cli-") as tmp:
            run_dir = Path(tmp)
            run_id = generate_run_id()
            try:
                with RunJournalWriter(run_dir) as journal:
                    result = await engine.execute(
                        request, journal=journal, run_id=run_id,
                    )
            finally:
                engine.http_runner.shutdown()
                await pool.shutdown()

        formatter = get_formatter(args.format)
        output = formatter.format(result)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Output written to {args.output}", file=sys.stderr)
        else:
            print(output)

    try:
        asyncio.run(_run())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# list-sources
# ---------------------------------------------------------------------------


def cmd_list_sources(args: argparse.Namespace) -> None:
    info = get_scraper_metadata()
    if not info:
        print("No scrapers registered.")
        return
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return
    print("Available scrapers:")
    for name, metadata in info.items():
        countries = format_country_codes(metadata["countries"]) or "all"
        transport = metadata.get("transport", "?")
        print(f"  {name:18s} {metadata['display_name']:24s} [{countries}] ({transport})")


# ---------------------------------------------------------------------------
# company-search — bundled directory lookup, no scraping
# ---------------------------------------------------------------------------


def cmd_company_search(args: argparse.Namespace) -> None:
    companies = search_company_directory(
        query=args.query,
        country=args.country,
        stack=args.stack,
        job_type=args.job_type,
        industry=args.industry,
        remote_only=args.remote_only,
        max_results=args.max_results,
    )
    if args.format == "json":
        payload = [c.to_dict() for c in companies]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for c in companies:
        countries = ", ".join(c.countries) or "—"
        print(f"  {c.name:30s} {countries:24s} {c.careers_url or '—'}")


# ---------------------------------------------------------------------------
# company-live-batch — resumable concurrent career-page scan
# ---------------------------------------------------------------------------


def cmd_company_live_batch(args: argparse.Namespace) -> None:
    """Thin adapter around the existing async batch runner."""
    from job_harness.company_career_batch import run_company_career_batch

    asyncio.run(
        run_company_career_batch(
            query=args.query,
            output_jsonl=args.output_jsonl,
            summary_json=args.summary_json,
            country=args.country,
            stack=args.stack,
            job_type=args.job_type,
            industry=args.industry,
            remote_only=args.remote_only,
            max_companies=args.max_companies,
            workers=args.workers,
            timeout_ms=args.timeout_ms,
            headless=args.headless,
            progress=args.progress,
        )
    )


# ---------------------------------------------------------------------------
# CLI assembly
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(prog="job-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search
    s = subparsers.add_parser("search", help="Search for job listings via the engine")
    s.add_argument("--query", "-q", required=True, help="Search query")
    s.add_argument("--sources", "-s", default="all", help='Comma-separated scraper names, or "all"')
    s.add_argument("--profile", choices=["fast", "full"], help="Source profile")
    s.add_argument("--country", help="CIS country code or name (e.g. RU, KZ, Armenia)")
    s.add_argument("--remote-only", action="store_true", help="Only remote listings")
    s.add_argument(
        "--experience-levels",
        help="Comma-separated exact levels: junior,middle,senior",
    )
    s.add_argument("--location", help="Location filter string")
    s.add_argument("--max-results", type=int, default=20)
    s.add_argument("--detail", action="store_true", help="Fetch full details for each listing")
    s.add_argument("--exclude-keywords", help="Comma-separated keywords to exclude")
    s.add_argument("--exclude-keywords-context", help="Comma-separated context words that allow excluded keywords")
    s.add_argument("--exclude-companies", help="Comma-separated company names to exclude")
    s.add_argument("--has-salary", action="store_true", help="Only listings with salary info")
    s.add_argument("--lenient-flags", action="store_true",
                   help="Disable strict-flag policy (include sources whose capability is UNSUPPORTED)")
    s.add_argument("--source-timeout-ms", type=int, default=30_000)
    s.add_argument("--total-timeout-ms", type=int, default=90_000)
    s.add_argument("--dedupe", dest="dedupe", action="store_true", default=True)
    s.add_argument("--no-dedupe", dest="dedupe", action="store_false")
    s.add_argument("--cache", action="store_true", help="(reserved) Cache employer career page results")
    s.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    s.add_argument("--output", "-o", help="Output file path")
    s.set_defaults(func=cmd_search)

    # list-sources
    ls = subparsers.add_parser("list-sources", help="List registered scrapers")
    ls.add_argument("--json", action="store_true", help="Print metadata as JSON")
    ls.set_defaults(func=cmd_list_sources)

    # company-search
    cs = subparsers.add_parser("company-search", help="Search the bundled company directory")
    cs.add_argument("--query", "-q", default="")
    cs.add_argument("--country")
    cs.add_argument("--stack")
    cs.add_argument("--job-type")
    cs.add_argument("--industry")
    cs.add_argument("--remote-only", action="store_true")
    cs.add_argument("--max-results", type=int, default=20)
    cs.add_argument("--format", choices=["text", "json"], default="text")
    cs.set_defaults(func=cmd_company_search)

    # company-live-batch
    cb = subparsers.add_parser(
        "company-live-batch",
        help="Concurrent resumable scan of career pages (CLI-only)",
    )
    cb.add_argument("--query", "-q", required=True)
    cb.add_argument("--output-jsonl", required=True)
    cb.add_argument("--summary-json", required=True)
    cb.add_argument("--country")
    cb.add_argument("--stack")
    cb.add_argument("--job-type")
    cb.add_argument("--industry")
    cb.add_argument("--remote-only", action="store_true")
    cb.add_argument("--max-companies", type=int, default=None)
    cb.add_argument("--workers", type=int, default=12)
    cb.add_argument("--timeout-ms", type=int, default=8000)
    cb.add_argument("--headless", action="store_true", default=True)
    cb.add_argument("--no-headless", dest="headless", action="store_false")
    cb.add_argument("--progress", action="store_true")
    cb.set_defaults(func=cmd_company_live_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
