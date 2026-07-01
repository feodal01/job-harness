"""Contract-first 3Commas career source backed by Ashby posting API JSON."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

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
from job_harness.v2.runtime.sources._html import html_to_text
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/3commas"
_SOURCE_ID = "career:3commas"
_COMPANY = "3Commas"
_SECTION_LABEL_RE = re.compile(
    r"<(?P<tag>h[1-6]|strong)[^>]*>(?P<label>.*?)</(?P=tag)>",
    re.I | re.S,
)
_REMOTE_FORMAT = "remote"
_HYBRID_FORMAT = "hybrid"
_OFFICE_FORMAT = "office"


class ThreeCommasCareerSource(SourceScraper):
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
        payload = _json_object(response.body)
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("3Commas Ashby response jobs field is not a JSON array")
        if not jobs:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        listings = tuple(_listing(job) for job in jobs if isinstance(job, dict) and job.get("isListed") is not False)
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("3Commas Ashby response is not a JSON object")
    return value


def _listing(job: dict[str, Any]) -> RawListing:
    source_listing_id = _text(job.get("id")).strip()
    title = _text(job.get("title")).strip()
    url = _text(job.get("jobUrl")).strip()
    if not source_listing_id or not title or not url:
        raise ValueError("3Commas Ashby job is missing id, title, or jobUrl")

    description_html = _text(job.get("descriptionHtml"))
    description = html_to_text(description_html)
    additional_sections = _html_sections(description_html)
    primary_location = _primary_location(job)
    secondary_locations = _secondary_locations(job.get("secondaryLocations"))
    locations = (primary_location, *secondary_locations)
    location_text = _location_text(locations)
    workplace_type = _text(job.get("workplaceType")).strip()
    work_format = _work_format(workplace_type)
    remote_locations = _remote_locations(locations, work_format)
    raw = _raw(job, work_format=work_format, locations=locations, remote_locations=remote_locations)

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=url,
        source=_SOURCE_ID,
        company=_COMPANY,
        country=primary_location.country,
        city=primary_location.city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(job.get("publishedAt")).strip() or None,
        remote_in_country=_remote_in_country(work_format=work_format, remote_locations=remote_locations),
        remote_global=_remote_global(work_format=work_format, remote_locations=remote_locations),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=_requirements(additional_sections),
        additional_sections=additional_sections,
        skills=(),
        raw_text=_join_text(
            title,
            _text(job.get("department")),
            _text(job.get("team")),
            _text(job.get("employmentType")),
            workplace_type,
            location_text,
            description,
        ),
        raw=raw,
    )


@dataclass(frozen=True)
class _Location:
    name: str | None
    country: str | None
    city: str | None

    @property
    def raw(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "country": self.country,
            "city": self.city,
        }


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str | None:
        if not self.parts:
            return None
        return "\n".join(self.parts)


def _primary_location(job: dict[str, Any]) -> _Location:
    return _location(
        name=_text(job.get("location")).strip() or None,
        address=job.get("address"),
    )


def _secondary_locations(value: object) -> tuple[_Location, ...]:
    if not isinstance(value, list):
        return ()
    locations: list[_Location] = []
    for item in value:
        if isinstance(item, dict):
            locations.append(_location(name=_text(item.get("location")).strip() or None, address=item.get("address")))
    return tuple(locations)


def _location(*, name: str | None, address: object) -> _Location:
    postal_address = _postal_address(address)
    country = _text(postal_address.get("addressCountry")).strip() or None
    city = _text(postal_address.get("addressLocality")).strip() or None
    if city is None and country and name and name != country:
        city = name
    return _Location(name=name, country=country, city=city)


def _postal_address(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    postal_address = value.get("postalAddress")
    return postal_address if isinstance(postal_address, dict) else {}


def _location_text(locations: tuple[_Location, ...]) -> str | None:
    values: list[str] = []
    for location in locations:
        text = location.name or location.country or location.city
        if text and text not in values:
            values.append(text)
    return "; ".join(values) or None


def _work_format(value: str) -> str | None:
    normalized = value.casefold()
    if normalized == "remote":
        return _REMOTE_FORMAT
    if normalized == "hybrid":
        return _HYBRID_FORMAT
    if normalized in {"onsite", "on-site", "office"}:
        return _OFFICE_FORMAT
    return None


def _remote_locations(locations: tuple[_Location, ...], work_format: str | None) -> tuple[str, ...]:
    if work_format != _REMOTE_FORMAT:
        return ()
    values: list[str] = []
    for location in locations:
        value = location.country or location.name
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _remote_in_country(*, work_format: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if work_format != _REMOTE_FORMAT:
        return None
    return True if remote_locations else None


def _remote_global(*, work_format: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if work_format != _REMOTE_FORMAT:
        return None
    return False if remote_locations else None


def _raw(
    job: dict[str, Any],
    *,
    work_format: str | None,
    locations: tuple[_Location, ...],
    remote_locations: tuple[str, ...],
) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": _text(job.get("id")).strip() or None,
        "job_id": _text(job.get("jobId")).strip() or None,
        "department": _text(job.get("department")).strip() or None,
        "team": _text(job.get("team")).strip() or None,
        "employment_type": _text(job.get("employmentType")).strip() or None,
        "workplace_type": _text(job.get("workplaceType")).strip() or None,
        "is_remote": job.get("isRemote"),
        "locations": tuple(location.raw for location in locations),
        "should_display_compensation": job.get("shouldDisplayCompensationOnJobBoard"),
        "compensation_tier_summary": job.get("compensationTierSummary"),
    }
    if work_format:
        raw["work_format"] = work_format
    if remote_locations:
        raw["remote_locations"] = remote_locations
    return raw


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


def _requirements(sections: dict[str, str]) -> str | None:
    for label, body in sections.items():
        normalized = label.casefold()
        if "requirements" in normalized or "looking for" in normalized:
            return body
    return None


def _html_to_text(value: str) -> str | None:
    if not value.strip():
        return None
    collector = _TextCollector()
    collector.feed(html.unescape(value))
    return collector.text()


def _section_label(match: re.Match[str]) -> str | None:
    tag = match.group("tag").casefold()
    label = (_html_to_text(match.group("label")) or "").rstrip(":").strip()
    if not label:
        return None
    if tag == "strong" and not match.group("label").strip().endswith(":"):
        return None
    return label


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
