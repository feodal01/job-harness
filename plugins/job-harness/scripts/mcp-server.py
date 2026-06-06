"""FastMCP server exposing job-harness tools to Claude Code.

Only the non-blocking async surface is exposed. Search runs through the
SearchEngine + BrowserPool with a journal-backed lifecycle:

  • search_start / search_status / search_results / search_cancel
  • search_refine — re-filter a finished run's journal
  • list_active_runs — discover recent runs (including post-restart)

Lookup tools (no scraping, sub-50 ms):
  • list_sources, search_company_jobs
  • cache_get, cache_upsert, cache_stats
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable as _Callable
from dataclasses import asdict
from pathlib import Path

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# TMPDIR safety — ensure Playwright can always create temp files.
# Set BEFORE any Playwright import so chromium picks up the override.
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

_PLUGIN_ROOT = Path(
    os.environ.get("JOB_HARNESS_ROOT", Path(__file__).resolve().parent.parent)
)
_LOCAL_CACHE = _PLUGIN_ROOT / "data" / "company-careers.json"
_PUBLIC_CACHE = _PLUGIN_ROOT / "data" / "company-careers-public.json"
_COMPANY_DIRECTORY = _PLUGIN_ROOT / "data" / "company-directory.json"
_RUNS_ROOT = _PLUGIN_ROOT / "data" / ".runs"
_SANITY_BASELINES = _PLUGIN_ROOT / "data" / "source_baselines.json"


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_engine_singleton = None
_run_registry_singleton = None
_browser_pool_singleton = None


def _get_browser_pool():
    """Construct the BrowserPool on first browser-needing call."""
    global _browser_pool_singleton
    if _browser_pool_singleton is None:
        from job_harness.browser_pool import BrowserPool

        _browser_pool_singleton = BrowserPool(max_contexts=2)
    return _browser_pool_singleton


def _get_engine():
    global _engine_singleton
    if _engine_singleton is None:
        from job_harness.search_engine import SearchEngine

        _engine_singleton = SearchEngine(
            browser_pool=_get_browser_pool(),
            sanity_baselines_path=_SANITY_BASELINES if _SANITY_BASELINES.exists() else None,
        )
    return _engine_singleton


def _get_run_registry():
    global _run_registry_singleton
    if _run_registry_singleton is None:
        # Lazy import side-effect: register every scraper.
        import job_harness.scrapers  # noqa: F401
        import job_harness.scrapers.career  # noqa: F401
        from job_harness.run_registry import RunRegistry

        async def runner(request, journal, run_id):
            await _get_engine().execute(request, journal=journal, run_id=run_id)

        _run_registry_singleton = RunRegistry(
            runs_root=_RUNS_ROOT,
            engine_runner=runner,
        )
    return _run_registry_singleton


def _get_cache():
    from job_harness.employer_cache import EmployerCache

    return EmployerCache(path=_LOCAL_CACHE, public_path=_PUBLIC_CACHE)


# ---------------------------------------------------------------------------
# Lookup tools — no scraping, sub-50 ms
# ---------------------------------------------------------------------------


@mcp.tool
def list_sources() -> dict[str, dict]:
    """List available job board scrapers and their honest capabilities."""
    import job_harness.scrapers  # noqa: F401
    import job_harness.scrapers.career  # noqa: F401
    from job_harness.registry import get_scraper_metadata

    return get_scraper_metadata()


@mcp.tool
def cache_get(company: str) -> dict | None:
    """Get the cache entry for a company. Returns null if not cached."""
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
    """Insert or update a local cache entry."""
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
    """Search the bundled company directory for employer career entrypoints."""
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
# Search request helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Non-blocking search surface
# ---------------------------------------------------------------------------


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
    fsync per listing. search_status, search_results, search_cancel
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
    """Cheap (<50ms) poll. Reads the journal on disk."""
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
    return {
        "run_id": run_id,
        "state": snap.state.value,
        "started_at": snap.started_at,
        "ended_at": snap.ended_at,
        "elapsed_ms": snap.elapsed_ms,
        "listings_count": snap.listings_count,
        "errors": snap.errors,
        "sources": {name: s.to_dict() for name, s in snap.sources.items()},
        "summary": _summary_dict(reader),
    }


@mcp.tool
async def search_results(
    run_id: str, max_results: int = 20, include_partial: bool = True
) -> dict:
    """Return the SearchResults snapshot derived from the journal."""
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
    """Re-filter the journal of a finished run without re-scraping."""
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
    listings: list[JobListing] = []
    for raw in snap.listings:
        try:
            listings.append(
                JobListing(**{k: v for k, v in raw.items() if k in JobListing.__dataclass_fields__})
            )
        except TypeError:
            continue

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
    """Recent runs from data/.runs/, in-memory and on-disk together."""
    summaries = _get_run_registry().list_recent(limit=limit)
    return {"runs": [s.to_dict() for s in summaries]}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
