"""Contract-first getmatch.ru aggregator source."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
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
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_SPECIALIZATIONS_URL = "https://getmatch.ru/api/specializations"
_OFFERS_URL = "https://getmatch.ru/api/offers"
_PUBLIC_BASE_URL = "https://getmatch.ru"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PENDING_SLUGS_HEADER = "x-getmatch-pending-slugs"
_SEEN_IDS_HEADER = "x-getmatch-seen-ids"
_COUNTRY_BY_TEXT = {
    "арм": "AM",
    "armen": "AM",
    "азер": "AZ",
    "azer": "AZ",
    "беларус": "BY",
    "belarus": "BY",
    "казахстан": "KZ",
    "kazakh": "KZ",
    "киргиз": "KG",
    "кыргыз": "KG",
    "kyrgyz": "KG",
    "молдов": "MD",
    "moldov": "MD",
    "росси": "RU",
    "russia": "RU",
    "таджик": "TJ",
    "tajik": "TJ",
    "узбек": "UZ",
    "uzbek": "UZ",
    "туркмен": "TM",
    "turkmen": "TM",
    "грузи": "GE",
    "georgia": "GE",
    "украин": "UA",
    "ukrain": "UA",
}


class GetmatchSource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("getmatch")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("getmatch")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_SPECIALIZATIONS_URL,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        if request.url.startswith(_SPECIALIZATIONS_URL):
            return self._parse_specializations(response, request)
        if request.url.startswith(_OFFERS_URL):
            return self._parse_offers(response, request)
        raise ValueError("getmatch response URL is not recognized")

    def _parse_specializations(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _json_array(response.body)
        matched_slugs = _matching_specialization_slugs(payload, request.query_variant)
        if matched_slugs:
            slugs: list[str | None] = list(matched_slugs)
        else:
            slugs = [None]
        first_slug, *pending_slugs = slugs
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=(),
            next_request=_offers_request(
                query_variant=request.query_variant,
                specialization_slug=first_slug,
                pending_slugs=pending_slugs,
            ),
        )

    def _parse_offers(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _json_object(response.body)
        offers = payload.get("offers")
        if not isinstance(offers, list):
            raise ValueError("getmatch offers payload is malformed")

        seen_ids = _header_values(request, _SEEN_IDS_HEADER)
        listings: list[RawListing] = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            listing = _listing_from_offer(offer)
            if listing is None:
                continue
            listing_id = listing.source_listing_id
            if not listing_id or listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            listings.append(listing)

        pending_slugs = _header_values(request, _PENDING_SLUGS_HEADER)
        if not listings and not pending_slugs:
            if seen_ids:
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(),
                    evidence=AttemptEvidence(multi_step_terminal=True),
                )
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        next_request = None
        if pending_slugs:
            next_slug, *remaining_slugs = list(pending_slugs)
            next_request = _offers_request(
                query_variant=request.query_variant,
                specialization_slug=next_slug,
                pending_slugs=remaining_slugs,
                seen_ids=seen_ids,
            )

        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(listings),
            next_request=next_request,
        )


def _offers_request(
    *,
    query_variant: str,
    specialization_slug: str | None,
    pending_slugs: Sequence[str | None] = (),
    seen_ids: set[str] | None = None,
) -> SourceFetchRequest:
    params = {
        "sa": "any",
        "p": "1",
        "offset": "0",
        "limit": "100",
        "pa": "all",
    }
    if specialization_slug:
        params["sp"] = specialization_slug
    headers: dict[str, str] = {}
    if pending_slugs:
        headers[_PENDING_SLUGS_HEADER] = ",".join("" if slug is None else slug for slug in pending_slugs)
    if seen_ids:
        headers[_SEEN_IDS_HEADER] = ",".join(sorted(seen_ids))
    return SourceFetchRequest(
        source_id="getmatch",
        query_variant=query_variant,
        url=f"{_OFFERS_URL}?{urlencode(params)}",
        headers=headers,
    )


def _matching_specialization_slugs(payload: list[Any], query: str) -> list[str]:
    query_tokens = _query_tokens(query)
    if not query_tokens:
        return []

    slugs: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        category = item.get("category") or {}
        haystack = " ".join(
            str(value or "")
            for value in (
                item.get("name"),
                item.get("slug"),
                category.get("name"),
                category.get("slug"),
            )
        ).casefold()
        haystack_tokens = set(re.findall(r"[a-zа-яё0-9]+", haystack))
        if (
            query_tokens <= haystack_tokens
            if len(query_tokens) > 1
            else bool(query_tokens & haystack_tokens)
        ):
            slug = item.get("slug")
            if isinstance(slug, str) and slug:
                slugs.append(slug)
    return slugs


def _listing_from_offer(offer: dict[str, Any]) -> RawListing | None:
    title = _text(offer.get("position")).strip()
    url_path = _text(offer.get("url")).strip()
    if not title or not url_path:
        return None

    offer_id = offer.get("id")
    if offer_id is None:
        return None

    company_data = offer.get("company") or {}
    company = _text(company_data.get("name")).strip() if isinstance(company_data, dict) else ""
    location_requirements = offer.get("location_requirements")
    location_items = offer.get("location_items")
    location_text = _location_text(location_items)
    country_text = _country_text(location_requirements)
    remote = _is_remote(location_requirements)
    city = _city(location_requirements, location_items)
    skills = _skills(offer.get("skills_objects"))
    description = _plain_text(offer.get("offer_description"))
    salary_text = _text(offer.get("salary_description")).strip() or _format_salary(offer)

    return RawListing(
        source_listing_id=str(offer_id),
        title=title,
        url=_absolute_url(url_path),
        source="getmatch",
        company=company or None,
        country=_country_from_text(country_text) or "RU",
        city=city,
        location_text=location_text,
        salary_text=salary_text or None,
        salary_min=_positive_int(offer.get("salary_display_from")),
        salary_max=_positive_int(offer.get("salary_display_to")),
        salary_currency=_text(offer.get("salary_currency")).strip() or None,
        posted_at=_text(offer.get("published_at")).strip() or None,
        remote_in_country=remote,
        remote_global=remote,
        relocation=None,
        native_grade=None,
        description=description,
        requirements=None,
        skills=skills,
        raw_text=_join_text(title, company, location_text, salary_text, description),
        raw={"id": offer_id, "analytics_id": offer.get("analytics_id")},
    )


def _json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("getmatch response is not a JSON object")
    return value


def _json_array(body: str) -> list[Any]:
    value = json.loads(body)
    if not isinstance(value, list):
        raise ValueError("getmatch response is not a JSON array")
    return value


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9+#.]+", query.casefold())
        if len(token) > 1
    }


def _header_values(request: SourceFetchRequest, header_name: str) -> set[str]:
    raw = request.headers.get(header_name, "")
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _location_text(location_items: object) -> str | None:
    if not isinstance(location_items, list):
        return None
    labels = [
        _text(item.get("label")).strip()
        for item in location_items
        if isinstance(item, dict) and _text(item.get("label")).strip()
    ]
    return ", ".join(labels) or None


def _country_text(location_requirements: object) -> str:
    if not isinstance(location_requirements, list):
        return ""
    return " ".join(
        _text(item.get("country")).strip()
        for item in location_requirements
        if isinstance(item, dict)
    )


def _city(location_requirements: object, location_items: object) -> str | None:
    if isinstance(location_requirements, list):
        for item in location_requirements:
            if isinstance(item, dict):
                city = _text(item.get("city")).strip()
                if city:
                    return city
    if isinstance(location_items, list):
        for item in location_items:
            if isinstance(item, dict):
                label = _text(item.get("label")).strip()
                if label:
                    return label.split("(")[0].strip()
    return None


def _is_remote(location_requirements: object) -> bool | None:
    if not isinstance(location_requirements, list):
        return None
    return any(
        isinstance(item, dict) and item.get("format") == "remote" for item in location_requirements
    ) or None


def _skills(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        _text(skill.get("name")).strip()
        for skill in value
        if isinstance(skill, dict) and _text(skill.get("name")).strip()
    )


def _country_from_text(text: str) -> str | None:
    folded = text.casefold()
    for marker, code in _COUNTRY_BY_TEXT.items():
        if marker in folded:
            return code
    return None


def _plain_text(value: object) -> str | None:
    text = _HTML_TAG_RE.sub(" ", _text(value))
    normalized = " ".join(text.split())
    return normalized or None


def _format_salary(offer: dict[str, Any]) -> str | None:
    salary_from = offer.get("salary_display_from")
    salary_to = offer.get("salary_display_to")
    currency = _text(offer.get("salary_currency")).strip()
    if isinstance(salary_from, int) and isinstance(salary_to, int):
        return f"{salary_from} - {salary_to} {currency}".strip()
    if isinstance(salary_from, int):
        return f"from {salary_from} {currency}".strip()
    if isinstance(salary_to, int):
        return f"to {salary_to} {currency}".strip()
    return None


def _absolute_url(url_path: str) -> str:
    if url_path.startswith(("http://", "https://")):
        return url_path
    return f"{_PUBLIC_BASE_URL}{url_path}"


def _positive_int(value: object) -> int | None:
    if not isinstance(value, int) or value <= 0:
        return None
    return value


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
