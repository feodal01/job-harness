"""Universal callable-based filters for job listings."""

from __future__ import annotations

from typing import Callable

from job_harness.models import JobListing


def apply_filters(
    listings: list[JobListing],
    filters: list[Callable[[JobListing], bool]],
) -> list[JobListing]:
    """Apply a chain of predicate filters. All must pass."""
    return [l for l in listings if all(f(l) for f in filters)]


# --- Pre-built filter factories ---


def remote_only(listing: JobListing) -> bool:
    """Keep only remote listings."""
    return listing.remote


def no_keywords(
    *keywords: str,
    ignore_context: list[str] | None = None,
) -> Callable[[JobListing], bool]:
    """Factory: exclude listings whose description/requirements contain any keyword.

    Keywords in "nice to have" context (e.g. "будет плюсом") are allowed
    if `ignore_context` words appear near the keyword.
    """
    def predicate(listing: JobListing) -> bool:
        text = f"{listing.description or ''} {listing.requirements or ''}".lower()
        if not text.strip():
            return True  # Can't filter without content
        for kw in keywords:
            if kw.lower() not in text:
                continue
            if ignore_context:
                idx = text.find(kw.lower())
                ctx_start = max(0, idx - 80)
                ctx = text[ctx_start:idx + len(kw) + 30]
                if any(w in ctx for w in ignore_context):
                    continue
            return False
        return True
    return predicate


def min_experience(level: str) -> Callable[[JobListing], bool]:
    """Factory: keep listings at or above the given experience level."""
    order = {"junior": 0, "middle": 1, "senior": 2}
    min_level = order.get(level, 0)
    def predicate(listing: JobListing) -> bool:
        if not listing.experience:
            return True
        return order.get(listing.experience, 0) >= min_level
    return predicate


def has_salary(listing: JobListing) -> bool:
    """Keep only listings with salary info."""
    return listing.salary is not None


def location_in(*locations: str) -> Callable[[JobListing], bool]:
    """Factory: keep listings whose location contains any of the given strings."""
    def predicate(listing: JobListing) -> bool:
        if not listing.location:
            return True
        return any(loc.lower() in listing.location.lower() for loc in locations)
    return predicate


def _exclude_companies(names: list[str]) -> Callable[[JobListing], bool]:
    """Factory: exclude listings from specific companies (case-insensitive)."""
    lowered = [n.strip().lower() for n in names]
    def predicate(listing: JobListing) -> bool:
        return listing.company.lower() not in lowered
    return predicate
