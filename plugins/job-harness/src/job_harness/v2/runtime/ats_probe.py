"""Runtime helpers for ad-hoc ATS company parsing."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import RawListing, SourceFetchRequest, SourceOutcome
from job_harness.v2.ports import ArtifactFetcher
from job_harness.v2.runtime.http import HttpArtifactFetcher
from job_harness.v2.runtime.sources.companies.ats import (
    AtsCompanySourceConfig,
    AtsPlatform,
    ats_company_initial_request,
    ats_company_source_from_config,
    detect_ats_company_config,
)

_DEFAULT_SOURCE_ID = "adhoc:ats"
_DEFAULT_QUERY_VARIANT = "ats-url"
_DEFAULT_SOURCE_LIMIT = 200


@dataclass(frozen=True)
class AtsCompanyUrlParseResult:
    config: AtsCompanySourceConfig
    listings: tuple[RawListing, ...]
    pages_visited: int
    limit_reached: bool


async def fetch_ats_company_listings(
    url: str,
    *,
    company: str | None = None,
    source_id: str = _DEFAULT_SOURCE_ID,
    platform: AtsPlatform | None = None,
    source_limit: int = _DEFAULT_SOURCE_LIMIT,
    query_variant: str = _DEFAULT_QUERY_VARIANT,
    fetcher: ArtifactFetcher | None = None,
) -> AtsCompanyUrlParseResult:
    config = detect_ats_company_config(
        url,
        company=company,
        source_id=source_id,
        platform=platform,
    )
    return await fetch_ats_company_config_listings(
        config,
        source_limit=source_limit,
        query_variant=query_variant,
        fetcher=fetcher,
    )


async def fetch_ats_company_config_listings(
    config: AtsCompanySourceConfig,
    *,
    source_limit: int = _DEFAULT_SOURCE_LIMIT,
    query_variant: str = _DEFAULT_QUERY_VARIANT,
    fetcher: ArtifactFetcher | None = None,
) -> AtsCompanyUrlParseResult:
    if source_limit < 1:
        raise ValueError("source_limit must be >= 1")
    if not query_variant.strip():
        raise ValueError("query_variant must be non-empty")

    scraper = ats_company_source_from_config(config)
    artifact_fetcher = fetcher or HttpArtifactFetcher()
    current_request: SourceFetchRequest | None = ats_company_initial_request(
        config,
        query_variant=query_variant,
    )
    listings: list[RawListing] = []
    pages_visited = 0

    while current_request is not None and len(listings) < source_limit:
        response = await artifact_fetcher.fetch(current_request)
        if response.source_id != config.source_id:
            raise ValueError("response.source_id must match ATS config source_id")
        parsed = scraper.parse_search_response(response, current_request)
        pages_visited += 1

        if parsed.outcome == SourceOutcome.NO_RESULTS:
            if listings:
                raise ValueError("no_results page after collected listings is invalid")
            return AtsCompanyUrlParseResult(
                config=config,
                listings=(),
                pages_visited=pages_visited,
                limit_reached=False,
            )
        if parsed.outcome != SourceOutcome.SUCCESS:
            raise ValueError(f"ATS parser returned unsupported outcome: {parsed.outcome.value}")

        remaining = source_limit - len(listings)
        for listing in parsed.listings[:remaining]:
            if listing.source != config.source_id:
                raise ValueError("listing.source must match ATS config source_id")
            listings.append(listing)
        current_request = parsed.next_request

    if not listings:
        raise ValueError("ATS source produced neither listings nor explicit no_results")
    return AtsCompanyUrlParseResult(
        config=config,
        listings=tuple(listings),
        pages_visited=pages_visited,
        limit_reached=len(listings) >= source_limit,
    )
