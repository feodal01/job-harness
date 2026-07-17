"""Abstract scraper contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from job_harness.v2.contracts.enums import HttpMethod, SourceOutcome
from job_harness.v2.contracts.fixtures import RequiredParserFixtures
from job_harness.v2.contracts.records import AttemptEvidence, RawListing
from job_harness.v2.contracts.search import SearchRequest
from job_harness.v2.contracts.source import SourceDescriptor


@dataclass(frozen=True)
class SourceFetchRequest:
    """Read-only source fetch; POST bodies may encode queries but never mutations."""

    source_id: str
    query_variant: str
    url: str
    method: HttpMethod = HttpMethod.GET
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    query_variants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must be non-empty")
        if not self.query_variant.strip():
            raise ValueError("query_variant must be non-empty")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("url must be an absolute http(s) URL")
        variants = self.query_variants or (self.query_variant,)
        cleaned_variants = tuple(variant.strip() for variant in variants)
        if not cleaned_variants or any(not variant for variant in cleaned_variants):
            raise ValueError("query_variants must contain non-empty values")
        object.__setattr__(self, "query_variants", cleaned_variants)


@dataclass(frozen=True)
class SourceResponseArtifact:
    source_id: str
    url: str
    media_type: str
    body: str

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must be non-empty")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("url must be an absolute http(s) URL")
        if not self.media_type.strip():
            raise ValueError("media_type must be non-empty")


@dataclass(frozen=True)
class SourceSearchParseResult:
    outcome: SourceOutcome
    listings: tuple[RawListing, ...]
    evidence: AttemptEvidence = field(default_factory=AttemptEvidence)
    next_request: SourceFetchRequest | None = None
    parallel_requests: tuple[SourceFetchRequest, ...] = ()

    def __post_init__(self) -> None:
        if self.next_request is not None and self.parallel_requests:
            raise ValueError("parse result must not include both next_request and parallel_requests")
        if (
            self.outcome == SourceOutcome.SUCCESS
            and not self.listings
            and self.next_request is None
            and not self.parallel_requests
            and not self.evidence.multi_step_terminal
        ):
            raise ValueError("success parse result requires at least one listing")
        if self.outcome == SourceOutcome.NO_RESULTS:
            if self.listings:
                raise ValueError("no_results parse result must not include listings")
            if not self.evidence.no_results:
                raise ValueError("no_results parse result requires explicit evidence")
            if self.next_request is not None:
                raise ValueError("no_results parse result must not include next_request")
            if self.parallel_requests:
                raise ValueError("no_results parse result must not include parallel_requests")


class SourceScraper(ABC):
    @property
    @abstractmethod
    def descriptor(self) -> SourceDescriptor:
        """Static source declaration for the supported source catalog."""

    @property
    @abstractmethod
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        """Required parser fixture suite for this scraper."""

    @abstractmethod
    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        """Build source-native search requests for every relevant query variant."""

    @abstractmethod
    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        """Parse one real source response artifact into source-native raw facts."""


class DetailEnrichmentScraper(SourceScraper):
    @abstractmethod
    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        """Build a source-native detail request for a raw listing."""

    @abstractmethod
    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        """Merge one real detail response into a raw listing."""
