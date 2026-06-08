"""Post-aggregation dedupe and filter helpers shared by the SearchEngine.

Separated from search_engine.py to keep the orchestrator focused on
dispatch + journal IO. Pure functions — no I/O, no async.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from job_harness.experience_engine import experience_match_rank
from job_harness.filters import (
    _exclude_companies,
    apply_filters,
    experience_in,
    has_salary as has_salary_filter,
    location_in,
    no_keywords,
    remote_only as remote_only_filter,
)
from job_harness.models import JobListing


@dataclass(frozen=True)
class FilterPlan:
    """One named predicate. The engine surfaces the `name` list in the
    result summary so callers can see which filters were applied."""

    name: str
    predicate: Callable[[JobListing], bool]


def build_filter_plan(
    *,
    remote_only: bool,
    has_salary: bool,
    exclude_companies: str | None,
    experience_levels: tuple[str, ...],
    exclude_keywords: str | None,
    exclude_keywords_context: str | None,
    location: str | None,
) -> list[FilterPlan]:
    plan: list[FilterPlan] = []
    if remote_only:
        plan.append(FilterPlan("remote_only", remote_only_filter))
    if has_salary:
        plan.append(FilterPlan("has_salary", has_salary_filter))
    if exclude_companies:
        plan.append(
            FilterPlan(
                "exclude_companies",
                _exclude_companies([c.strip() for c in exclude_companies.split(",")]),
            )
        )
    if experience_levels:
        label = ",".join(experience_levels)
        plan.append(FilterPlan(f"experience_in:{label}", experience_in(experience_levels)))
    if exclude_keywords:
        keywords = [k.strip() for k in exclude_keywords.split(",")]
        ignore_words = (
            [w.strip() for w in exclude_keywords_context.split(",")]
            if exclude_keywords_context
            else None
        )
        plan.append(
            FilterPlan(
                "exclude_keywords", no_keywords(*keywords, ignore_context=ignore_words)
            )
        )
    if location:
        plan.append(FilterPlan("location", location_in(location)))
    return plan


def apply_filter_plan(
    listings: list[JobListing], plan: list[FilterPlan]
) -> list[JobListing]:
    if not plan:
        return list(listings)
    return apply_filters(listings, [item.predicate for item in plan])


def order_by_experience_match(
    listings: list[JobListing],
    experience_levels: tuple[str, ...],
) -> list[JobListing]:
    if not experience_levels:
        return list(listings)
    return sorted(
        listings,
        key=lambda listing: experience_match_rank(listing, experience_levels),
    )


def dedupe_listings(listings: list[JobListing]) -> list[JobListing]:
    """Collapse duplicates across sources using three key strategies:
    hh-style vacancy id, canonical URL, and (title, company) pair.

    When two listings collide we keep the one with the higher quality
    score so an aggregator with richer data wins over a thin entry."""
    unique: list[JobListing] = []
    key_to_index: dict[tuple[str, str], int] = {}
    for listing in listings:
        keys = _dedupe_keys(listing)
        existing_index = next(
            (key_to_index[key] for key in keys if key in key_to_index), None
        )
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
    return urlunparse(
        (parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", "")
    )


def _hh_vacancy_id(url: str) -> str | None:
    match = re.search(r"/vacancy/(\d+)", url)
    return match.group(1) if match else None


def _title_company_key(listing: JobListing) -> str | None:
    if not listing.company.strip():
        return None
    title = re.sub(r"\s+", " ", listing.title.casefold()).strip()
    company = re.sub(r"\s+", " ", listing.company.casefold()).strip()
    return f"{title}|{company}"


def _listing_quality(
    listing: JobListing,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        int(bool(listing.company.strip())),
        int(
            "+direct" in listing.source
            or bool(listing.raw.get("direct_vacancy_url"))
        ),
        int(bool(listing.description)),
        int(bool(listing.requirements)),
        len(listing.skills),
        int(bool(listing.salary)),
        int(bool(listing.remote)),
        int(bool(listing.country)),
    )
