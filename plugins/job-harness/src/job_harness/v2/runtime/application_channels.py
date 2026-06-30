"""Resolve secondary application channels for aggregator listings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from job_harness.v2.contracts import RawListing
from job_harness.v2.ports import ArtifactFetcher, RawRecordListingWriter, StoredRawRecord
from job_harness.v2.runtime.application_channel_profiles import (
    ProfileSiteResolution,
    ProfileSiteResolutionRequest,
    resolve_profile_site,
)
from job_harness.v2.runtime.application_channel_records import listing_from_record
from job_harness.v2.runtime.application_channel_resolver import (
    SiteResolution,
    SiteResolutionRequest,
    clean_http_url,
    resolve_site,
)
from job_harness.v2.runtime.application_channel_sources import application_channel_seed
from job_harness.v2.runtime.config import ApplicationChannelServiceConfig
from job_harness.v2.serialization import JsonObject


@dataclass(frozen=True)
class ApplicationChannelWorkItem:
    raw_record_id: int
    listing: RawListing

    def __post_init__(self) -> None:
        if self.raw_record_id < 1:
            raise ValueError("raw_record_id must be >= 1")


@dataclass(frozen=True)
class ApplicationChannelRunResult:
    attempted: int
    resolved: int
    failed: int
    updated: int

    def __post_init__(self) -> None:
        for field_name in ("attempted", "resolved", "failed", "updated"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0")


class ApplicationChannelEnrichmentRunner:
    def __init__(
        self,
        *,
        fetcher: ArtifactFetcher,
        writer: RawRecordListingWriter,
        config: ApplicationChannelServiceConfig,
        request_concurrency_by_source: int = 1,
    ) -> None:
        if request_concurrency_by_source < 1:
            raise ValueError("request_concurrency_by_source must be >= 1")
        self._fetcher = fetcher
        self._writer = writer
        self._config = config
        self._request_concurrency_by_source = request_concurrency_by_source

    async def run(self, work_items: tuple[ApplicationChannelWorkItem, ...]) -> ApplicationChannelRunResult:
        if not self._config.enabled or not work_items:
            return ApplicationChannelRunResult(attempted=0, resolved=0, failed=0, updated=0)

        profile_site_requests = _unique_profile_site_requests(work_items)
        profile_site_results = await self._resolve_profile_sites(profile_site_requests)
        site_requests = _unique_site_requests(work_items, profile_site_results)
        site_results = await self._resolve_sites(site_requests)
        attempted = sum(int(result.attempted) for result in profile_site_results.values()) + sum(
            int(result.attempted) for result in site_results.values()
        )
        resolved = sum(int(result.resolved) for result in site_results.values())
        failed = sum(int(result.failed) for result in profile_site_results.values()) + sum(
            int(result.failed) for result in site_results.values()
        )
        updated = 0

        for item in work_items:
            site_url = _site_url_for_listing(item.listing, profile_site_results)
            site_source = _site_source_for_listing(item.listing, profile_site_results)
            site_key = _site_result_key(item.listing, site_url)
            channels = _channels_for_listing(
                item.listing,
                site_url=site_url,
                site_source=site_source,
                site_resolution=site_results.get(site_key) if site_key is not None else None,
            )
            if not channels:
                continue
            raw = {**item.listing.raw, "application_channels": channels}
            self._writer.update_raw_record_listing(
                raw_record_id=item.raw_record_id,
                listing=replace(item.listing, raw=raw),
            )
            updated += 1

        return ApplicationChannelRunResult(
            attempted=attempted,
            resolved=resolved,
            failed=failed,
            updated=updated,
        )

    async def _resolve_profile_sites(
        self,
        profile_site_requests: tuple[ProfileSiteResolutionRequest, ...],
    ) -> dict[tuple[str, str], ProfileSiteResolution]:
        semaphores = _source_semaphores(profile_site_requests, self._request_concurrency_by_source)

        async def resolve(
            request: ProfileSiteResolutionRequest,
        ) -> tuple[tuple[str, str], ProfileSiteResolution]:
            async with semaphores[request.policy.source_id]:
                return _profile_site_request_key(request), await resolve_profile_site(
                    request,
                    fetcher=self._fetcher,
                )

        results = await asyncio.gather(*(resolve(request) for request in profile_site_requests))
        return dict(results)

    async def _resolve_sites(
        self,
        site_requests: tuple[SiteResolutionRequest, ...],
    ) -> dict[tuple[str, str], SiteResolution]:
        semaphores = _source_semaphores(site_requests, self._request_concurrency_by_source)

        async def resolve(request: SiteResolutionRequest) -> tuple[tuple[str, str], SiteResolution]:
            async with semaphores[request.policy.source_id]:
                return _site_request_key(request), await resolve_site(
                    request,
                    fetcher=self._fetcher,
                )

        results = await asyncio.gather(*(resolve(request) for request in site_requests))
        return dict(results)


def application_channel_work_items(
    *,
    processed_payload: JsonObject,
    raw_rows: tuple[StoredRawRecord, ...],
) -> tuple[ApplicationChannelWorkItem, ...]:
    results = processed_payload.get("results")
    if not isinstance(results, list):
        raise ValueError("processed results payload must contain results list")
    raw_by_id = {row.raw_record_id: row.payload for row in raw_rows}
    work_items: list[ApplicationChannelWorkItem] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("processed results must contain row objects")
        raw_record_id = result.get("raw_record_id")
        if not isinstance(raw_record_id, int):
            raise ValueError("processed result row is missing raw_record_id")
        raw_record = raw_by_id.get(raw_record_id)
        if raw_record is None:
            raise ValueError(f"raw record row does not exist: {raw_record_id}")
        listing = listing_from_record(raw_record)
        if _has_application_channel_seed(listing):
            work_items.append(ApplicationChannelWorkItem(raw_record_id=raw_record_id, listing=listing))
    return tuple(work_items)


def application_channel_summary(
    *,
    total_work_items: int,
    result: ApplicationChannelRunResult,
) -> JsonObject:
    return {
        "total_application_channel_work_items": total_work_items,
        "attempted": result.attempted,
        "resolved": result.resolved,
        "failed": result.failed,
        "updated": result.updated,
    }


def _channels_for_listing(
    listing: RawListing,
    *,
    site_url: str | None,
    site_source: str,
    site_resolution: SiteResolution | None,
) -> list[dict[str, str]]:
    channels: list[dict[str, str]] = []
    seed = application_channel_seed(listing)
    if site_resolution is not None and site_resolution.channel is not None:
        channels.append(site_resolution.channel)
    elif site_url:
        channels.append(
            {
                "type": "company_site",
                "label": "Site",
                "url": site_url,
                "status": "source_provided",
                "source": site_source,
            }
        )

    profile_url = _aggregator_profile_url(listing)
    if profile_url:
        channels.append(
            {
                "type": "aggregator_company_profile",
                "label": "Profile",
                "url": profile_url,
                "status": "source_provided",
                "source": _seed_source(seed.aggregator_source, "company_profile_url"),
            }
        )
    return channels


def _unique_profile_site_requests(
    work_items: tuple[ApplicationChannelWorkItem, ...],
) -> tuple[ProfileSiteResolutionRequest, ...]:
    seen: set[tuple[str, str]] = set()
    requests: list[ProfileSiteResolutionRequest] = []
    for item in work_items:
        seed = application_channel_seed(item.listing)
        if _company_site_url(item.listing) is not None:
            continue
        profile_url = _aggregator_profile_url(item.listing)
        if profile_url is None or seed.resolution_policy is None:
            continue
        request = ProfileSiteResolutionRequest(profile_url=profile_url, policy=seed.resolution_policy)
        key = _profile_site_request_key(request)
        if key in seen:
            continue
        seen.add(key)
        requests.append(request)
    return tuple(requests)


def _unique_site_requests(
    work_items: tuple[ApplicationChannelWorkItem, ...],
    profile_site_results: dict[tuple[str, str], ProfileSiteResolution],
) -> tuple[SiteResolutionRequest, ...]:
    seen: set[tuple[str, str]] = set()
    requests: list[SiteResolutionRequest] = []
    for item in work_items:
        seed = application_channel_seed(item.listing)
        site_url = _site_url_for_listing(item.listing, profile_site_results)
        if site_url is None or seed.resolution_policy is None:
            continue
        request = SiteResolutionRequest(
            site_url=site_url,
            policy=seed.resolution_policy,
            channel_source=_site_source_for_listing(item.listing, profile_site_results),
        )
        key = _site_request_key(request)
        if key in seen:
            continue
        seen.add(key)
        requests.append(request)
    return tuple(requests)


def _profile_site_request_key(request: ProfileSiteResolutionRequest) -> tuple[str, str]:
    return (request.profile_url, request.policy.source_id)


def _site_request_key(request: SiteResolutionRequest) -> tuple[str, str]:
    return (request.site_url, request.policy.source_id)


def _site_result_key(listing: RawListing, site_url: str | None) -> tuple[str, str] | None:
    if site_url is None:
        return None
    policy = application_channel_seed(listing).resolution_policy
    if policy is None:
        return None
    return (site_url, policy.source_id)


def _site_url_for_listing(
    listing: RawListing,
    profile_site_results: dict[tuple[str, str], ProfileSiteResolution],
) -> str | None:
    site_url = _company_site_url(listing)
    if site_url is not None:
        return site_url
    profile_result = _profile_site_result_for_listing(listing, profile_site_results)
    if profile_result is None:
        return None
    return profile_result.site_url


def _site_source_for_listing(
    listing: RawListing,
    profile_site_results: dict[tuple[str, str], ProfileSiteResolution],
) -> str:
    seed = application_channel_seed(listing)
    if _company_site_url(listing) is not None:
        return _seed_source(seed.aggregator_source, "company_site_url")
    profile_result = _profile_site_result_for_listing(listing, profile_site_results)
    if profile_result is not None and profile_result.site_url is not None:
        return _seed_source(seed.aggregator_source, "company_profile_official_site")
    return _seed_source(seed.aggregator_source, "company_site_url")


def _profile_site_result_for_listing(
    listing: RawListing,
    profile_site_results: dict[tuple[str, str], ProfileSiteResolution],
) -> ProfileSiteResolution | None:
    profile_key = _profile_site_result_key(listing)
    if profile_key is None:
        return None
    return profile_site_results.get(profile_key)


def _profile_site_result_key(listing: RawListing) -> tuple[str, str] | None:
    profile_url = _aggregator_profile_url(listing)
    if profile_url is None:
        return None
    policy = application_channel_seed(listing).resolution_policy
    if policy is None:
        return None
    return (profile_url, policy.source_id)


def _company_site_url(listing: RawListing) -> str | None:
    return clean_http_url(application_channel_seed(listing).company_site_url)


def _aggregator_profile_url(listing: RawListing) -> str | None:
    return clean_http_url(application_channel_seed(listing).aggregator_profile_url)


def _seed_source(source_id: str | None, field: str) -> str:
    if source_id:
        return f"{source_id}.{field}"
    return f"aggregator.{field}"


def _has_application_channel_seed(listing: RawListing) -> bool:
    return application_channel_seed(listing).has_channel


def _source_semaphores(
    requests: tuple[ProfileSiteResolutionRequest, ...] | tuple[SiteResolutionRequest, ...],
    request_concurrency_by_source: int,
) -> dict[str, asyncio.Semaphore]:
    return {
        request.policy.source_id: asyncio.Semaphore(request_concurrency_by_source)
        for request in requests
    }
