"""Contract-first VK career source."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from job_harness.v2.contracts import (
    AttemptEvidence,
    DetailEnrichmentScraper,
    RawListing,
    RequiredParserFixtures,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceSearchParseResult,
    WorkFormat,
)
from job_harness.v2.runtime.sources._html import ClassTextCollector
from job_harness.v2.runtime.sources._url import absolute_url
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://team.vk.company/vacancy/"
_API_URL = "https://team.vk.company/career/api/v2/vacancies/"
_PAGE_LIMIT = 25


class VKCareerSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("career:vk")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("career:vk")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return (
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=request.query_variants[0],
                url=_build_vk_api_url(use_remote_collection_hint=_use_remote_collection_hint(request)),
            ),
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _extract_json_object(response.body)
        items = payload.get("results")
        total_count = payload.get("count")
        if not isinstance(items, list) or not isinstance(total_count, int):
            raise ValueError("VK vacancies API payload is malformed")
        if total_count == 0 and not items:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        if not items:
            raise ValueError("VK vacancies API returned an empty page before completion")

        parallel_requests = _parallel_page_requests(
            request=_request,
            total_count=total_count,
            source_limit=self.descriptor.source_limit,
        )
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_vk_listing(item) for item in items if isinstance(item, dict)),
            parallel_requests=parallel_requests,
        )

    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=listing.url,
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        collector = ClassTextCollector(tag_name="div", class_name="article")
        collector.feed(response.body)
        description = collector.text()
        if description is None:
            raise ValueError("VK detail page does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
        )


def _use_remote_collection_hint(request: SearchRequest) -> bool:
    return WorkFormat.REMOTE in request.work_formats


def _build_vk_api_url(*, use_remote_collection_hint: bool) -> str:
    params = {"limit": str(_PAGE_LIMIT)}
    if use_remote_collection_hint:
        params["remote"] = "true"
    return f"{_API_URL}?{urlencode(params)}"


def _parallel_page_requests(
    *,
    request: SourceFetchRequest,
    total_count: int,
    source_limit: int,
) -> tuple[SourceFetchRequest, ...]:
    current_offset = _request_offset(request.url)
    limit = _request_limit(request.url)
    if current_offset != 0 or limit < 1 or total_count <= limit:
        return ()
    max_records = min(total_count, source_limit)
    return tuple(
        SourceFetchRequest(
            source_id=request.source_id,
            query_variant=request.query_variant,
            url=_page_url(request.url, offset=offset),
        )
        for offset in range(current_offset + limit, max_records, limit)
    )


def _request_offset(url: str) -> int:
    values = parse_qs(urlparse(url).query).get("offset")
    if not values:
        return 0
    return _positive_int_text(values[0]) or 0


def _request_limit(url: str) -> int:
    values = parse_qs(urlparse(url).query).get("limit")
    if not values:
        return _PAGE_LIMIT
    return _positive_int_text(values[0]) or _PAGE_LIMIT


def _page_url(url: str, *, offset: int) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["offset"] = [str(offset)]
    normalized = {key: values[-1] for key, values in query.items() if values}
    return urlunparse(parsed._replace(query=urlencode(normalized)))


def _positive_int_text(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _extract_json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("VK response JSON must be an object")
    return value


def _vk_listing(item: dict[str, Any]) -> RawListing:
    source_listing_id = str(item.get("id") or "")
    title = _str_value(item.get("title")).strip()
    company = _nested_str(item, "group", "name")
    city = _nested_str(item, "town", "name")
    work_format = _str_value(item.get("work_format")).strip()
    skills = tuple(
        _str_value(tag.get("name"))
        for tag in item.get("tags", [])
        if isinstance(tag, dict) and _str_value(tag.get("name"))
    )
    location_text = ", ".join(part for part in (city, work_format) if part)

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=absolute_url(_BASE_URL, f"/vacancy/{source_listing_id}/"),
        source="career:vk",
        company=company or "VK",
        country="Россия",
        city=city or None,
        location_text=location_text or None,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=bool(item.get("remote")) or "дистан" in work_format.casefold(),
        remote_global=None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        skills=skills,
        raw_text=_join_text(title, company, location_text, " ".join(skills)),
        raw={
            "id": item.get("id"),
            "work_format": work_format,
            "specialty": item.get("specialty"),
        },
    )


def _nested_str(value: dict[str, Any], key: str, nested_key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, dict):
        return ""
    return _str_value(nested.get(nested_key)).strip()


def _str_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
