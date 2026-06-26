"""Contract-first AppFollow career source backed by Lever postings."""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

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
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_SOURCE_ID = "career:appfollow"
_COMPANY = "AppFollow"
_BOARD_URL = "https://api.lever.co/v0/postings/appfollow?mode=json"
_SECTION_LABEL_RE = re.compile(
    r"<(?P<tag>b|h[1-6]|strong)[^>]*>(?P<label>.*?)</(?P=tag)>",
    re.I | re.S,
)
_JSON_LD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(?P<payload>.*?)</script>",
    re.I | re.S,
)
_REQUIREMENTS_MARKERS = (
    "about you",
    "experience",
    "languages",
    "nice to have",
    "qualification",
    "requirements",
    "skills",
    "tools",
)
_STRONG_SECTION_LABELS = frozenset(
    {
        "about the role",
        "about you",
        "benefits we offer",
        "hiring process",
        "it would be nice to have",
    }
)


class AppFollowCareerSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(_SOURCE_ID)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(_SOURCE_ID)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_BOARD_URL,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _json_list(response.body)
        if not payload:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        listings = tuple(_listing(item) for item in payload if isinstance(item, dict))
        if not listings:
            raise ValueError("AppFollow Lever payload contains no valid postings")
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)

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
        payload = _json_ld_job_posting(response.body)
        description_html = _text(payload.get("description")).strip()
        description = _html_to_text(description_html)
        if description is None:
            raise ValueError("AppFollow detail page does not contain JobPosting description")

        sections = _html_sections(description_html)
        employment_type = _text(payload.get("employmentType")).strip()
        remote = "remote" in employment_type.casefold()
        location_text = _job_posting_location(payload) or listing.location_text
        remote_global = remote and _is_global_location(location_text)
        country = _job_posting_country(payload)
        detail_raw = {
            "datePosted": _text(payload.get("datePosted")).strip() or None,
            "employmentType": employment_type or None,
            "jobLocation": payload.get("jobLocation"),
        }
        return replace(
            listing,
            company=_job_posting_company(payload) or listing.company,
            country=country,
            location_text=location_text,
            posted_at=_text(payload.get("datePosted")).strip() or listing.posted_at,
            remote_in_country=True if remote and not remote_global else listing.remote_in_country,
            remote_global=remote_global if remote else listing.remote_global,
            description=description,
            requirements=_requirements(sections),
            additional_sections=sections,
            raw_text=_join_text(listing.raw_text, description),
            raw={**listing.raw, "detail": detail_raw},
        )


def _json_list(body: str) -> list[object]:
    value = json.loads(body)
    if not isinstance(value, list):
        raise ValueError("AppFollow Lever response is not a JSON list")
    return value


def _listing(job: dict[str, Any]) -> RawListing:
    source_listing_id = _text(job.get("id")).strip()
    title = _text(job.get("text")).strip()
    categories = _categories(job.get("categories"))
    location_text = _location_text(categories)
    commitment = _text(categories.get("commitment")).strip()
    department = _text(categories.get("department")).strip()
    team = _text(categories.get("team")).strip()
    workplace_type = _text(job.get("workplaceType")).strip()
    remote = _is_remote(workplace_type=workplace_type, commitment=commitment)
    remote_global = remote and _is_global_location(location_text)

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=_text(job.get("hostedUrl")).strip(),
        source=_SOURCE_ID,
        company=_COMPANY,
        country=None,
        city=None,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_timestamp_date(job.get("createdAt")),
        remote_in_country=remote and not remote_global,
        remote_global=remote_global,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, location_text, department, team, commitment, workplace_type),
        raw=_raw_facts(
            source_listing_id=job.get("id"),
            apply_url=job.get("applyUrl"),
            categories=categories,
            lever_country=job.get("country"),
            workplace_type=workplace_type,
            created_at=job.get("createdAt"),
            location_text=location_text,
            remote=remote,
        ),
    )


def _raw_facts(
    *,
    source_listing_id: object,
    apply_url: object,
    categories: dict[str, object],
    lever_country: object,
    workplace_type: str,
    created_at: object,
    location_text: str | None,
    remote: bool,
) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": source_listing_id,
        "apply_url": apply_url,
        "categories": categories,
        "lever_country": lever_country,
        "workplace_type": workplace_type or None,
        "created_at": created_at,
    }
    if remote:
        raw["work_format"] = "remote"
        if location_text:
            raw["remote_locations"] = location_text
    return raw


def _json_ld_job_posting(body: str) -> dict[str, Any]:
    for match in _JSON_LD_RE.finditer(body):
        payload = _json_ld_value(match.group("payload"))
        for candidate in _json_ld_candidates(payload):
            type_value = candidate.get("@type")
            if type_value == "JobPosting" or (
                isinstance(type_value, list) and "JobPosting" in type_value
            ):
                return candidate
    raise ValueError("AppFollow detail page does not contain JobPosting JSON-LD")


def _json_ld_value(payload: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return json.loads(html.unescape(payload))


def _json_ld_candidates(value: object) -> tuple[dict[str, Any], ...]:
    if isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            return tuple(item for item in graph if isinstance(item, dict))
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, dict))
    return ()


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.replace("\\n", " ").split())
        if text:
            self.parts.append(text)

    def text(self) -> str | None:
        if not self.parts:
            return None
        return "\n".join(self.parts)


def _html_to_text(value: str) -> str | None:
    if not value.strip():
        return None
    collector = _TextCollector()
    collector.feed(html.unescape(value))
    return collector.text()


def _html_sections(value: str) -> dict[str, str]:
    raw_html = html.unescape(value)
    labels = tuple(match for match in _SECTION_LABEL_RE.finditer(raw_html) if _section_label(match))
    sections: dict[str, str] = {}
    for index, match in enumerate(labels):
        label = _section_label(match)
        if label is None:
            continue
        body_start = match.end()
        body_end = labels[index + 1].start() if index + 1 < len(labels) else len(raw_html)
        body = _html_to_text(raw_html[body_start:body_end])
        if body:
            sections[label] = body
    return sections


def _section_label(match: re.Match[str]) -> str | None:
    tag = match.group("tag").casefold()
    label = (_html_to_text(match.group("label")) or "").rstrip(":").strip()
    if not label:
        return None
    normalized = label.casefold()
    if (
        tag == "strong"
        and not match.group("label").strip().endswith(":")
        and normalized not in _STRONG_SECTION_LABELS
    ):
        return None
    return label


def _requirements(sections: dict[str, str]) -> str | None:
    parts = [
        body
        for label, body in sections.items()
        if any(marker in label.casefold() for marker in _REQUIREMENTS_MARKERS)
    ]
    return "\n".join(parts) or None


def _categories(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return dict(value)


def _location_text(categories: dict[str, object]) -> str | None:
    location = _text(categories.get("location")).strip()
    if location:
        return location
    all_locations = categories.get("allLocations")
    if not isinstance(all_locations, list):
        return None
    location_names = tuple(_text(item).strip() for item in all_locations if _text(item).strip())
    return ", ".join(location_names) or None


def _job_posting_company(payload: dict[str, Any]) -> str | None:
    organization = payload.get("hiringOrganization")
    if not isinstance(organization, dict):
        return None
    return _text(organization.get("name")).strip() or None


def _job_posting_location(payload: dict[str, Any]) -> str | None:
    address = _job_posting_address(payload)
    if address is None:
        return None
    parts = (
        _text(address.get("addressLocality")).strip(),
        _text(address.get("addressRegion")).strip(),
    )
    return ", ".join(part for part in parts if part) or None


def _job_posting_country(payload: dict[str, Any]) -> str | None:
    address = _job_posting_address(payload)
    if address is None:
        return None
    country = _text(address.get("addressCountry")).strip()
    return country or None


def _job_posting_address(payload: dict[str, Any]) -> dict[str, Any] | None:
    location = payload.get("jobLocation")
    if not isinstance(location, dict):
        return None
    address = location.get("address")
    if not isinstance(address, dict):
        return None
    return address


def _is_remote(*, workplace_type: str, commitment: str) -> bool:
    return workplace_type.casefold() == "remote" or "remote" in commitment.casefold()


def _is_global_location(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"anywhere", "global", "remote", "worldwide"}


def _timestamp_date(value: object) -> str | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, UTC).date().isoformat()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
