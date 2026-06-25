"""Contract-first VK career source."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from job_harness.v2.contracts import (
    AttemptEvidence,
    DetailEnrichmentScraper,
    RawListing,
    RemoteMode,
    RequiredParserFixtures,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceSearchParseResult,
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
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_build_vk_api_url(use_remote_collection_hint=_use_remote_collection_hint(request)),
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _extract_json_object(response.body)
        items = payload.get("results")
        total_count = payload.get("count")
        next_url = payload.get("next")
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

        next_request = (
            SourceFetchRequest(
                source_id=_request.source_id,
                query_variant=_request.query_variant,
                url=next_url,
            )
            if isinstance(next_url, str) and next_url
            else None
        )
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_vk_listing(item) for item in items if isinstance(item, dict)),
            next_request=next_request,
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
    return request.remote_mode == RemoteMode.COMPATIBLE_REMOTE


def _build_vk_api_url(*, use_remote_collection_hint: bool) -> str:
    params = {"limit": str(_PAGE_LIMIT)}
    if use_remote_collection_hint:
        params["remote"] = "true"
    return f"{_API_URL}?{urlencode(params)}"


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
