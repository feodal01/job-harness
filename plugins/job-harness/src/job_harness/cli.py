"""CLI entry point for job-harness."""

from __future__ import annotations

import argparse
import json
import sys

from job_harness.browser import configure_playwright_tmpdir, create_browser
from job_harness.employer_cache import EmployerCache
from job_harness.filters import apply_filters, has_salary, location_in, min_experience, no_keywords, remote_only
from job_harness.formatters import get_formatter
from job_harness.models import SearchParams
from job_harness.registry import create_scraper, get_scraper_class, get_scraper_info, list_scrapers

# Ensure scrapers are imported so @register_scraper decorators fire
import job_harness.scrapers  # noqa: F401
import job_harness.scrapers.career  # noqa: F401


def cmd_search(args: argparse.Namespace) -> None:
    if not args.query:
        print("Error: --query is required", file=sys.stderr)
        sys.exit(1)

    params = SearchParams(
        query=args.query,
        remote_only=args.remote_only,
        experience=args.experience,
        location=args.location,
        max_results=args.max_results,
    )

    # Determine sources
    if args.sources == "all" or args.sources is None:
        sources = list_scrapers()
    else:
        sources = [s.strip() for s in args.sources.split(",")]

    # Build filters
    filters = []
    if params.remote_only:
        filters.append(remote_only)
    if args.has_salary:
        filters.append(has_salary)
    if args.exclude_companies:
        from job_harness.filters import _exclude_companies
        filters.append(_exclude_companies(args.exclude_companies.split(",")))
    if params.experience:
        filters.append(min_experience(params.experience))

    if args.exclude_keywords:
        keywords = [k.strip() for k in args.exclude_keywords.split(",")]
        ignore_words = [w.strip() for w in args.exclude_keywords_context.split(",")] if args.exclude_keywords_context else None
        filters.append(no_keywords(*keywords, ignore_context=ignore_words))

    if args.location:
        filters.append(location_in(args.location))

    all_listings = []
    errors = []
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
        for source_name in sources:
            try:
                scraper_class = get_scraper_class(source_name)
                needs_browser = scraper_class.requires_browser or (
                    args.detail and scraper_class.detail_requires_browser
                )
                scraper_context = ensure_context() if needs_browser else None
                scraper = create_scraper(source_name, scraper_context, max_results=params.max_results, debug=args.debug)
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

        # Apply filters
        if filters:
            before = len(all_listings)
            all_listings = apply_filters(all_listings, filters)
            print(f"Filters removed {before - len(all_listings)} listings, {len(all_listings)} remaining", file=sys.stderr)

        all_listings = all_listings[:params.max_results]

        # Resolve employer career pages if requested
        if args.resolve and all_listings:
            from job_harness.employer_resolver import resolve_listings
            cache = EmployerCache() if args.cache else None
            if cache:
                print(f"Using employer cache ({len(cache.all_entries())} entries)", file=sys.stderr)
            print("Resolving employer career pages...", file=sys.stderr)
            enriched = resolve_listings(
                [l.to_dict() for l in all_listings],
                ensure_context(),
                query=params.query,
                cache=cache,
            )
            for listing, enrich in zip(all_listings, enriched):
                if enrich.careers_page:
                    cp = enrich.careers_page
                    listing.raw["careers_url"] = cp.careers_url
                    listing.raw["careers_type"] = cp.page_type
                    listing.raw["direct_vacancy_url"] = cp.direct_vacancy_url
                    if cp.direct_vacancy_url:
                        listing.url = cp.direct_vacancy_url
                        listing.source = f"{listing.source}+direct"
                    elif cp.careers_url:
                        listing.raw["employer_careers"] = cp.careers_url
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()

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
        entry = {
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
    search_parser.add_argument("--exclude-companies", help="Comma-separated company names to exclude")
    search_parser.add_argument("--has-salary", action="store_true", help="Only listings with salary info")
    search_parser.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown", help="Output format")
    search_parser.add_argument("--output", "-o", help="Output file path")
    search_parser.add_argument("--headless", action="store_true", default=True, help="Run browser headless (default)")
    search_parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")
    search_parser.add_argument("--debug", action="store_true", help="Save debug screenshots")
    search_parser.add_argument("--resolve", action="store_true", help="Resolve listings to direct employer career pages")
    search_parser.add_argument("--cache", action="store_true", help="Cache employer career page results for future searches")

    # --- list-sources ---
    subparsers.add_parser("list-sources", help="List available scrapers")

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
    elif args.command == "resolve":
        cmd_resolve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
