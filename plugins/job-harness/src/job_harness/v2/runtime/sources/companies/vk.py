"""Contract-first VK career source."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import urlencode

from job_harness.v2.contracts import (
    AttemptEvidence,
    RawListing,
    RequiredParserFixtures,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
    SourceSearchParseResult,
)
from job_harness.v2.runtime.sources._html import ScriptCollector
from job_harness.v2.runtime.sources._url import absolute_url
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://team.vk.company/vacancy/"
_SPECIALTIES = {
    "qa": "284",
    "тестировщик": "284",
    "тестирование": "284",
}


class VKCareerSource(SourceScraper):
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
                url=_build_vk_url(query_variant),
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _extract_next_data(response.body)
        page_props = payload.get("props", {}).get("pageProps", {})
        if not isinstance(page_props, dict):
            raise ValueError("VK __NEXT_DATA__ pageProps is malformed")

        items = page_props.get("initialVacancies")
        total_count = page_props.get("initialTotalCount")
        if not isinstance(items, list) or not isinstance(total_count, int):
            raise ValueError("VK vacancies payload is malformed")
        if total_count == 0 and not items:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_vk_listing(item) for item in items if isinstance(item, dict)),
        )


def _build_vk_url(query: str) -> str:
    lowered = query.casefold()
    specialty = next((value for key, value in _SPECIALTIES.items() if key in lowered), None)
    params = {"specialty": specialty} if specialty else {"search": query}
    return f"{_BASE_URL}?{urlencode(params)}"


def _extract_next_data(body: str) -> dict[str, Any]:
    collector = ScriptCollector()
    collector.feed(body)
    for attrs, text in collector.scripts:
        if attrs.get("id") != "__NEXT_DATA__":
            continue
        value = json.loads(html.unescape(text))
        if isinstance(value, dict):
            return value
    raise ValueError("VK response does not contain __NEXT_DATA__")


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
        country="RU",
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
