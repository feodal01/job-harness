"""Contract-first JetBrains career source backed by Greenhouse board JSON."""

from __future__ import annotations

import html
import json
import re
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
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/jetbrains/jobs?content=true"
_SECTION_LABEL_RE = re.compile(
    r"<(?P<tag>h[1-6]|strong)[^>]*>(?P<label>.*?)</(?P=tag)>",
    re.I | re.S,
)
_LINKEDIN_TAG_RE = re.compile(r"#LI-[A-Za-z0-9_-]+", re.I)
_LINKEDIN_WORKPLACE_TAGS = {
    "#li-hybrid",
    "#li-onsite",
    "#li-remote",
}

class JetBrainsCareerSource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("career:jetbrains")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("career:jetbrains")

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
        raw_jobs = payload.get("jobs")
        meta = payload.get("meta")
        if not isinstance(raw_jobs, list) or not isinstance(meta, dict):
            raise ValueError("JetBrains Greenhouse payload is malformed")
        if not raw_jobs and meta.get("total") == 0:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        listings = tuple(_listing(job) for job in raw_jobs if isinstance(job, dict))
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("JetBrains Greenhouse response is not a JSON object")
    return value


def _listing(job: dict[str, Any]) -> RawListing:
    source_listing_id = str(job.get("id") or "")
    title = _text(job.get("title")).strip()
    location_text = _nested_text(job, "location", "name")
    locations = _locations(location_text)
    content = _text(job.get("content"))
    linkedin_workplace_tags = _linkedin_workplace_tags(content)
    visible_content = _remove_linkedin_tags(content)
    description = _html_to_text(visible_content)
    additional_sections = _html_sections(visible_content)
    departments = _names(job.get("departments"))
    offices = _names(job.get("offices"))
    metadata = _metadata(job.get("metadata"))
    first_location = locations[0] if locations else _Location(city=None, country=None, remote=False)
    remote_in_country = _remote_in_country(locations)
    remote_global = _remote_global(locations)

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=_text(job.get("absolute_url")).strip(),
        source="career:jetbrains",
        company=_text(job.get("company_name")).strip() or "JetBrains",
        country=first_location.country,
        city=first_location.city,
        location_text=location_text or None,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(job.get("first_published")).strip() or None,
        remote_in_country=remote_in_country,
        remote_global=remote_global,
        relocation=None,
        native_grade=None,
        description=description,
        requirements=None,
        additional_sections=additional_sections,
        skills=(),
        raw_text=_join_text(title, location_text, " ".join(departments), " ".join(metadata), description),
        raw={
            "id": job.get("id"),
            "internal_job_id": job.get("internal_job_id"),
            "requisition_id": job.get("requisition_id"),
            "updated_at": job.get("updated_at"),
            "linkedin_workplace_tags": linkedin_workplace_tags,
            "departments": departments,
            "offices": offices,
            "metadata": metadata,
            "locations": tuple(location.raw for location in locations),
        },
    )


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


class _Location:
    def __init__(self, *, city: str | None, country: str | None, remote: bool) -> None:
        self.city = city
        self.country = country
        self.remote = remote

    @property
    def raw(self) -> dict[str, object]:
        return {
            "city": self.city,
            "country": self.country,
            "remote": self.remote,
        }


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


def _linkedin_workplace_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    for match in _LINKEDIN_TAG_RE.findall(html.unescape(value)):
        tag = match.upper()
        if tag.casefold() in _LINKEDIN_WORKPLACE_TAGS and tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _remove_linkedin_tags(value: str) -> str:
    return _LINKEDIN_TAG_RE.sub("", html.unescape(value))


def _section_label(match: re.Match[str]) -> str | None:
    tag = match.group("tag").casefold()
    label = (_html_to_text(match.group("label")) or "").rstrip(":").strip()
    if not label:
        return None
    if tag == "strong" and not match.group("label").strip().endswith(":"):
        return None
    return label


def _locations(location_text: str) -> tuple[_Location, ...]:
    locations: list[_Location] = []
    for raw_part in location_text.split(";"):
        part = raw_part.strip()
        if not part:
            continue
        locations.append(_parse_location(part))
    return tuple(locations)


def _parse_location(value: str) -> _Location:
    if value.casefold() == "remote":
        return _Location(city=None, country=None, remote=True)
    if value.casefold().startswith("remote,"):
        country_name = value.split(",", 1)[1].strip()
        return _Location(city=None, country=country_name or None, remote=True)
    if "," not in value:
        return _Location(city=value, country=None, remote=False)
    city, country_name = (part.strip() for part in value.rsplit(",", 1))
    return _Location(city=city or None, country=country_name or None, remote=False)


def _remote_global(locations: tuple[_Location, ...]) -> bool | None:
    remote_locations = tuple(location for location in locations if location.remote)
    if not remote_locations:
        return False
    if any(location.country is None for location in remote_locations):
        return None
    return False


def _remote_in_country(locations: tuple[_Location, ...]) -> bool | None:
    remote_locations = tuple(location for location in locations if location.remote)
    if not remote_locations:
        return False
    if any(location.country is None for location in remote_locations):
        return None
    return True


def _metadata(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name")).strip()
        raw_value = _text(item.get("value")).strip()
        if name and raw_value:
            result.append(f"{name}: {raw_value}")
    return tuple(result)


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        name
        for item in value
        if isinstance(item, dict)
        for name in (_text(item.get("name")).strip(),)
        if name
    )


def _nested_text(value: dict[str, Any], key: str, nested_key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, dict):
        return ""
    return _text(nested.get(nested_key)).strip()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
