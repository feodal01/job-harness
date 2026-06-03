"""CLI entry point for job-harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure scrapers are imported so @register_scraper decorators fire
import job_harness.scrapers  # noqa: F401
import job_harness.scrapers.career  # noqa: F401
from job_harness.browser import configure_playwright_tmpdir, create_browser
from job_harness.company_directory import search_company_directory
from job_harness.countries import format_country_codes
from job_harness.employer_cache import EmployerCache
from job_harness.formatters import get_formatter
from job_harness.registry import get_scraper_metadata
from job_harness.search_runner import DEFAULT_SOURCE_TIMEOUT_MS, execute_search


def cmd_search(args: argparse.Namespace) -> None:
    if not args.query:
        print("Error: --query is required", file=sys.stderr)
        sys.exit(1)

    pw = None
    browser = None
    context = None

    def ensure_context():
        nonlocal pw, browser, context
        if context is None:
            configure_playwright_tmpdir()
            from rebrowser_playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser, context = create_browser(pw, headless=args.headless)
        return context

    try:
        results = execute_search(
            query=args.query,
            ensure_context=ensure_context,
            cache_factory=lambda: EmployerCache(),
            sources=args.sources,
            profile=args.profile,
            country=args.country,
            remote_only=args.remote_only,
            experience=args.experience,
            location=args.location,
            max_results=args.max_results,
            detail=args.detail,
            resolve=args.resolve,
            cache=args.cache,
            exclude_keywords=args.exclude_keywords,
            exclude_keywords_context=args.exclude_keywords_context,
            exclude_companies=args.exclude_companies,
            has_salary=args.has_salary,
            skip_slow=args.skip_slow,
            source_timeout_ms=args.source_timeout_ms,
            raw_jsonl=args.raw_jsonl,
            dedupe=args.dedupe,
            debug=args.debug,
            progress=lambda message: print(message, file=sys.stderr),
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()

    # Format output
    formatter = get_formatter(args.format)
    output = formatter.format(results)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


def cmd_list_sources(args: argparse.Namespace) -> None:
    info = get_scraper_metadata()
    if not info:
        print("No scrapers registered.")
        return
    print("Available scrapers:")
    for name, metadata in info.items():
        countries = format_country_codes(metadata["countries"]) or "all"
        print(f"  {name:20s} {metadata['display_name']} [{countries}]")


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
    data = [company.to_dict() for company in companies]

    if args.format == "json":
        output = json.dumps({"total": len(data), "companies": data}, ensure_ascii=False, indent=2)
    elif args.format == "csv":
        import csv
        import io

        fieldnames = [
            "name",
            "careers_url",
            "linkedin_url",
            "linkedin_jobs_url",
            "industry",
            "headcount",
            "remote",
            "job_types",
            "stack",
            "countries",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for company in data:
            row = company.copy()
            row["job_types"] = "; ".join(row["job_types"])
            row["stack"] = "; ".join(row["stack"])
            row["countries"] = "; ".join(row["countries"])
            writer.writerow(row)
        output = buffer.getvalue()
    else:
        lines = [f"Found {len(data)} companies"]
        for company in data:
            url = company["careers_url"] or company["linkedin_jobs_url"] or company["linkedin_url"] or ""
            countries = ", ".join(company["countries"][:6])
            lines.append(f"- {company['name']} — {url}")
            if countries:
                lines.append(f"  Countries: {countries}")
            if company["job_types"]:
                lines.append(f"  Hiring: {', '.join(company['job_types'][:8])}")
            if company["stack"]:
                lines.append(f"  Stack: {', '.join(company['stack'][:8])}")
        output = "\n".join(lines)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


def cmd_company_live_search(args: argparse.Namespace) -> None:
    from job_harness.company_career_search import search_company_careers

    configure_playwright_tmpdir()

    from rebrowser_playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, context = create_browser(pw, headless=args.headless)
        try:
            def progress(index, total, company, status):
                if args.progress:
                    print(f"[{index}/{total}] {status}: {company.name}", file=sys.stderr)

            result = search_company_careers(
                query=args.query,
                context=context,
                country=args.country,
                stack=args.stack,
                job_type=args.job_type,
                industry=args.industry,
                remote_only=args.remote_only,
                max_companies=args.max_companies,
                max_results=args.max_results,
                timeout_ms=args.timeout_ms,
                progress=progress,
            )
        finally:
            browser.close()

    data = result.to_dict()
    if args.format == "json":
        output = json.dumps(data, ensure_ascii=False, indent=2)
    elif args.format == "csv":
        import csv
        import io

        fieldnames = [
            "company",
            "title",
            "vacancy_url",
            "careers_url",
            "score",
            "countries",
            "stack",
            "job_types",
            "matched_text",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for hit in data["hits"]:
            row = hit.copy()
            row["countries"] = "; ".join(row["countries"])
            row["stack"] = "; ".join(row["stack"])
            row["job_types"] = "; ".join(row["job_types"])
            writer.writerow(row)
        output = buffer.getvalue()
    else:
        lines = [
            f"Checked {data['companies_checked']} of {data['companies_considered']} companies",
            f"Found {data['total']} matching vacancy links",
        ]
        for hit in data["hits"]:
            lines.append(f"- {hit['company']}: {hit['title']}")
            lines.append(f"  {hit['vacancy_url']}")
        if data["errors"]:
            lines.append(f"Errors: {len(data['errors'])}")
        output = "\n".join(lines)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


def cmd_company_live_batch(args: argparse.Namespace) -> None:
    import asyncio

    from job_harness.company_career_batch import run_company_career_batch

    summary = asyncio.run(
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
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def cmd_wide_search(args: argparse.Namespace) -> None:
    from job_harness.models import JobListing, SearchParams, SearchResults
    from job_harness.search_runner import dedupe_listings

    queries = list(args.query or [])
    if args.queries_file:
        queries.extend(
            line.strip()
            for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if not queries:
        print("Error: provide --query or --queries-file", file=sys.stderr)
        sys.exit(1)

    countries = [country.strip() or None for country in (args.countries or "").split(",")] if args.countries else [None]
    output_dir = Path(args.output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    pw = None
    browser = None
    context = None

    def ensure_context():
        nonlocal pw, browser, context
        if context is None:
            configure_playwright_tmpdir()
            from rebrowser_playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser, context = create_browser(pw, headless=args.headless)
        return context

    runs = []
    listings: list[JobListing] = []
    try:
        for query in queries:
            for country in countries:
                for profile in ("fast", "full"):
                    raw_path = raw_dir / f"{_slug(query)}_{country or 'all'}_{profile}.jsonl"
                    result = execute_search(
                        query=query,
                        ensure_context=ensure_context,
                        cache_factory=lambda: EmployerCache(),
                        sources="all",
                        profile=profile,
                        country=country,
                        remote_only=args.remote_only,
                        experience=args.experience,
                        max_results=args.max_results,
                        source_timeout_ms=args.source_timeout_ms,
                        raw_jsonl=raw_path,
                        dedupe=True,
                        progress=lambda message: print(message, file=sys.stderr),
                    )
                    listings.extend(result.listings)
                    runs.append({
                        "query": query,
                        "country": country,
                        "profile": profile,
                        "total": len(result.listings),
                        "summary": result.summary,
                    })
                if args.company_live:
                    listings.extend(_run_wide_company_live(args, query, country, raw_dir, runs))
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()

    merged = dedupe_listings(listings)[:args.max_results]
    result = SearchResults(
        params=SearchParams(query="; ".join(queries), country=args.countries, remote_only=args.remote_only, experience=args.experience, max_results=args.max_results),
        listings=merged,
        summary={
            "runs": runs,
            "dedupe": {
                "enabled": True,
                "before": len(listings),
                "after": len(merged),
                "removed": len(listings) - len(merged),
            },
        },
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(result.summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "report.md").write_text(get_formatter("markdown").format(result), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "total": len(merged), "runs": len(runs)}, ensure_ascii=False, indent=2))


def _run_wide_company_live(args: argparse.Namespace, query: str, country: str | None, raw_dir: Path, runs: list[dict]) -> list:
    import asyncio

    from job_harness.company_career_batch import run_company_career_batch
    from job_harness.models import JobListing

    jsonl_path = raw_dir / f"{_slug(query)}_{country or 'all'}_company-live.jsonl"
    summary_path = raw_dir / f"{_slug(query)}_{country or 'all'}_company-live-summary.json"
    summary = asyncio.run(
        run_company_career_batch(
            query=query,
            output_jsonl=jsonl_path,
            summary_json=summary_path,
            country=country,
            remote_only=args.remote_only,
            max_companies=args.company_max_companies,
            timeout_ms=args.company_timeout_ms,
            headless=args.headless,
            progress=args.progress,
        )
    )
    runs.append({"query": query, "country": country, "profile": "company-live", "total": summary["total"], "summary": summary})
    listings = []
    for hit in summary["hits"]:
        listings.append(
            JobListing(
                title=hit["title"],
                url=hit["vacancy_url"],
                company=hit["company"],
                country=", ".join(hit.get("countries", [])) or None,
                remote=hit.get("remote_match") is True,
                location=", ".join(hit.get("countries", [])) or None,
                skills=list(hit.get("stack", [])),
                source="company_live",
                raw=hit,
            )
        )
    return listings


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower() or "query"


def cmd_resolve(args: argparse.Namespace) -> None:
    """Resolve aggregator listings to direct employer career pages."""
    listings = []
    if args.input_file:
        with open(args.input_file, encoding="utf-8") as f:
            data = json.load(f)
        listings = data.get("listings", data) if isinstance(data, dict) else data
    elif args.urls:
        for url in args.urls:
            listings.append({"url": url, "company": "", "title": "", "source": ""})
    else:
        print("Error: provide --input-file or URLs", file=sys.stderr)
        sys.exit(1)

    query = args.query or ""

    from job_harness.employer_resolver import resolve_listings

    configure_playwright_tmpdir()

    from rebrowser_playwright.sync_api import sync_playwright

    cache = EmployerCache() if args.cache else None

    with sync_playwright() as pw:
        browser, context = create_browser(pw, headless=args.headless)
        try:
            enriched = resolve_listings(listings, context, query=query, cache=cache)
        finally:
            browser.close()

    # Output results
    results = []
    for e in enriched:
        entry: dict[str, str | None] = {
            "company": e.company,
            "title": e.title,
            "aggregator_url": e.original_url,
            "source": e.source,
            "best_url": e.best_url,
        }
        if e.careers_page:
            cp = e.careers_page
            entry["careers_url"] = cp.careers_url
            entry["careers_type"] = cp.page_type
            entry["direct_vacancy_url"] = cp.direct_vacancy_url
            if cp.error:
                entry["error"] = cp.error
        results.append(entry)

    output = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


def main() -> None:
    from job_harness.company_career_batch import DEFAULT_COMPANY_LIVE_WORKERS

    parser = argparse.ArgumentParser(
        prog="job-harness",
        description="Universal job search harness",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- search ---
    search_parser = subparsers.add_parser("search", help="Search for job listings")
    search_parser.add_argument("--query", "-q", help="Search query")
    search_parser.add_argument("--sources", "-s", default="all", help="Comma-separated scraper names (default: all)")
    search_parser.add_argument("--profile", choices=["fast", "full"], help="Source profile; fast skips slow browser sources and company_directory")
    search_parser.add_argument("--country", help="CIS country code or name, e.g. RU, KZ, Armenia")
    search_parser.add_argument("--remote-only", action="store_true", help="Only remote listings")
    search_parser.add_argument("--experience", choices=["junior", "middle", "senior"], help="Minimum experience level")
    search_parser.add_argument("--location", help="Location filter string")
    search_parser.add_argument("--max-results", type=int, default=20, help="Max results (default: 20)")
    search_parser.add_argument("--detail", action="store_true", help="Fetch full details for each listing")
    search_parser.add_argument("--exclude-keywords", help="Comma-separated keywords to exclude")
    search_parser.add_argument("--exclude-keywords-context", help="Comma-separated context words that allow excluded keywords")
    search_parser.add_argument("--exclude-companies", help="Comma-separated company names to exclude")
    search_parser.add_argument("--has-salary", action="store_true", help="Only listings with salary info")
    search_parser.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown", help="Output format")
    search_parser.add_argument("--output", "-o", help="Output file path")
    search_parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default)")
    search_parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")
    search_parser.add_argument("--debug", action="store_true", help="Save debug screenshots")
    search_parser.add_argument("--resolve", action="store_true", help="Resolve listings to direct employer career pages")
    search_parser.add_argument("--cache", action="store_true", help="Cache employer career page results for future searches")
    search_parser.add_argument("--skip-slow", action="store_true", help="Skip browser-backed slow sources")
    search_parser.add_argument("--source-timeout-ms", type=int, default=DEFAULT_SOURCE_TIMEOUT_MS, help=f"Per-source timeout in ms (default: {DEFAULT_SOURCE_TIMEOUT_MS})")
    search_parser.add_argument("--raw-jsonl", help="Incremental raw JSONL output path")
    search_parser.add_argument("--dedupe", dest="dedupe", action="store_true", default=True, help="Deduplicate results before applying max-results (default)")
    search_parser.add_argument("--no-dedupe", dest="dedupe", action="store_false", help="Disable built-in dedupe")

    # --- list-sources ---
    subparsers.add_parser("list-sources", help="List available scrapers")

    # --- company-search ---
    company_parser = subparsers.add_parser("company-search", help="Search bundled employer directory")
    company_parser.add_argument("--query", "-q", default="", help="Role, skill, company, or hiring profile query")
    company_parser.add_argument("--country", help="Country or city text from company hiring locations")
    company_parser.add_argument("--stack", help="Technology stack filter")
    company_parser.add_argument("--job-type", help="Hiring function filter, e.g. QA, Developers, Sales")
    company_parser.add_argument("--industry", help="Industry filter")
    company_parser.add_argument("--remote-only", action="store_true", help="Only remote-friendly companies")
    company_parser.add_argument("--max-results", type=int, default=20, help="Max companies (default: 20)")
    company_parser.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown", help="Output format")
    company_parser.add_argument("--output", "-o", help="Output file path")

    # --- company-live-search ---
    live_parser = subparsers.add_parser("company-live-search", help="Search live career pages from bundled employers")
    live_parser.add_argument("--query", "-q", required=True, help="Role query to match on career pages")
    live_parser.add_argument("--country", help="Country or city text from company hiring locations")
    live_parser.add_argument("--stack", help="Known company technology stack filter")
    live_parser.add_argument("--job-type", help="Known hiring function filter, e.g. QA, Developers, Sales")
    live_parser.add_argument("--industry", help="Industry filter")
    live_parser.add_argument("--remote-only", action="store_true", help="Only remote-friendly companies")
    live_parser.add_argument("--max-companies", type=int, help="Maximum companies to check; default checks all matching companies")
    live_parser.add_argument("--max-results", type=int, default=20, help="Max vacancy links to return (default: 20)")
    live_parser.add_argument("--timeout-ms", type=int, default=8000, help="Per-company page timeout in ms (default: 8000)")
    live_parser.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown", help="Output format")
    live_parser.add_argument("--output", "-o", help="Output file path")
    live_parser.add_argument("--progress", action="store_true", help="Print per-company progress to stderr")
    live_parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default)")
    live_parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")

    # --- company-live-batch ---
    batch_parser = subparsers.add_parser(
        "company-live-batch",
        help="Concurrent resumable career-page search over bundled and cached employers",
    )
    batch_parser.add_argument("--query", "-q", required=True, help="Role query to match on career pages")
    batch_parser.add_argument("--output-jsonl", required=True, help="Incremental JSONL output path, one record per company")
    batch_parser.add_argument("--summary-json", required=True, help="Summary JSON output path")
    batch_parser.add_argument("--country", help="Country or city text from company hiring locations")
    batch_parser.add_argument("--stack", help="Known company technology stack filter")
    batch_parser.add_argument("--job-type", help="Known hiring function filter, e.g. QA, Developers, Sales")
    batch_parser.add_argument("--industry", help="Industry filter")
    batch_parser.add_argument("--remote-only", action="store_true", help="Only remote-friendly companies")
    batch_parser.add_argument("--max-companies", type=int, help="Maximum companies to check; default checks all matching companies")
    batch_parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_COMPANY_LIVE_WORKERS,
        help=(
            "Advanced override for concurrent company checks "
            f"(default: {DEFAULT_COMPANY_LIVE_WORKERS}; omit for normal full-scale runs)"
        ),
    )
    batch_parser.add_argument("--timeout-ms", type=int, default=8000, help="Per-company page timeout in ms (default: 8000)")
    batch_parser.add_argument("--progress", action="store_true", help="Print per-company progress to stderr")
    batch_parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default)")
    batch_parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")

    # --- wide-search ---
    wide_parser = subparsers.add_parser("wide-search", help="Run fast/full multi-query search and write report artifacts")
    wide_parser.add_argument("--query", "-q", action="append", help="Role query; repeat for variants")
    wide_parser.add_argument("--queries-file", help="File with one query variant per line")
    wide_parser.add_argument("--countries", help="Comma-separated country codes/names")
    wide_parser.add_argument("--remote-only", action="store_true", help="Only remote listings when listing-level evidence allows it")
    wide_parser.add_argument("--experience", choices=["junior", "middle", "senior"], help="Minimum experience level")
    wide_parser.add_argument("--max-results", type=int, default=100, help="Max merged results (default: 100)")
    wide_parser.add_argument("--source-timeout-ms", type=int, default=DEFAULT_SOURCE_TIMEOUT_MS, help=f"Per-source timeout in ms (default: {DEFAULT_SOURCE_TIMEOUT_MS})")
    wide_parser.add_argument("--output-dir", required=True, help="Directory for results.json, summary.json, report.md, and raw files")
    wide_parser.add_argument("--company-live", action="store_true", help="Also run company-live-batch")
    wide_parser.add_argument("--company-max-companies", type=int, help="Maximum companies for company-live-batch")
    wide_parser.add_argument("--company-timeout-ms", type=int, default=8000, help="Per-company timeout in ms for company-live-batch")
    wide_parser.add_argument("--progress", action="store_true", help="Print company-live progress")
    wide_parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default)")
    wide_parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")

    # --- resolve ---
    resolve_parser = subparsers.add_parser("resolve", help="Resolve aggregator listings to employer career pages")
    resolve_parser.add_argument("--input-file", "-i", help="JSON file with search results")
    resolve_parser.add_argument("--query", "-q", help="Original search query (improves vacancy matching)")
    resolve_parser.add_argument("--output", "-o", help="Output file path")
    resolve_parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default)")
    resolve_parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")
    resolve_parser.add_argument("--cache", action="store_true", help="Cache employer career page results for future searches")
    resolve_parser.add_argument("urls", nargs="*", help="Aggregator URLs to resolve")

    args = parser.parse_args()
    if args.command == "search":
        cmd_search(args)
    elif args.command == "list-sources":
        cmd_list_sources(args)
    elif args.command == "company-search":
        cmd_company_search(args)
    elif args.command == "company-live-search":
        cmd_company_live_search(args)
    elif args.command == "company-live-batch":
        cmd_company_live_batch(args)
    elif args.command == "wide-search":
        cmd_wide_search(args)
    elif args.command == "resolve":
        cmd_resolve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
