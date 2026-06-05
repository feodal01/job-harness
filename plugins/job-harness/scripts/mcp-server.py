"""FastMCP server exposing job-harness tools to Claude Code."""

from __future__ import annotations

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import TypeVar

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# TMPDIR safety — ensure Playwright can always create temp files
# ---------------------------------------------------------------------------

_tmpdir = os.environ.get(
    "JOB_HARNESS_TMPDIR",
    os.path.expanduser("~/.cache/job-harness/tmp"),
)
os.makedirs(_tmpdir, exist_ok=True)
os.environ.setdefault("TMPDIR", _tmpdir)
os.environ.setdefault("TEMP", _tmpdir)
os.environ.setdefault("TMP", _tmpdir)
tempfile.tempdir = _tmpdir

mcp = FastMCP("job-harness")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(os.environ.get("JOB_HARNESS_ROOT", Path(__file__).resolve().parent.parent))
_LOCAL_CACHE = _PLUGIN_ROOT / "data" / "company-careers.json"
_PUBLIC_CACHE = _PLUGIN_ROOT / "data" / "company-careers-public.json"
_COMPANY_DIRECTORY = _PLUGIN_ROOT / "data" / "company-directory.json"

# ---------------------------------------------------------------------------
# Browser state — lazy-initialised sync Playwright running in a dedicated
# thread, with asyncio.Lock for serialisation and graceful error recovery.
# ---------------------------------------------------------------------------

_browser_state: dict = {}
_browser_lock = asyncio.Lock()
_browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job-harness-browser")
_T = TypeVar("_T")


def _ensure_browser():
    """Return a sync Playwright BrowserContext, starting the browser if needed.

    Must be called from the browser executor thread so that all Playwright
    objects live on the same thread.
    """
    if _browser_state.get("context") is not None:
        return _browser_state["context"]

    from job_harness.browser import configure_playwright_tmpdir, create_browser

    configure_playwright_tmpdir(_PLUGIN_ROOT / "data" / ".tmp")

    from rebrowser_playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser, context = create_browser(pw, headless=True)
    _browser_state["pw"] = pw
    _browser_state["browser"] = browser
    _browser_state["context"] = context
    return context


def _reset_browser():
    """Close and discard the current browser, allowing a fresh start."""
    browser = _browser_state.pop("browser", None)
    pw = _browser_state.pop("pw", None)
    _browser_state.pop("context", None)
    if browser:
        try:
            browser.close()
        except Exception:
            pass
    if pw:
        try:
            pw.stop()
        except Exception:
            pass


async def _with_browser(func, *args, **kwargs):
    """Run *func* in the browser thread under the async lock.

    Serialises concurrent MCP tool calls so that only one uses the browser
    at a time.  On failure the browser is torn down so the next call starts
    fresh instead of hanging permanently.
    """
    async with _browser_lock:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                _browser_executor,
                partial(_browser_guard, func, *args, **kwargs),
            )
        except Exception:
            await asyncio.to_thread(_reset_browser)
            raise


def _browser_guard(func, *args, **kwargs):
    """Wrapper that runs in the browser executor thread.

    Recreates the browser on init failure, and tears it down on any
    error so the next call gets a fresh instance.
    """
    try:
        _ensure_browser()
    except Exception:
        _reset_browser()
        _ensure_browser()

    try:
        return func(*args, **kwargs)
    except Exception:
        _reset_browser()
        raise


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
    return await _with_browser(
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
    profile: str | None = None,
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
    skip_slow: bool = False,
    source_timeout_ms: int = 30_000,
    dedupe: bool = True,
) -> dict:
    """Search job aggregators for listings matching the query.

    Returns search results as a dictionary with 'params', 'listings',
    'total', 'timestamp', and 'errors' keys.

    Args:
        query: Search query (e.g. "QA engineer", "Python backend")
        sources: Comma-separated scraper names, or "all"
        profile: Optional source profile: fast or full
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
        skip_slow: Skip browser-backed slow sources
        source_timeout_ms: Per-source timeout in milliseconds
        dedupe: Deduplicate normalized results before returning
    """
    return await _with_browser(
        _search_impl,
        query=query,
        sources=sources,
        profile=profile,
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
        skip_slow=skip_slow,
        source_timeout_ms=source_timeout_ms,
        dedupe=dedupe,
    )


def _search_impl(
    query: str,
    sources: str = "all",
    profile: str | None = None,
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
    skip_slow: bool = False,
    source_timeout_ms: int = 30_000,
    dedupe: bool = True,
) -> dict:
    from job_harness.search_runner import execute_search

    results = execute_search(
        query=query,
        ensure_context=_ensure_browser,
        cache_factory=_get_cache,
        sources=sources,
        profile=profile,
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
        skip_slow=skip_slow,
        source_timeout_ms=source_timeout_ms,
        dedupe=dedupe,
    )
    return results.to_dict()


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
    return await _with_browser(_resolve_impl, listings=listings, query=query, cache=cache)


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
    return await _with_browser(
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
# Non-blocking surface — search_start / search_status / search_results /
# search_cancel / search_refine / list_active_runs.
#
# These tools never block the agent's turn for longer than ~100 ms. The
# engine runs as a background asyncio.Task; the journal on disk is the
# source of truth so polling and result-reading work even after the
# server restarts.
# ---------------------------------------------------------------------------

_RUNS_ROOT = _PLUGIN_ROOT / "data" / ".runs"
_engine_singleton = None
_run_registry_singleton = None


def _get_engine():
    global _engine_singleton
    if _engine_singleton is None:
        from job_harness.search_engine import SearchEngine

        baselines = _PLUGIN_ROOT / "data" / "source_baselines.json"
        _engine_singleton = SearchEngine(
            sanity_baselines_path=baselines if baselines.exists() else None,
        )
    return _engine_singleton


def _get_run_registry():
    global _run_registry_singleton
    if _run_registry_singleton is None:
        # Lazy import so the registry's GC doesn't run at module load
        # when no tool has been invoked.
        import job_harness.scrapers  # noqa: F401  — register all scrapers
        import job_harness.scrapers.career  # noqa: F401
        from job_harness.run_registry import RunRegistry

        async def runner(request, journal, run_id):
            await _get_engine().execute(request, journal=journal, run_id=run_id)

        _run_registry_singleton = RunRegistry(
            runs_root=_RUNS_ROOT,
            engine_runner=runner,
        )
    return _run_registry_singleton


def _build_search_request(
    *,
    query: str,
    sources: str = "all",
    profile: str | None = None,
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
    source_timeout_ms: int = 30_000,
    total_timeout_ms: int = 90_000,
    strict_flags: bool = True,
    dedupe: bool = True,
):
    from job_harness.countries import normalize_country_code
    from job_harness.types import SearchRequest

    source_tuple: tuple[str, ...] | None
    if sources in (None, "", "all"):
        source_tuple = None
    else:
        source_tuple = tuple(s.strip() for s in sources.split(",") if s.strip())

    return SearchRequest(
        query=query,
        country=normalize_country_code(country),
        remote_only=remote_only,
        experience=experience,
        location=location,
        max_results=max_results,
        sources=source_tuple,
        profile=profile,
        detail=detail,
        resolve=resolve,
        cache=cache,
        exclude_keywords=tuple(
            k.strip() for k in (exclude_keywords or "").split(",") if k.strip()
        ),
        exclude_keywords_context=tuple(
            k.strip() for k in (exclude_keywords_context or "").split(",") if k.strip()
        ),
        exclude_companies=tuple(
            c.strip() for c in (exclude_companies or "").split(",") if c.strip()
        ),
        has_salary=has_salary,
        strict_flags=strict_flags,
        dedupe=dedupe,
        source_timeout_ms=source_timeout_ms,
        total_timeout_ms=total_timeout_ms,
    )


def _summary_dict(reader) -> dict:
    summary = reader.read_summary()
    if summary is not None:
        return summary
    return reader.snapshot().to_dict()


@mcp.tool
async def search_start(
    query: str,
    sources: str = "all",
    profile: str | None = None,
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
    source_timeout_ms: int = 30_000,
    total_timeout_ms: int = 90_000,
    strict_flags: bool = True,
    dedupe: bool = True,
) -> dict:
    """Kick off a search in the background. Returns immediately (<100ms).

    The run writes its results to data/.runs/<run_id>/raw.jsonl with
    fsync per listing — search_status, search_results, search_cancel
    operate on that journal and never block.
    """
    from job_harness.run_registry import MaxConcurrentRunsReached

    request = _build_search_request(
        query=query,
        sources=sources,
        profile=profile,
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
        source_timeout_ms=source_timeout_ms,
        total_timeout_ms=total_timeout_ms,
        strict_flags=strict_flags,
        dedupe=dedupe,
    )
    try:
        run = await _get_run_registry().start(request)
    except MaxConcurrentRunsReached as exc:
        return {
            "error": "max_concurrent_runs_reached",
            "active_runs": [s.to_dict() for s in exc.active],
        }
    return {
        "run_id": run.run_id,
        "run_dir": str(run.run_dir),
        "started_at": run.started_at.isoformat(),
    }


@mcp.tool
async def search_status(run_id: str) -> dict:
    """Cheap (<50ms) poll. Reads the journal on disk; works after
    server restart for runs evicted from memory."""
    from job_harness.run_registry import UnknownRunId

    registry = _get_run_registry()
    try:
        await registry.touch(run_id)
    except UnknownRunId:
        pass
    try:
        reader = registry.read_journal(run_id)
    except UnknownRunId:
        return {"error": "unknown_run_id", "run_id": run_id}
    snap = reader.snapshot()
    summary = _summary_dict(reader)
    return {
        "run_id": run_id,
        "state": snap.state.value,
        "started_at": snap.started_at,
        "ended_at": snap.ended_at,
        "elapsed_ms": snap.elapsed_ms,
        "listings_count": snap.listings_count,
        "errors": snap.errors,
        "sources": {name: s.to_dict() for name, s in snap.sources.items()},
        "summary": summary,
    }


@mcp.tool
async def search_results(
    run_id: str, max_results: int = 20, include_partial: bool = True
) -> dict:
    """Return the SearchResults snapshot derived from the journal.

    Works on running, completed, cancelled, and failed runs. If the
    run is still in flight and include_partial is False, returns a
    {"error": "still_running"} placeholder.
    """
    from job_harness.run_registry import UnknownRunId
    from job_harness.types import RunState

    registry = _get_run_registry()
    try:
        await registry.touch(run_id)
    except UnknownRunId:
        pass
    try:
        reader = registry.read_journal(run_id)
    except UnknownRunId:
        return {"error": "unknown_run_id", "run_id": run_id}
    snap = reader.snapshot()
    if snap.state == RunState.RUNNING and not include_partial:
        return {"run_id": run_id, "state": "running", "error": "still_running"}
    listings = snap.listings[:max_results]
    return {
        "run_id": run_id,
        "state": snap.state.value,
        "started_at": snap.started_at,
        "ended_at": snap.ended_at,
        "elapsed_ms": snap.elapsed_ms,
        "request": snap.request,
        "total": len(listings),
        "listings": listings,
        "errors": snap.errors,
        "sources": {name: s.to_dict() for name, s in snap.sources.items()},
    }


@mcp.tool
async def search_cancel(run_id: str) -> dict:
    """Cancel an active run. Returns immediately; cleanup happens
    asynchronously. Idempotent."""
    from job_harness.run_registry import UnknownRunId

    try:
        await _get_run_registry().cancel(run_id)
    except UnknownRunId:
        return {"error": "unknown_run_id", "run_id": run_id}
    return {"run_id": run_id, "state": "cancelling"}


@mcp.tool
async def search_refine(
    run_id: str,
    experience: str | None = None,
    has_salary: bool = False,
    remote_only: bool = False,
    exclude_companies: str | None = None,
    exclude_keywords: str | None = None,
    exclude_keywords_context: str | None = None,
    location: str | None = None,
    max_results: int = 20,
    strict_refine: bool = False,
) -> dict:
    """Re-filter the journal of a finished run without re-scraping.

    The original capabilities determine whether a listing's source
    can honestly enforce a refine filter; sources whose capability
    for a refine filter is UNSUPPORTED have their listings tagged
    with raw['filter_uncertain'][flag]=True (lenient) or removed
    (strict_refine=True).
    """
    from job_harness.filters import (
        _exclude_companies,
        apply_filters,
        has_salary as has_salary_filter,
        location_in,
        min_experience,
        no_keywords,
        remote_only as remote_only_filter,
    )
    from job_harness.models import JobListing
    from job_harness.run_registry import UnknownRunId

    registry = _get_run_registry()
    try:
        await registry.touch(run_id)
    except UnknownRunId:
        pass
    try:
        reader = registry.read_journal(run_id)
    except UnknownRunId:
        return {"error": "unknown_run_id", "run_id": run_id}

    snap = reader.snapshot()
    listings_raw = list(snap.listings)
    # Re-hydrate JobListing objects so the existing predicate filters work.
    listings: list[JobListing] = []
    for raw in listings_raw:
        try:
            listings.append(JobListing(**{k: v for k, v in raw.items() if k in JobListing.__dataclass_fields__}))
        except TypeError:
            continue

    from collections.abc import Callable as _Callable
    predicates: list[_Callable[[JobListing], bool]] = []
    refine_flags: list[str] = []
    if remote_only:
        predicates.append(remote_only_filter)
        refine_flags.append("remote_only")
    if has_salary:
        predicates.append(has_salary_filter)
        refine_flags.append("has_salary")
    if exclude_companies:
        predicates.append(_exclude_companies([c.strip() for c in exclude_companies.split(",")]))
    if experience:
        predicates.append(min_experience(experience))
        refine_flags.append("experience")
    if exclude_keywords:
        keywords = [k.strip() for k in exclude_keywords.split(",")]
        ignore_words = (
            [w.strip() for w in exclude_keywords_context.split(",")]
            if exclude_keywords_context
            else None
        )
        predicates.append(no_keywords(*keywords, ignore_context=ignore_words))
    if location:
        predicates.append(location_in(location))
        refine_flags.append("location")

    filtered = apply_filters(listings, predicates) if predicates else listings
    final = filtered[:max_results]

    return {
        "run_id": run_id,
        "state": snap.state.value,
        "total": len(final),
        "listings": [item.to_dict() for item in final],
        "errors": snap.errors,
        "refine_filters": refine_flags,
        "policy": "strict" if strict_refine else "lenient",
        "sources": {name: s.to_dict() for name, s in snap.sources.items()},
    }


@mcp.tool
async def list_active_runs(limit: int = 20) -> dict:
    """Recent runs from data/.runs/, in-memory and on-disk together.

    Useful after server restart to discover what runs exist."""
    summaries = _get_run_registry().list_recent(limit=limit)
    return {"runs": [s.to_dict() for s in summaries]}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
