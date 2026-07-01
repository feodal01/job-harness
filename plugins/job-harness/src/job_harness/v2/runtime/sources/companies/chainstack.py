"""Contract-first Chainstack career source backed by BambooHR careers JSON."""

from __future__ import annotations

import json
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

_BOARD_URL = "https://chainstack.bamboohr.com/careers/list"
_DETAIL_URL_TEMPLATE = "https://chainstack.bamboohr.com/careers/{id}"
_SOURCE_ID = "career:chainstack"
_COMPANY = "Chainstack"
_REMOTE_FORMAT = "remote"


class ChainstackCareerSource(SourceScraper):
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
        items = payload.get("result")
        if not isinstance(items, list):
            raise ValueError("Chainstack BambooHR response result is not a JSON array")
        total_count = _int_value(payload.get("meta"), "totalCount")
        if not items and total_count == 0:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        if not items:
            raise ValueError("Chainstack BambooHR response has no result rows without an explicit empty count")

        listings = tuple(_listing(item) for item in items if isinstance(item, dict))
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Chainstack BambooHR response is not a JSON object")
    return value


def _listing(item: dict[str, Any]) -> RawListing:
    listing_id = str(item.get("id") or "").strip()
    title = _text(item.get("jobOpeningName")).strip()
    if not listing_id or not title:
        raise ValueError("Chainstack BambooHR listing is missing id or jobOpeningName")

    department = _text(item.get("departmentLabel")).strip() or None
    employment_status = _text(item.get("employmentStatusLabel")).strip() or None
    work_format = _work_format(employment_status)
    raw: dict[str, object] = {
        "department": department,
        "employment_status": employment_status,
        "location": item.get("location"),
        "ats_location": item.get("atsLocation"),
        "is_remote": item.get("isRemote"),
        "location_type": item.get("locationType"),
    }
    if work_format:
        raw["work_format"] = (work_format,)

    return RawListing(
        source_listing_id=listing_id,
        title=title,
        url=_DETAIL_URL_TEMPLATE.format(id=listing_id),
        source=_SOURCE_ID,
        company=_COMPANY,
        country=None,
        city=None,
        location_text=_location_text(item),
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=None,
        remote_global=None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        additional_sections={},
        skills=(),
        raw_text=_join_text(title, department, employment_status, _location_text(item)),
        raw=raw,
    )


def _location_text(item: dict[str, Any]) -> str | None:
    values: list[str] = []
    for container_key in ("atsLocation", "location"):
        container = item.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in ("city", "state", "province", "country"):
            value = _text(container.get(key)).strip()
            if value and value not in values:
                values.append(value)
    return ", ".join(values) or None


def _work_format(employment_status: str | None) -> str | None:
    normalized = (employment_status or "").casefold()
    if "remote" in normalized:
        return _REMOTE_FORMAT
    return None


def _int_value(value: object, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    return item if isinstance(item, int) else None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
