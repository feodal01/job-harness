"""Contract-first Habr Career source."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

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
)
from job_harness.v2.contracts.enums import Grade
from job_harness.v2.runtime.sources._html import ClassTextCollector, ScriptCollector, html_to_text
from job_harness.v2.runtime.sources._url import absolute_url, update_query
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://career.habr.com/vacancies"
_DETAIL_BASE_URL = "https://career.habr.com"


class HabrCareerSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("habr_career")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("habr_career")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        grade_values = request.grades or (None,)
        fetch_requests: list[SourceFetchRequest] = []
        for query_variant in request.query_variants:
            for grade in grade_values:
                params = {"q": query_variant, "type": "all"}
                if grade is not None:
                    params["qualification"] = _habr_grade(grade)
                if request.salary_from is not None:
                    params["salary"] = str(request.salary_from)
                fetch_requests.append(
                    SourceFetchRequest(
                        source_id=self.descriptor.source_id,
                        query_variant=query_variant,
                        url=f"{_BASE_URL}?{urlencode(params)}",
                    )
                )
        return tuple(fetch_requests)

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _extract_habr_payload(response.body)
        vacancy_block = payload.get("vacancies")
        if not isinstance(vacancy_block, dict):
            raise ValueError("Habr payload does not contain vacancies object")

        items = vacancy_block.get("list")
        meta = vacancy_block.get("meta")
        if not isinstance(items, list) or not isinstance(meta, dict):
            raise ValueError("Habr vacancies object is malformed")

        total_results = _int_value(meta.get("totalResults"))
        if total_results == 0 and not items:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        listings = tuple(_habr_listing(item) for item in items if isinstance(item, dict))
        next_request = _next_page_request(meta=meta, request=request)
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=listings,
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
        description = _habr_detail_description(response.body)
        if description is None:
            raise ValueError("Habr detail page does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
        )


def _habr_detail_description(body: str) -> str | None:
    collector = ScriptCollector()
    collector.feed(body)
    for _attrs, text in collector.scripts:
        if not text.startswith("{") or '"vacancy"' not in text:
            continue
        data = json.loads(text)
        vacancy = data.get("vacancy")
        if not isinstance(vacancy, dict):
            continue
        raw_description = vacancy.get("description")
        if isinstance(raw_description, str) and raw_description.strip():
            parsed = _habr_description_text(raw_description, vacancy)
            if parsed:
                return parsed

    dom_collector = ClassTextCollector(
        tag_name="div",
        class_name="vacancy-description__text",
    )
    dom_collector.feed(body)
    return dom_collector.text()


_MIN_PARSED_DESCRIPTION_CHARS = 120


def _habr_description_text(raw_description: str, vacancy: dict[str, object]) -> str | None:
    parsed = html_to_text(raw_description)
    banner = _str_value(vacancy.get("bannerDescription")).strip()
    image_urls = _image_urls_from_html(raw_description)
    sections: list[str] = []
    if parsed and len(parsed) >= _MIN_PARSED_DESCRIPTION_CHARS:
        sections.append(parsed)
    elif banner:
        sections.append(banner)
    elif parsed:
        sections.append(parsed)
    if image_urls:
        sections.append("Vacancy details are published as images:\n" + "\n".join(image_urls))
    if not sections:
        return None
    return "\n\n".join(sections)


def _image_urls_from_html(value: str) -> tuple[str, ...]:
    urls: list[str] = []
    for match in re.finditer(r"""<img[^>]+src=["']([^"']+)["']""", value, flags=re.IGNORECASE):
        url = match.group(1).strip()
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


def _extract_habr_payload(body: str) -> dict[str, Any]:
    collector = ScriptCollector()
    collector.feed(body)
    for _attrs, text in collector.scripts:
        if text.startswith("{") and '"vacancies"' in text:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
    raise ValueError("Habr response does not contain vacancy JSON payload")


def _habr_listing(item: dict[str, Any]) -> RawListing:
    source_listing_id = str(item.get("id") or "")
    href = _str_value(item.get("href"))
    title = _str_value(item.get("title")).strip()
    company = _nested_str(item, "company", "title")
    salary = item.get("salary") if isinstance(item.get("salary"), dict) else {}
    raw_locations = item.get("locations")
    locations = raw_locations if isinstance(raw_locations, list) else []
    raw_skills = item.get("skills")
    if not isinstance(raw_skills, list):
        raise ValueError("Habr vacancy item has malformed skills")
    skills = tuple(
        _str_value(skill.get("title"))
        for skill in raw_skills
        if isinstance(skill, dict) and _str_value(skill.get("title"))
    )
    city_values = tuple(
        _str_value(location.get("title"))
        for location in locations
        if isinstance(location, dict) and _str_value(location.get("title"))
    )
    posted_at = _nested_str(item, "publishedDate", "date") or None
    salary_text = _str_value(salary.get("formatted")).strip() if isinstance(salary, dict) else ""
    salary_currency = _currency(_str_value(salary.get("currency"))) if isinstance(salary, dict) else None

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=absolute_url(_DETAIL_BASE_URL, href),
        source="habr_career",
        company=company or None,
        country="Россия",
        city=city_values[0] if city_values else None,
        location_text=", ".join(city_values) or None,
        salary_text=salary_text or None,
        salary_min=_int_value(salary.get("from")) if isinstance(salary, dict) else None,
        salary_max=_int_value(salary.get("to")) if isinstance(salary, dict) else None,
        salary_currency=salary_currency,
        posted_at=posted_at,
        remote_in_country=bool(item.get("remoteWork")),
        remote_global=None,
        relocation=None,
        native_grade=_grade_value(_str_value(item.get("qualification"))),
        description=None,
        requirements=None,
        skills=skills,
        raw_text=_join_text(title, company, ", ".join(city_values), salary_text, " ".join(skills)),
        raw={
            "id": item.get("id"),
            "href": href,
            "publishedDate": item.get("publishedDate"),
            "qualification": item.get("qualification"),
        },
    )


def _next_page_request(
    *,
    meta: dict[str, Any],
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    current_page = _int_value(meta.get("currentPage")) or 1
    total_pages = _int_value(meta.get("totalPages")) or 0
    if current_page >= total_pages:
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=update_query(request.url, {"page": str(current_page + 1)}),
        method=request.method,
        headers=dict(request.headers),
        body=request.body,
    )


def _habr_grade(grade: Grade) -> str:
    return grade.value


def _grade_value(value: str) -> str | None:
    lowered = value.strip().lower()
    if not lowered:
        return None
    if lowered == "senior":
        return "senior"
    if lowered == "middle":
        return "middle"
    if lowered == "junior":
        return "junior"
    if lowered == "lead":
        return "lead"
    return lowered


def _currency(value: str) -> str | None:
    normalized = value.strip().upper()
    if not normalized:
        return None
    if normalized == "RUR":
        return "RUB"
    return normalized


def _nested_str(value: dict[str, Any], key: str, nested_key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, dict):
        return ""
    return _str_value(nested.get(nested_key)).strip()


def _str_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_value(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
