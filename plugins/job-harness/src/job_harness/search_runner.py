"""Shared fail-open search runner for CLI and MCP entrypoints."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import job_harness.registry as registry
import job_harness.scrapers  # noqa: F401
import job_harness.scrapers.career  # noqa: F401
from job_harness.countries import format_country_codes, normalize_country_code
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

DEFAULT_SOURCE_TIMEOUT_MS = 30_000
SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_PARTIAL = "partial"
SOURCE_STATUS_TIMEOUT = "timeout"
SOURCE_STATUS_ERROR = "error"
SOURCE_STATUS_SKIPPED = "skipped"


@dataclass(frozen=True)
class SourceStatus:
    source: str
    display_name: str
    status: str
    duration_ms: int
    raw_count: int = 0
    after_filter_count: int = 0
    after_dedupe_count: int = 0
    company_missing_count: int = 0
    error_class: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FilterPlan:
    name: str
    predicate: Callable[[JobListing], bool]


def execute_search(
    *,
    query: str,
    ensure_context: Callable[[], Any],
    cache_factory: Callable[[], Any | None],
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
    source_timeout_ms: int = DEFAULT_SOURCE_TIMEOUT_MS,
    raw_jsonl: str | Path | None = None,
    dedupe: bool = True,
    debug: bool = False,
    progress: Callable[[str], None] | None = None,
) -> SearchResults:
    """Run a broad search without letting one source discard prior results."""
    if source_timeout_ms < 1:
        raise ValueError("source_timeout_ms must be >= 1")

    country_code = normalize_country_code(country)
    params = SearchParams(
        query=query,
        country=country_code,
        remote_only=remote_only,
        experience=experience,
        location=location,
        max_results=max_results,
    )
    source_names = resolve_source_names(
        sources=sources,
        profile=profile,
        country=country_code,
        detail=detail,
        skip_slow=skip_slow,
    )
    filter_plan = build_filter_plan(
        remote_only=remote_only,
        has_salary=has_salary,
        exclude_companies=exclude_companies,
        experience=experience,
        exclude_keywords=exclude_keywords,
        exclude_keywords_context=exclude_keywords_context,
        location=location,
    )
    raw_path = _prepare_raw_jsonl(raw_jsonl)
    all_listings: list[JobListing] = []
    errors: list[str] = []
    source_statuses: list[SourceStatus] = []
    browser_context = None

    for source_name in source_names:
        started = time.monotonic()
        source_listings: list[JobListing] = []
        status = SOURCE_STATUS_OK
        error: Exception | None = None
        display_name = source_name

        try:
            scraper_class = registry.get_scraper_class(source_name)
            display_name = getattr(scraper_class, "display_name", source_name)
            if skip_slow and _source_is_slow(scraper_class, detail):
                status = SOURCE_STATUS_SKIPPED
            else:
                needs_browser = scraper_class.requires_browser or (detail and scraper_class.detail_requires_browser)
                scraper_context = ensure_context() if needs_browser else None
                if scraper_context is not None:
                    browser_context = scraper_context
                scraper = registry.create_scraper(
                    source_name,
                    scraper_context,
                    max_results=params.max_results,
                    debug=debug,
                    timeout_ms=source_timeout_ms,
                )
                if progress:
                    progress(f"Searching {scraper.display_name}...")
                source_listings = scraper.search(params)
                if detail and source_listings and not getattr(scraper, "timed_out", False):
                    source_listings, detail_errors = _fetch_details(scraper, source_listings)
                    errors.extend(detail_errors)
                    if detail_errors:
                        status = SOURCE_STATUS_PARTIAL
                if getattr(scraper, "timed_out", False):
                    status = SOURCE_STATUS_PARTIAL if source_listings else SOURCE_STATUS_TIMEOUT
                elif getattr(scraper, "runtime_error", None) is not None:
                    error = scraper.runtime_error
                    status = SOURCE_STATUS_PARTIAL if source_listings else SOURCE_STATUS_ERROR
                if progress:
                    progress(f"  Found {len(source_listings)} listings")
        except Exception as exc:
            error = exc
            status = SOURCE_STATUS_TIMEOUT if _is_timeout_exception(exc) else SOURCE_STATUS_ERROR
            if progress:
                progress(f"  Error: {exc}")

        filtered_source = _apply_filter_plan(source_listings, filter_plan)
        deduped_source = dedupe_listings(filtered_source) if dedupe else filtered_source
        if error is not None and status in {SOURCE_STATUS_PARTIAL, SOURCE_STATUS_ERROR, SOURCE_STATUS_TIMEOUT}:
            errors.append(f"{source_name}: {error}")
        source_status = SourceStatus(
            source=source_name,
            display_name=display_name,
            status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_count=len(source_listings),
            after_filter_count=len(filtered_source),
            after_dedupe_count=len(deduped_source),
            company_missing_count=sum(1 for listing in source_listings if _company_missing(listing)),
            error_class=type(error).__name__ if error else None,
            error_message=str(error) if error else None,
        )
        source_statuses.append(source_status)
        _write_raw_records(raw_path, source_name, source_listings, source_status)
        all_listings.extend(source_listings)

    before_filter = len(all_listings)
    filtered_listings = _apply_filter_plan(all_listings, filter_plan)
    before_dedupe = len(filtered_listings)
    deduped_listings = dedupe_listings(filtered_listings) if dedupe else filtered_listings
    final_listings = deduped_listings[:max_results]

    if resolve and final_listings:
        from job_harness.employer_resolver import resolve_listings

        browser_context = browser_context or ensure_context()
        employer_cache = cache_factory() if cache else None
        enriched = resolve_listings(
            [listing.to_dict() for listing in final_listings],
            browser_context,
            query=params.query,
            cache=employer_cache,
        )
        for listing, enrich in zip(final_listings, enriched, strict=False):
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

    summary = {
        "source_statuses": [source_status.to_dict() for source_status in source_statuses],
        "filters": {
            "enabled": [item.name for item in filter_plan],
            "before": before_filter,
            "after": len(filtered_listings),
            "removed": before_filter - len(filtered_listings),
        },
        "dedupe": {
            "enabled": dedupe,
            "before": before_dedupe,
            "after": len(deduped_listings),
            "removed": before_dedupe - len(deduped_listings),
        },
        "max_results": {
            "requested": max_results,
            "returned": len(final_listings),
        },
    }
    return SearchResults(params=params, listings=final_listings, errors=errors, summary=summary)


def resolve_source_names(
    *,
    sources: str,
    profile: str | None,
    country: str | None,
    detail: bool,
    skip_slow: bool,
) -> list[str]:
    if profile not in {None, "fast", "full"}:
        raise ValueError("profile must be one of: fast, full")

    if sources == "all" or not sources:
        source_names = registry.list_scrapers(country=country)
        if profile in {"fast", "full"}:
            source_names = [source for source in source_names if source != "company_directory"]
        if profile == "fast":
            source_names = [
                source for source in source_names
                if not _source_is_slow(registry.get_scraper_class(source), detail)
            ]
    else:
        source_names = [source.strip() for source in sources.split(",") if source.strip()]

    resolved = []
    for source_name in source_names:
        scraper_class = registry.get_scraper_class(source_name)
        if not scraper_class.supports_country(country):
            raise ValueError(
                f"{source_name} does not support country {country}. "
                f"Supported countries: {format_country_codes(scraper_class.countries)}"
            )
        if skip_slow and _source_is_slow(scraper_class, detail):
            resolved.append(source_name)
            continue
        resolved.append(source_name)
    return resolved


def build_filter_plan(
    *,
    remote_only: bool,
    has_salary: bool,
    exclude_companies: str | None,
    experience: str | None,
    exclude_keywords: str | None,
    exclude_keywords_context: str | None,
    location: str | None,
) -> list[FilterPlan]:
    filters: list[FilterPlan] = []
    if remote_only:
        filters.append(FilterPlan("remote_only", remote_only_filter))
    if has_salary:
        filters.append(FilterPlan("has_salary", has_salary_filter))
    if exclude_companies:
        filters.append(FilterPlan("exclude_companies", _exclude_companies([c.strip() for c in exclude_companies.split(",")])))
    if experience:
        filters.append(FilterPlan(f"min_experience:{experience}", min_experience(experience)))
    if exclude_keywords:
        keywords = [keyword.strip() for keyword in exclude_keywords.split(",")]
        ignore_words = (
            [word.strip() for word in exclude_keywords_context.split(",")]
            if exclude_keywords_context
            else None
        )
        filters.append(FilterPlan("exclude_keywords", no_keywords(*keywords, ignore_context=ignore_words)))
    if location:
        filters.append(FilterPlan("location", location_in(location)))
    return filters


def dedupe_listings(listings: list[JobListing]) -> list[JobListing]:
    unique: list[JobListing] = []
    key_to_index: dict[tuple[str, str], int] = {}
    for listing in listings:
        keys = _dedupe_keys(listing)
        existing_index = next((key_to_index[key] for key in keys if key in key_to_index), None)
        if existing_index is None:
            key_to_index.update({key: len(unique) for key in keys})
            unique.append(listing)
            continue

        existing = unique[existing_index]
        if _listing_quality(listing) > _listing_quality(existing):
            unique[existing_index] = listing
        for key in keys:
            key_to_index[key] = existing_index
    return unique


def _fetch_details(scraper: Any, listings: list[JobListing]) -> tuple[list[JobListing], list[str]]:
    detailed = []
    errors = []
    for listing in listings:
        try:
            scraper.enforce_deadline()
            detailed.append(scraper.fetch_detail(listing))
        except Exception as exc:
            if _is_timeout_exception(exc):
                scraper.mark_timed_out()
                errors.append(f"{scraper.name}: detail timeout for {listing.url}: {exc}")
                detailed.append(listing)
                break
            errors.append(f"{scraper.name}: detail error for {listing.url}: {exc}")
            detailed.append(listing)
    return detailed, errors


def _apply_filter_plan(listings: list[JobListing], filter_plan: list[FilterPlan]) -> list[JobListing]:
    return apply_filters(listings, [item.predicate for item in filter_plan]) if filter_plan else list(listings)


def _source_is_slow(scraper_class: Any, detail: bool) -> bool:
    return bool(scraper_class.requires_browser or (detail and scraper_class.detail_requires_browser))


def _is_timeout_exception(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".casefold()
    return isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text


def _dedupe_keys(listing: JobListing) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    vacancy_id = _hh_vacancy_id(listing.url)
    if vacancy_id:
        keys.append(("hh_id", vacancy_id))
    canonical_url = _canonical_url(listing.url)
    if canonical_url:
        keys.append(("url", canonical_url))
    title_company = _title_company_key(listing)
    if title_company:
        keys.append(("title_company", title_company))
    if not keys:
        keys.append(("object", f"{listing.source}:{listing.title}:{listing.url}"))
    return keys


def _canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip().rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _hh_vacancy_id(url: str) -> str | None:
    match = re.search(r"/vacancy/(\d+)", url)
    return match.group(1) if match else None


def _title_company_key(listing: JobListing) -> str | None:
    if not listing.company.strip():
        return None
    title = re.sub(r"\s+", " ", listing.title.casefold()).strip()
    company = re.sub(r"\s+", " ", listing.company.casefold()).strip()
    return f"{title}|{company}"


def _listing_quality(listing: JobListing) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        int(bool(listing.company.strip())),
        int("+direct" in listing.source or bool(listing.raw.get("direct_vacancy_url"))),
        int(bool(listing.description)),
        int(bool(listing.requirements)),
        len(listing.skills),
        int(bool(listing.salary)),
        int(bool(listing.remote)),
        int(bool(listing.country)),
    )


def _company_missing(listing: JobListing) -> bool:
    return not listing.company.strip() or bool(listing.raw.get("company_missing"))


def _prepare_raw_jsonl(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    raw_path = Path(path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("", encoding="utf-8")
    return raw_path


def _write_raw_records(path: Path | None, source_name: str, listings: list[JobListing], status: SourceStatus) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as file:
        for listing in listings:
            file.write(json.dumps({"type": "listing", "source": source_name, "listing": listing.to_dict()}, ensure_ascii=False) + "\n")
        file.write(json.dumps({"type": "source_status", **status.to_dict()}, ensure_ascii=False) + "\n")
