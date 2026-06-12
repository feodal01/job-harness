"""Downstream result processing for raw search listings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_harness.dedupe_filter import (
    apply_filter_plan,
    build_filter_plan,
    dedupe_listings,
    order_by_experience_match,
)
from job_harness.experience_engine import annotate_listing_experience
from job_harness.models import JobListing, RawListing
from job_harness.types import FilterSupport, SearchCriterion, SourceStatus


@dataclass(frozen=True)
class PipelineResult:
    listings: list[JobListing]
    summary: dict[str, Any]


def run_result_pipeline(
    *,
    raw_listings: list[RawListing],
    request: dict[str, Any],
    sources: dict[str, SourceStatus],
) -> PipelineResult:
    listings: list[JobListing] = []
    for raw in raw_listings:
        listing = raw.to_job_listing()
        status = sources.get(listing.source)
        support = (
            FilterSupport.SERVER
            if status is not None
            and SearchCriterion.EXPERIENCE_LEVELS in status.supported_server_criteria
            else FilterSupport.UNSUPPORTED
        )
        annotate_listing_experience(listing, listing.source, support)
        listings.append(listing)

    experience_levels = tuple(str(item) for item in request.get("experience_levels") or ())
    filter_plan = build_filter_plan(
        remote_only=bool(request.get("remote_only", False)),
        has_salary=bool(request.get("has_salary", False)),
        exclude_companies=",".join(request.get("exclude_companies") or ()) or None,
        experience_levels=experience_levels,
        exclude_keywords=",".join(request.get("exclude_keywords") or ()) or None,
        exclude_keywords_context=",".join(request.get("exclude_keywords_context") or ()) or None,
        location=request.get("location"),
    )
    filtered = apply_filter_plan(listings, filter_plan)
    ranked = order_by_experience_match(filtered, experience_levels)
    dedupe_enabled = bool(request.get("dedupe", True))
    deduped = dedupe_listings(ranked) if dedupe_enabled else ranked
    deduped = order_by_experience_match(deduped, experience_levels)
    max_results = int(request.get("max_results", 20))
    final = deduped[:max_results]

    summary = {
        "filters": {
            "enabled": [item.name for item in filter_plan],
            "before": len(listings),
            "after": len(filtered),
            "removed": len(listings) - len(filtered),
            "experience": _experience_filter_summary(
                listings,
                filtered,
                experience_levels,
            ),
        },
        "dedupe": {
            "enabled": dedupe_enabled,
            "before": len(filtered),
            "after": len(deduped),
            "removed": len(filtered) - len(deduped),
        },
        "max_results": {
            "requested": max_results,
            "returned": len(final),
        },
    }
    return PipelineResult(listings=final, summary=summary)


def raw_listings_from_dicts(rows: list[dict[str, Any]]) -> list[RawListing]:
    listings: list[RawListing] = []
    for row in rows:
        try:
            listings.append(RawListing.from_dict(row))
        except (KeyError, TypeError, ValueError):
            continue
    return listings


def _experience_filter_summary(
    before: list[JobListing],
    after: list[JobListing],
    requested_levels: tuple[str, ...],
) -> dict[str, Any]:
    if not requested_levels:
        return {}
    requested = set(requested_levels)

    def matched(listing: JobListing) -> bool:
        return bool(requested.intersection(listing.experience_levels))

    return {
        "requested_levels": list(requested_levels),
        "native_matched": sum(
            1
            for listing in after
            if listing.experience_origin == "native" and matched(listing)
        ),
        "estimated_matched": sum(
            1
            for listing in after
            if listing.experience_origin == "estimated" and matched(listing)
        ),
        "unknown_kept": sum(
            1 for listing in after if listing.experience_origin == "unknown"
        ),
        "removed": len(before) - len(after),
    }
