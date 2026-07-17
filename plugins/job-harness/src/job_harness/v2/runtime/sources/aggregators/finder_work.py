"""Contract-first Finder.work aggregator source."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode, urlparse

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
from job_harness.v2.runtime.sources._html import html_to_text
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_API_URL = "https://api.finder.work/api/v1/vacancies"
_PUBLIC_BASE_URL = "https://finder.work/vacancies"
_PUBLIC_PATH_PART_COUNT = 2
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_EXPERIENCE_GRADE_MAP = {
    "no_experience": "junior",
    "one_year_more": "junior",
    "one_three_years": "middle",
    "three_years_more": "senior",
}


class FinderWorkSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("finder_work")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("finder_work")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=f"{_API_URL}?{urlencode(_search_params(query_variant))}",
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _json_object(response.body)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("Finder.work payload is malformed")
        if not items:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        listings = tuple(
            listing
            for item in items
            if isinstance(item, dict)
            for listing in (_listing_from_item(item),)
            if listing is not None
        )
        if not listings:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)

    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        listing_id = listing.source_listing_id or _listing_id_from_public_url(listing.url)
        if not listing_id:
            raise ValueError("Finder.work detail request requires a canonical public vacancy URL")
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=f"{_API_URL}/{listing_id}",
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        item = _json_object(response.body)
        description = html_to_text(_text(item.get("description")))
        if description is None:
            raise ValueError("Finder.work detail payload does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
        )


def _listing_id_from_public_url(url: str) -> str | None:
    parsed = urlparse(url)
    parts = tuple(part for part in parsed.path.split("/") if part)
    if parsed.netloc.lower() not in {"finder.work", "www.finder.work"}:
        return None
    if (
        len(parts) != _PUBLIC_PATH_PART_COUNT
        or parts[0] != "vacancies"
        or not parts[1].isdigit()
    ):
        return None
    return parts[1]


def _search_params(query_variant: str) -> dict[str, str]:
    return {"search": query_variant}


def _listing_from_item(item: dict[str, Any]) -> RawListing | None:
    title = _text(item.get("title")).strip()
    item_id = item.get("id")
    if not title or item_id is None:
        return None

    company_data = item.get("company")
    company = _text(company_data.get("title")).strip() if isinstance(company_data, dict) else ""
    locations = item.get("locations")
    location_names: list[str] = []
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, dict):
                name = _text(location.get("name")).strip()
                if name:
                    location_names.append(name)
    location_text = ", ".join(location_names) or None
    city = location_names[0] if location_names else None

    external_url = item.get("external_url")
    raw: dict[str, object] = {"id": item_id}
    if isinstance(external_url, dict):
        value = _text(external_url.get("value")).strip()
        label = _text(external_url.get("label")).strip()
        if value:
            raw["external_url"] = value
        if label:
            raw["external_source"] = label

    salary_min = _positive_int(item.get("salary_from"))
    salary_max = _positive_int(item.get("salary_to"))
    currency = _text(item.get("currency_symbol")).strip() or None
    description = _plain_text(item.get("short_description"))

    return RawListing(
        source_listing_id=str(item_id),
        title=title,
        url=f"{_PUBLIC_BASE_URL}/{item_id}",
        source="finder_work",
        company=company or None,
        country=None,
        city=city,
        location_text=location_text,
        salary_text=_salary_text(salary_min, salary_max, currency),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=currency,
        posted_at=_text(item.get("publication_at")).strip() or None,
        remote_in_country=bool(item.get("distant_work")) or None,
        remote_global=None,
        relocation=None,
        native_grade=_native_grade(_text(item.get("experience")).strip()),
        description=description,
        requirements=None,
        skills=(),
        raw_text=_join_text(title, company, location_text, description),
        raw=raw,
    )


def _json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Finder.work response is not a JSON object")
    return value


def _native_grade(value: str) -> str | None:
    if not value:
        return None
    return _EXPERIENCE_GRADE_MAP.get(value, value)


def _plain_text(value: object) -> str | None:
    text = _HTML_TAG_RE.sub(" ", _text(value))
    normalized = " ".join(text.split())
    return normalized or None


def _salary_text(salary_min: int | None, salary_max: int | None, currency: str | None) -> str | None:
    if salary_min is not None and salary_max is not None:
        return f"{salary_min} - {salary_max} {currency or ''}".strip()
    if salary_min is not None:
        return f"from {salary_min} {currency or ''}".strip()
    if salary_max is not None:
        return f"to {salary_max} {currency or ''}".strip()
    return None


def _positive_int(value: object) -> int | None:
    if not isinstance(value, int) or value <= 0:
        return None
    return value


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
