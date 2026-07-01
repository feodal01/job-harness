"""Contract-first Outschool career source backed by Greenhouse board JSON."""

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

_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/outschool/jobs?content=true"
_SOURCE_ID = "career:outschool"
_COMPANY = "Outschool"
_SECTION_LABEL_RE = re.compile(
    r"<(?P<tag>h[1-6]|strong|b)[^>]*>(?P<label>.*?)</(?P=tag)>",
    re.I | re.S,
)
_SALARY_MARKER_RE = re.compile(r"(\$|CAD|USD|hourly rate)", re.I)
_REMOTE_SCOPE_RE = re.compile(r"remote\s*\((?P<scope>[^)]+)\)", re.I)
_REQUIREMENTS_LABEL_MARKERS = ("desired experience", "required", "requirements", "skills")
_SALARY_LABEL_MARKERS = ("compensation", "pay zones", "보상")
_SALARY_STOP_LINE_MARKERS = (
    "we use covey",
    "please see the independent bias audit",
    "benefits & culture",
    "outschool is an equal opportunity employer",
)


class OutschoolCareerSource(SourceScraper):
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
        raw_jobs = payload.get("jobs")
        meta = payload.get("meta")
        if not isinstance(raw_jobs, list) or not isinstance(meta, dict):
            raise ValueError("Outschool Greenhouse payload is malformed")
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
        raise ValueError("Outschool Greenhouse response is not a JSON object")
    return value


def _listing(job: dict[str, Any]) -> RawListing:
    source_listing_id = str(job.get("id") or "")
    title = _required_text(job.get("title"), "title")
    location_text = _nested_text(job, "location", "name")
    content = _text(job.get("content"))
    visible_content = html.unescape(content)
    description = _html_to_text(visible_content)
    additional_sections = _html_sections(visible_content)
    requirements = _requirements(additional_sections)
    salary_text = _salary_text(additional_sections)
    departments = _names(job.get("departments"))
    offices = _names(job.get("offices"))
    remote_locations = _remote_locations(location_text)
    work_formats = _work_formats(location_text)
    country, city = _country_city(location_text)
    raw = {
        "id": job.get("id"),
        "internal_job_id": job.get("internal_job_id"),
        "requisition_id": job.get("requisition_id"),
        "updated_at": job.get("updated_at"),
        "departments": departments,
        "offices": offices,
        "location": location_text,
    }
    if remote_locations:
        raw["remote_locations"] = remote_locations
    if work_formats:
        raw["work_format"] = work_formats

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=_required_text(job.get("absolute_url"), "absolute_url"),
        source=_SOURCE_ID,
        company=_text(job.get("company_name")).strip() or _COMPANY,
        country=country,
        city=city,
        location_text=location_text or None,
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_text(job.get("first_published")).strip() or _text(job.get("updated_at")).strip() or None,
        remote_in_country=_remote_in_country(location_text),
        remote_global=_remote_global(location_text),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections=additional_sections,
        skills=(),
        raw_text=_join_text(title, location_text, " ".join(departments), description, requirements, salary_text),
        raw=raw,
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
    label = (_html_to_text(match.group("label")) or "").rstrip(":").strip()
    return label or None


def _requirements(sections: dict[str, str]) -> str | None:
    parts = [
        body
        for label, body in sections.items()
        if any(marker in label.casefold() for marker in _REQUIREMENTS_LABEL_MARKERS)
        and not any(salary_marker in label.casefold() for salary_marker in ("compensation", "pay"))
    ]
    return "\n".join(parts) or None


def _salary_text(sections: dict[str, str]) -> str | None:
    parts: list[str] = []
    for label, body in sections.items():
        if not any(marker in label.casefold() for marker in _SALARY_LABEL_MARKERS):
            continue
        salary_body = _salary_body(body)
        if salary_body and _SALARY_MARKER_RE.search(salary_body):
            parts.append(f"{label}\n{salary_body}")
    return "\n\n".join(parts) or None


def _salary_body(body: str) -> str | None:
    lines: list[str] = []
    for line in body.splitlines():
        normalized = line.strip().casefold()
        if any(normalized.startswith(marker) for marker in _SALARY_STOP_LINE_MARKERS):
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    return text or None


def _work_formats(location_text: str) -> tuple[str, ...]:
    work_formats: list[str] = []
    if "remote" in location_text.casefold():
        work_formats.append("remote")
    if any(part.strip() and "remote" not in part.casefold() for part in location_text.split(";")):
        work_formats.append("office")
    return tuple(work_formats)


def _remote_locations(location_text: str) -> tuple[str, ...]:
    locations: list[str] = []
    for match in _REMOTE_SCOPE_RE.finditer(location_text):
        locations.extend(_split_remote_scope(match.group("scope")))
    return tuple(locations)


def _split_remote_scope(value: str) -> tuple[str, ...]:
    normalized = value.replace("&", ",").replace("/", ",")
    return tuple(_normalize_remote_scope_part(part) for part in normalized.split(",") if part.strip())


def _normalize_remote_scope_part(value: str) -> str:
    part = value.strip()
    if part.casefold().replace(".", "") in {"us", "usa"}:
        return "US"
    return part


def _remote_in_country(location_text: str) -> bool | None:
    if "remote" not in location_text.casefold():
        return False
    if _remote_locations(location_text):
        return True
    return None


def _remote_global(location_text: str) -> bool | None:
    if "remote" not in location_text.casefold():
        return False
    return False if _remote_locations(location_text) else None


def _country_city(location_text: str) -> tuple[str | None, str | None]:
    if ";" in location_text or "remote" in location_text.casefold():
        return None, None
    if "," not in location_text:
        return None, location_text or None
    city, country = (part.strip() for part in location_text.rsplit(",", 1))
    return country or None, city or None


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


def _required_text(value: object, field_name: str) -> str:
    text = _text(value).strip()
    if not text:
        raise ValueError(f"Outschool Greenhouse posting is missing {field_name}")
    return text


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = "\n".join(part.strip() for part in parts if part and part.strip())
    return text or None
