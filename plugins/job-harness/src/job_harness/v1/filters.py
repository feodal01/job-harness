"""Universal callable-based filters for job listings."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from job_harness.v1.models import JobListing


def apply_filters(
    listings: list[JobListing],
    filters: Sequence[Callable[[JobListing], bool]],
) -> list[JobListing]:
    """Apply a chain of predicate filters. All must pass."""
    return [listing for listing in listings if all(filter_func(listing) for filter_func in filters)]


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


def experience_in(levels: list[str] | tuple[str, ...]) -> Callable[[JobListing], bool]:
    """Factory: keep listings whose assessed grade is in the given exact set.

    Unknown-grade listings are retained so callers can inspect them separately.
    """
    requested = set(levels)
    def predicate(listing: JobListing) -> bool:
        if listing.experience_origin == "unknown":
            return True
        return bool(requested.intersection(listing.experience_levels))
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
