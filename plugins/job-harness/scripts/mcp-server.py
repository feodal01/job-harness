"""FastMCP server exposing job-harness tools to Claude Code."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import TypeVar

from fastmcp import FastMCP

mcp = FastMCP("job-harness")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(os.environ.get("JOB_HARNESS_ROOT", Path(__file__).resolve().parent.parent))
_LOCAL_CACHE = _PLUGIN_ROOT / "data" / "company-careers.json"
_PUBLIC_CACHE = _PLUGIN_ROOT / "data" / "company-careers-public.json"
_COMPANY_DIRECTORY = _PLUGIN_ROOT / "data" / "company-directory.json"

# ---------------------------------------------------------------------------
# Browser state (lazy-initialised, lives for the session)
# ---------------------------------------------------------------------------

_browser_state: dict = {}
_browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-harness-browser")
_T = TypeVar("_T")


async def _run_in_browser_thread(func: Callable[..., _T], *args, **kwargs) -> _T:
    """Run sync Playwright code away from FastMCP's asyncio event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_browser_executor, partial(func, *args, **kwargs))


def _ensure_browser():
    """Return a Playwright BrowserContext, starting the browser if needed."""
    if _browser_state.get("context") is None:
        from job_harness.browser import configure_playwright_tmpdir, create_browser

        configure_playwright_tmpdir(_PLUGIN_ROOT / "data" / ".tmp")

        from rebrowser_playwright.sync_api import sync_playwright

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
def list_sources() -> dict[str, dict]:
    """List available job board scrapers and their display names."""
    import job_harness.scrapers  # noqa: F401
    import job_harness.scrapers.career  # noqa: F401
    from job_harness.registry import get_scraper_metadata

    return get_scraper_metadata()


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


@mcp.tool
def search_company_jobs(
    query: str,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_results: int = 20,
) -> dict:
    """Search the bundled company directory for employer career entrypoints.

    This tool returns companies whose known hiring profile matches the query,
    plus their career page and LinkedIn jobs links when available. It does not
    claim that a specific vacancy is currently open.

    Args:
        query: Role, skill, company, or hiring profile query, e.g. "QA", "Python backend"
        country: Optional country/city text from the company hiring locations
        stack: Optional technology stack filter
        job_type: Optional hiring function filter, e.g. "QA", "Developers", "Sales"
        industry: Optional industry filter
        remote_only: Only companies marked as remote-friendly in the directory
        max_results: Maximum number of companies to return
    """
    from job_harness.company_directory import search_company_directory

    companies = search_company_directory(
        query=query,
        country=country,
        stack=stack,
        job_type=job_type,
        industry=industry,
        remote_only=remote_only,
        max_results=max_results,
        path=_COMPANY_DIRECTORY,
    )
    return {
        "query": query,
        "total": len(companies),
        "companies": [company.to_dict() for company in companies],
    }


# ---------------------------------------------------------------------------
# Tools — browser required
# ---------------------------------------------------------------------------


@mcp.tool
async def search_company_careers(
    query: str,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_companies: int | None = None,
    max_results: int = 20,
    timeout_ms: int = 8000,
) -> dict:
    """Search live career pages from the bundled company directory.

    This performs a browser-based pass over company career URLs and returns
    vacancy links whose text or URL matches the role query. Use this MCP tool
    for targeted checks. For a full bundled/cache-backed company pass, run
    the CLI `job-harness company-live-batch` command so results are
    concurrent, resumable, and written incrementally.

    Args:
        query: Role query to match on career pages, e.g. "Python developer"
        country: Optional company hiring-location filter
        stack: Optional known company stack filter
        job_type: Optional known hiring function filter
        industry: Optional industry filter
        remote_only: Only companies marked as remote-friendly in the directory
        max_companies: Maximum companies to check; null means all matching companies
        max_results: Maximum vacancy links to return
        timeout_ms: Per-company page navigation timeout in milliseconds
    """
    return await _run_in_browser_thread(
        _search_company_careers_impl,
        query=query,
        country=country,
        stack=stack,
        job_type=job_type,
        industry=industry,
        remote_only=remote_only,
        max_companies=max_companies,
        max_results=max_results,
        timeout_ms=timeout_ms,
    )


def _search_company_careers_impl(
    query: str,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_companies: int | None = None,
    max_results: int = 20,
    timeout_ms: int = 8000,
) -> dict:
    from job_harness.company_career_search import search_company_careers

    context = _ensure_browser()
    result = search_company_careers(
        query=query,
        context=context,
        country=country,
        stack=stack,
        job_type=job_type,
        industry=industry,
        remote_only=remote_only,
        max_companies=max_companies,
        max_results=max_results,
        timeout_ms=timeout_ms,
        directory_path=_COMPANY_DIRECTORY,
    )
    return result.to_dict()


@mcp.tool
async def search(
    query: str,
    sources: str = "all",
    country: str | None = None,
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
        country: CIS country code or name, e.g. RU, KZ, Armenia
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
    return await _run_in_browser_thread(
        _search_impl,
        query=query,
        sources=sources,
        country=country,
        remote_only=remote_only,
        experience=experience,
        location=location,
        max_results=max_results,
        detail=detail,
        resolve=resolve,
        cache=cache,
        exclude_keywords=exclude_keywords,
        exclude_keywords_context=exclude_keywords_context,
        exclude_companies=exclude_companies,
        has_salary=has_salary,
    )


def _search_impl(
    query: str,
    sources: str = "all",
    country: str | None = None,
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
    import job_harness.scrapers  # noqa: F401
    import job_harness.scrapers.career  # noqa: F401
    from job_harness.countries import format_country_codes, normalize_country_code
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
    from job_harness.models import JobListing, SearchParams, SearchResults
    from job_harness.registry import create_scraper, get_scraper_class, list_scrapers

    country_code = normalize_country_code(country)
    params = SearchParams(
        query=query,
        country=country_code,
        remote_only=remote_only,
        experience=experience,
        location=location,
        max_results=max_results,
    )

    # Determine sources
    if sources == "all" or not sources:
        source_names = list_scrapers(country=country_code)
    else:
        source_names = [s.strip() for s in sources.split(",")]
        for source_name in source_names:
            scraper_class = get_scraper_class(source_name)
            if not scraper_class.supports_country(country_code):
                raise ValueError(
                    f"{source_name} does not support country {country_code}. "
                    f"Supported countries: {format_country_codes(scraper_class.countries)}"
                )

    # Build filters
    filters: list[Callable[[JobListing], bool]] = []
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

    context = None
    all_listings = []
    errors = []

    for source_name in source_names:
        try:
            scraper_class = get_scraper_class(source_name)
            needs_browser = scraper_class.requires_browser or (
                detail and scraper_class.detail_requires_browser
            )
            scraper_context = _ensure_browser() if needs_browser else None
            if scraper_context is not None:
                context = scraper_context
            scraper = create_scraper(source_name, scraper_context, max_results=params.max_results)
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
        all_listings = apply_filters(all_listings, filters)

    all_listings = all_listings[: params.max_results]

    # Resolve employer career pages
    if resolve and all_listings:
        context = context or _ensure_browser()
        employer_cache = _get_cache() if cache else None
        enriched = resolve_listings(
            [listing.to_dict() for listing in all_listings],
            context,
            query=params.query,
            cache=employer_cache,
        )
        for listing, enrich in zip(all_listings, enriched, strict=False):
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
async def resolve(
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
    return await _run_in_browser_thread(_resolve_impl, listings=listings, query=query, cache=cache)


def _resolve_impl(
    listings: list[dict],
    query: str | None = None,
    cache: bool = True,
) -> list[dict]:
    from job_harness.employer_resolver import resolve_listings

    context = _ensure_browser()
    employer_cache = _get_cache() if cache else None
    enriched = resolve_listings(listings, context, query=query, cache=employer_cache)

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
    return results


@mcp.tool
async def resolve_company(
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
    return await _run_in_browser_thread(
        _resolve_company_impl,
        company=company,
        query=query,
        cache=cache,
    )


def _resolve_company_impl(
    company: str,
    query: str | None = None,
    cache: bool = True,
) -> dict:
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
