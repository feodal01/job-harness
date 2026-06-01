"""FastMCP server exposing job-harness tools to Claude Code."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("job-harness")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(os.environ.get("JOB_HARNESS_ROOT", Path(__file__).resolve().parent.parent))
_LOCAL_CACHE = _PLUGIN_ROOT / "data" / "company-careers.json"
_PUBLIC_CACHE = _PLUGIN_ROOT / "data" / "company-careers-public.json"

# ---------------------------------------------------------------------------
# Browser state (lazy-initialised, lives for the session)
# ---------------------------------------------------------------------------

_browser_state: dict = {}


def _ensure_browser():
    """Return a Playwright BrowserContext, starting the browser if needed."""
    if _browser_state.get("context") is None:
        from rebrowser_playwright.sync_api import sync_playwright
        from job_harness.browser import create_browser

        pw = sync_playwright().start()
        browser, context = create_browser(pw, headless=True)
        _browser_state["pw"] = pw
        _browser_state["browser"] = browser
        _browser_state["context"] = context
    return _browser_state["context"]


def _get_cache():
    """Return an EmployerCache instance pointed at the plugin data dir."""
    from job_harness.employer_cache import EmployerCache

    return EmployerCache(path=_LOCAL_CACHE, public_path=_PUBLIC_CACHE)


# ---------------------------------------------------------------------------
# Tools — no browser required
# ---------------------------------------------------------------------------


@mcp.tool
def list_sources() -> dict[str, str]:
    """List available job board scrapers and their display names."""
    import job_harness.scrapers  # noqa: F401
    import job_harness.scrapers.career  # noqa: F401
    from job_harness.registry import get_scraper_info

    return get_scraper_info()


@mcp.tool
def cache_get(company: str) -> dict | None:
    """Get the cache entry for a company. Returns null if not cached.

    Args:
        company: Company name to look up
    """
    cache = _get_cache()
    entry = cache.get(company)
    return asdict(entry) if entry else None


@mcp.tool
def cache_upsert(
    company: str,
    careers_url: str | None = None,
    ats_type: str = "unknown",
    scraper_name: str | None = None,
    last_found_roles: bool = False,
    ignored: bool = False,
) -> dict:
    """Insert or update a local cache entry.

    Args:
        company: Company name
        careers_url: URL of the company career page, or null
        ats_type: ATS classification (direct, greenhouse, lever, workday, huntflow, unknown)
        scraper_name: Name of the per-company career scraper, if any
        last_found_roles: Whether matching roles were found on the career page
        ignored: Whether to skip this company in future resolution
    """
    from job_harness.employer_cache import CompanyEntry

    cache = _get_cache()
    entry = CompanyEntry(
        company=company,
        careers_url=careers_url,
        ats_type=ats_type,
        scraper_name=scraper_name,
        last_found_roles=last_found_roles,
        ignored=ignored,
    )
    cache.upsert(entry)
    cache.save()
    return asdict(entry)


@mcp.tool
def cache_stats() -> dict:
    """Return cache statistics: total entries, with careers_url, fresh, ignored."""
    cache = _get_cache()
    entries = cache.all_entries()
    return {
        "total": len(entries),
        "with_careers_url": sum(1 for e in entries if e.careers_url),
        "fresh": sum(1 for e in entries if e.is_fresh()),
        "ignored": sum(1 for e in entries if e.ignored),
    }


# ---------------------------------------------------------------------------
# Tools — browser required
# ---------------------------------------------------------------------------


@mcp.tool
def search(
    query: str,
    sources: str = "all",
    remote_only: bool = False,
    experience: str | None = None,
    location: str | None = None,
    max_results: int = 20,
    detail: bool = False,
    resolve: bool = False,
    cache: bool = True,
    exclude_keywords: str | None = None,
    exclude_keywords_context: str | None = None,
    exclude_companies: str | None = None,
    has_salary: bool = False,
) -> dict:
    """Search job aggregators for listings matching the query.

    Returns search results as a dictionary with 'params', 'listings',
    'total', 'timestamp', and 'errors' keys.

    Args:
        query: Search query (e.g. "QA engineer", "Python backend")
        sources: Comma-separated scraper names, or "all"
        remote_only: Only remote listings
        experience: Minimum experience level (junior/middle/senior)
        location: Location filter string
        max_results: Maximum number of results
        detail: Fetch full details for each listing
        resolve: Resolve listings to direct employer career pages
        cache: Use employer cache for resolution
        exclude_keywords: Comma-separated keywords to exclude
        exclude_keywords_context: Context words allowing excluded keywords
        exclude_companies: Comma-separated company names to exclude
        has_salary: Only listings with salary info
    """
    import job_harness.scrapers  # noqa: F401
    import job_harness.scrapers.career  # noqa: F401
    from job_harness.browser import create_browser
    from job_harness.employer_cache import EmployerCache
    from job_harness.employer_resolver import resolve_listings
    from job_harness.filters import (
        _exclude_companies,
        apply_filters,
        has_salary as has_salary_filter,
        location_in,
        min_experience,
        no_keywords,
        remote_only as remote_only_filter,
    )
    from job_harness.models import SearchParams, SearchResults
    from job_harness.registry import create_scraper, list_scrapers

    params = SearchParams(
        query=query,
        remote_only=remote_only,
        experience=experience,
        location=location,
        max_results=max_results,
    )

    # Determine sources
    if sources == "all" or not sources:
        source_names = list_scrapers()
    else:
        source_names = [s.strip() for s in sources.split(",")]

    # Build filters
    filters = []
    if remote_only:
        filters.append(remote_only_filter)
    if has_salary:
        filters.append(has_salary_filter)
    if exclude_companies:
        filters.append(_exclude_companies([c.strip() for c in exclude_companies.split(",")]))
    if experience:
        filters.append(min_experience(experience))
    if exclude_keywords:
        keywords = [k.strip() for k in exclude_keywords.split(",")]
        ignore_words = (
            [w.strip() for w in exclude_keywords_context.split(",")]
            if exclude_keywords_context
            else None
        )
        filters.append(no_keywords(*keywords, ignore_context=ignore_words))
    if location:
        filters.append(location_in(location))

    context = _ensure_browser()
    all_listings = []
    errors = []

    for source_name in source_names:
        try:
            scraper = create_scraper(source_name, context, max_results=params.max_results)
            listings = scraper.search(params)

            if detail and listings:
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

    # Apply filters
    if filters:
        before = len(all_listings)
        all_listings = apply_filters(all_listings, filters)

    all_listings = all_listings[: params.max_results]

    # Resolve employer career pages
    if resolve and all_listings:
        employer_cache = _get_cache() if cache else None
        enriched = resolve_listings(
            [l.to_dict() for l in all_listings],
            context,
            query=params.query,
            cache=employer_cache,
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

    results = SearchResults(params=params, listings=all_listings, errors=errors)
    data = results.to_dict()
    data["total"] = len(all_listings)
    return data


@mcp.tool
def resolve(
    listings: list[dict],
    query: str | None = None,
    cache: bool = True,
) -> list[dict]:
    """Resolve a batch of job listings to direct employer career pages.

    Each listing dict should have at least 'company', 'url', 'title', and 'source'.

    Args:
        listings: List of listing dicts to resolve
        query: Original search query (improves vacancy matching)
        cache: Use employer cache for resolution
    """
    from job_harness.employer_resolver import resolve_listings

    context = _ensure_browser()
    employer_cache = _get_cache() if cache else None
    enriched = resolve_listings(listings, context, query=query, cache=employer_cache)

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
    return results


@mcp.tool
def resolve_company(
    company: str,
    query: str | None = None,
    cache: bool = True,
) -> dict:
    """Resolve a single company to its career page and optionally find a matching vacancy.

    Args:
        company: Company name to resolve
        query: Search query for vacancy matching (e.g. "QA engineer")
        cache: Use employer cache
    """
    from job_harness.employer_resolver import resolve_company_careers

    context = _ensure_browser()
    employer_cache = _get_cache() if cache else None
    result = resolve_company_careers(company, context, query=query, cache=employer_cache)
    return asdict(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
