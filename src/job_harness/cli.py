"""CLI entry point for job-harness."""

from __future__ import annotations

import argparse
import sys

import yaml

from job_harness.browser import create_browser
from job_harness.filters import apply_filters, has_salary, location_in, min_experience, no_keywords, remote_only
from job_harness.formatters import get_formatter
from job_harness.models import SearchParams
from job_harness.registry import create_scraper, get_scraper_info, list_scrapers

# Ensure scrapers are imported so @register_scraper decorators fire
import job_harness.scrapers  # noqa: F401


def cmd_search(args: argparse.Namespace) -> None:
    # Load preset if specified
    preset = {}
    if args.preset:
        with open(args.preset) as f:
            preset = yaml.safe_load(f) or {}

    query = preset.get("query", args.query)
    if not query:
        print("Error: --query is required (or use --preset with a query field)", file=sys.stderr)
        sys.exit(1)

    params = SearchParams(
        query=query,
        remote_only=preset.get("remote_only", args.remote_only),
        experience=preset.get("experience", args.experience),
        location=preset.get("location", args.location),
        max_results=preset.get("max_results", args.max_results),
        extra=preset.get("extra", {}),
    )

    # Determine sources
    sources_arg = preset.get("sources", args.sources)
    if sources_arg == "all" or sources_arg is None:
        sources = list_scrapers()
    else:
        sources = [s.strip() for s in sources_arg.split(",")]

    # Build filters
    filters = []
    if params.remote_only:
        filters.append(remote_only)
    if preset.get("has_salary") or args.has_salary:
        filters.append(has_salary)
    if params.experience:
        filters.append(min_experience(params.experience))

    exclude_kw = preset.get("exclude_keywords", None)
    if args.exclude_keywords:
        exclude_kw = args.exclude_keywords
    if exclude_kw:
        keywords = [k.strip() for k in exclude_kw.split(",")]
        ignore_ctx = preset.get("exclude_keywords_context", None)
        if args.exclude_keywords_context:
            ignore_ctx = args.exclude_keywords_context
        ignore_words = [w.strip() for w in ignore_ctx.split(",")] if ignore_ctx else None
        filters.append(no_keywords(*keywords, ignore_context=ignore_words))

    loc_filter = preset.get("location_filter", None)
    if loc_filter:
        filters.append(location_in(*loc_filter if isinstance(loc_filter, list) else [loc_filter]))

    from rebrowser_playwright.sync_api import sync_playwright

    all_listings = []
    errors = []

    with sync_playwright() as pw:
        browser, context = create_browser(pw, headless=args.headless)
        try:
            for source_name in sources:
                try:
                    scraper = create_scraper(source_name, context, max_results=params.max_results, debug=args.debug)
                    print(f"Searching {scraper.display_name}...", file=sys.stderr)
                    listings = scraper.search(params)
                    print(f"  Found {len(listings)} listings", file=sys.stderr)

                    if args.detail and listings:
                        print(f"  Fetching details for {len(listings)} listings...", file=sys.stderr)
                        detailed = []
                        for listing in listings:
                            try:
                                detailed.append(scraper.fetch_detail(listing))
                            except Exception as e:
                                errors.append(f"{source_name}: detail error for {listing.url}: {e}")
                                detailed.append(listing)
                        listings = detailed

                    all_listings.extend(listings)
                except Exception as e:
                    errors.append(f"{source_name}: {e}")
                    print(f"  Error: {e}", file=sys.stderr)
        finally:
            browser.close()

    # Apply filters
    if filters:
        before = len(all_listings)
        all_listings = apply_filters(all_listings, filters)
        print(f"Filters removed {before - len(all_listings)} listings, {len(all_listings)} remaining", file=sys.stderr)

    all_listings = all_listings[:params.max_results]

    from job_harness.models import SearchResults

    results = SearchResults(params=params, listings=all_listings, errors=errors)

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
    info = get_scraper_info()
    if not info:
        print("No scrapers registered.")
        return
    print("Available scrapers:")
    for name, display in info.items():
        print(f"  {name:20s} {display}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="job-harness",
        description="Universal job search harness",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- search ---
    search_parser = subparsers.add_parser("search", help="Search for job listings")
    search_parser.add_argument("--query", "-q", help="Search query")
    search_parser.add_argument("--sources", "-s", default="all", help="Comma-separated scraper names (default: all)")
    search_parser.add_argument("--remote-only", action="store_true", help="Only remote listings")
    search_parser.add_argument("--experience", choices=["junior", "middle", "senior"], help="Minimum experience level")
    search_parser.add_argument("--location", help="Location filter string")
    search_parser.add_argument("--max-results", type=int, default=20, help="Max results (default: 20)")
    search_parser.add_argument("--detail", action="store_true", help="Fetch full details for each listing")
    search_parser.add_argument("--exclude-keywords", help="Comma-separated keywords to exclude")
    search_parser.add_argument("--exclude-keywords-context", help="Comma-separated context words that allow excluded keywords")
    search_parser.add_argument("--has-salary", action="store_true", help="Only listings with salary info")
    search_parser.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown", help="Output format")
    search_parser.add_argument("--output", "-o", help="Output file path")
    search_parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default)")
    search_parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")
    search_parser.add_argument("--debug", action="store_true", help="Save debug screenshots")
    search_parser.add_argument("--preset", help="Path to YAML preset config")

    # --- list-sources ---
    subparsers.add_parser("list-sources", help="List available scrapers")

    args = parser.parse_args()
    if args.command == "search":
        cmd_search(args)
    elif args.command == "list-sources":
        cmd_list_sources(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
