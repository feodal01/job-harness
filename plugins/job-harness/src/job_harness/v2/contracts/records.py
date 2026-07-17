"""Raw evidence and source-attempt records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from job_harness.v2.contracts.enums import (
    DescriptionAvailability,
    SearchCriterion,
    SourceOutcome,
    SourceType,
)

_GLOBAL_REMOTE_EVIDENCE_MARKERS = ("global", "worldwide", "anywhere", "весь мир")
_GLOBAL_REMOTE_EVIDENCE_RAW_KEYS = frozenset(
    {
        "eligible_locations",
        "location",
        "locations",
        "remote_locations",
        "remote_restrictions",
        "remote_scope",
        "remote_type",
        "work_format",
    }
)


@dataclass(frozen=True)
class RawListing:
    source_listing_id: str | None
    title: str
    url: str
    source: str
    company: str | None = None
    country: str | None = None
    city: str | None = None
    location_text: str | None = None
    location_cities: tuple[str, ...] = ()
    location_countries: tuple[str, ...] = ()
    location_regions: tuple[str, ...] = ()
    salary_text: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_gross: bool | None = None
    posted_at: str | None = None
    remote_in_country: bool | None = None
    remote_global: bool | None = None
    remote_scope_countries: tuple[str, ...] = ()
    remote_scope_regions: tuple[str, ...] = ()
    relocation: bool | None = None
    relocation_destinations: tuple[str, ...] = ()
    native_grade: str | None = None
    description: str | None = None
    requirements: str | None = None
    additional_sections: dict[str, str] = field(default_factory=dict)
    skills: tuple[str, ...] = ()
    raw_text: str | None = None
    raw: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.title, "title")
        _require_non_empty(self.url, "url")
        _require_non_empty(self.source, "source")
        _require_http_url(self.url, "url")
        if self.salary_min is not None and self.salary_min < 0:
            raise ValueError("salary_min must be >= 0")
        if self.salary_max is not None and self.salary_max < 0:
            raise ValueError("salary_max must be >= 0")
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("salary_min must be <= salary_max")
        if self.salary_period not in {None, "hour", "day", "month", "year"}:
            raise ValueError("invalid salary period")
        if self.salary_gross is not None and not isinstance(self.salary_gross, bool):
            raise ValueError("salary_gross must be boolean when provided")
        for field_name in (
            "location_cities",
            "location_countries",
            "location_regions",
            "remote_scope_countries",
            "remote_scope_regions",
            "relocation_destinations",
        ):
            values = tuple(
                dict.fromkeys(value.strip() for value in getattr(self, field_name) if value.strip())
            )
            object.__setattr__(self, field_name, values)
        if self.relocation_destinations and self.relocation is not True:
            raise ValueError("relocation destinations require relocation=True")
        if self.remote_global is True and not _has_explicit_global_remote_evidence(self):
            raise ValueError("remote_global=True requires explicit global remote evidence")


@dataclass(frozen=True)
class RawSearchRecord:
    run_id: str
    append_sequence: int
    query_variant: str
    source: str
    source_type: SourceType
    collected_at: datetime
    listing: RawListing
    schema_version: Literal[1] = 1
    record_type: Literal["raw_listing"] = "raw_listing"
    description_availability: DescriptionAvailability = DescriptionAvailability.NOT_REQUESTED
    detail_fetched: bool = False
    detail_parse_error: str | None = None
    source_url: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.run_id, "run_id")
        _require_non_empty(self.query_variant, "query_variant")
        _require_non_empty(self.source, "source")
        if self.append_sequence < 0:
            raise ValueError("append_sequence must be >= 0")
        if self.listing.source != self.source:
            raise ValueError("listing.source must match record source")
        if self.source_url is not None:
            _require_http_url(self.source_url, "source_url")


@dataclass(frozen=True)
class CriteriaDiagnostics:
    requested: frozenset[SearchCriterion]
    native_applied: frozenset[SearchCriterion] = frozenset()
    structured_evidence_available: frozenset[SearchCriterion] = frozenset()
    unsupported: frozenset[SearchCriterion] = frozenset()
    postprocess: frozenset[SearchCriterion] = frozenset()

    def __post_init__(self) -> None:
        for field_name in (
            "native_applied",
            "structured_evidence_available",
            "unsupported",
            "postprocess",
        ):
            values = getattr(self, field_name)
            if not values <= self.requested:
                extra = ", ".join(sorted(item.value for item in values - self.requested))
                raise ValueError(f"{field_name} must be a subset of requested criteria: {extra}")
        if self.native_applied & self.unsupported:
            raise ValueError("native_applied and unsupported criteria must be disjoint")


@dataclass(frozen=True)
class AttemptCounts:
    raw_listings_written: int
    pages_visited: int = 0

    def __post_init__(self) -> None:
        if self.raw_listings_written < 0:
            raise ValueError("raw_listings_written must be >= 0")
        if self.pages_visited < 0:
            raise ValueError("pages_visited must be >= 0")


@dataclass(frozen=True)
class AttemptEvidence:
    no_results: bool = False
    multi_step_terminal: bool = False
    block_signal: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SourceAttemptRecord:
    source: str
    source_type: SourceType
    query_variant: str
    attempt: int
    outcome: SourceOutcome
    started_at: datetime
    finished_at: datetime
    elapsed_ms: int
    source_limit: int
    limit_reached: bool
    counts: AttemptCounts
    criteria: CriteriaDiagnostics
    evidence: AttemptEvidence = field(default_factory=AttemptEvidence)

    def __post_init__(self) -> None:
        _require_non_empty(self.source, "source")
        _require_non_empty(self.query_variant, "query_variant")
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be >= started_at")
        if self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be >= 0")
        if self.source_limit < 1:
            raise ValueError("source_limit must be >= 1")
        if self.limit_reached and self.outcome != SourceOutcome.SUCCESS:
            raise ValueError("limit_reached is normal completion and requires outcome=success")
        if self.outcome == SourceOutcome.SUCCESS and self.counts.raw_listings_written < 1:
            raise ValueError("success requires at least one written raw listing")
        if self.outcome == SourceOutcome.NO_RESULTS:
            if self.counts.raw_listings_written != 0:
                raise ValueError("no_results requires zero written raw listings")
            if not self.evidence.no_results:
                raise ValueError("no_results requires explicit no-results evidence")
        if self.outcome == SourceOutcome.PARTIAL_SUCCESS and self.counts.raw_listings_written < 1:
            raise ValueError("partial_success requires at least one written raw listing")


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _require_http_url(value: str, field_name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute http(s) URL")


def _has_explicit_global_remote_evidence(listing: RawListing) -> bool:
    if _value_mentions_global_remote((listing.location_text, listing.country, listing.city)):
        return True
    for key, value in listing.raw.items():
        if key in _GLOBAL_REMOTE_EVIDENCE_RAW_KEYS and _value_mentions_global_remote(value):
            return True
    return False


def _value_mentions_global_remote(value: object) -> bool:
    if isinstance(value, str):
        text = value.casefold()
        return any(marker in text for marker in _GLOBAL_REMOTE_EVIDENCE_MARKERS)
    if isinstance(value, dict):
        return any(_value_mentions_global_remote(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_value_mentions_global_remote(item) for item in value)
    return False
