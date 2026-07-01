"""Contract-first Truv career source backed by Lever postings JSON."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
from job_harness.v2.runtime.sources._url import strip_query
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BOARD_URL = "https://api.lever.co/v0/postings/truv?mode=json"
_SOURCE_ID = "career:truv"
_COMPANY = "Truv"
_REQUIREMENTS_LABEL_MARKERS = (
    "required",
    "requirements",
    "who you are",
    "looking for",
    "preferred skills",
)


class TruvCareerSource(SourceScraper):
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
        postings = _json_array(response.body)
        if not postings:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        listings = tuple(_listing(posting) for posting in postings if isinstance(posting, dict))
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _json_array(body: str) -> list[Any]:
    value = json.loads(body)
    if not isinstance(value, list):
        raise ValueError("Truv Lever response is not a JSON array")
    return value


def _listing(posting: dict[str, Any]) -> RawListing:
    posting_id = _required_text(posting.get("id"), "id")
    title = _required_text(posting.get("text"), "text")
    categories = _categories(posting.get("categories"))
    all_locations = _text_values(categories.get("allLocations"))
    workplace_type = _text(posting.get("workplaceType")).strip().casefold() or None
    work_formats = _work_formats(workplace_type)
    remote_locations = _remote_locations(workplace_type=workplace_type, all_locations=all_locations)
    sections = _sections(posting.get("lists"))
    requirements = _requirements(sections)
    description = _description(posting)
    raw = {
        "id": posting_id,
        "apply_url": _text(posting.get("applyUrl")).strip() or None,
        "created_at": posting.get("createdAt"),
        "team": _text(categories.get("team")).strip() or None,
        "commitment": _text(categories.get("commitment")).strip() or None,
        "all_locations": all_locations,
        "lever_country": _text(posting.get("country")).strip() or None,
        "workplace_type": workplace_type,
    }
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=posting_id,
        title=title,
        url=strip_query(_required_text(posting.get("hostedUrl"), "hostedUrl")),
        source=_SOURCE_ID,
        company=_COMPANY,
        country=None,
        city=None,
        location_text=_location_text(categories.get("location"), all_locations),
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_posted_at(posting.get("createdAt")),
        remote_in_country=_remote_in_country(workplace_type, remote_locations),
        remote_global=_remote_global(workplace_type, remote_locations),
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections=sections,
        skills=(),
        raw_text=_join_text(
            title,
            _text(categories.get("team")),
            _text(categories.get("location")),
            " ".join(all_locations),
            description,
            requirements,
        ),
        raw=raw,
    )


def _categories(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Truv Lever posting categories are malformed")
    return dict(value)


def _description(posting: dict[str, Any]) -> str | None:
    return _join_text(
        _plain_text(posting.get("openingPlain")),
        _plain_text(posting.get("descriptionBodyPlain")),
        _plain_text(posting.get("additionalPlain")),
    )


def _sections(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    sections: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("text")).strip().rstrip(":")
        content = html_to_text(_text(item.get("content")))
        if label and content:
            sections[label] = content
    return sections


def _requirements(sections: dict[str, str]) -> str | None:
    parts = [
        body
        for label, body in sections.items()
        if any(marker in label.casefold() for marker in _REQUIREMENTS_LABEL_MARKERS)
    ]
    return "\n".join(parts) or None


def _work_formats(workplace_type: str | None) -> tuple[str, ...]:
    if workplace_type == "remote":
        return ("remote",)
    if workplace_type == "hybrid":
        return ("hybrid",)
    if workplace_type in {"on-site", "onsite"}:
        return ("office",)
    return ()


def _remote_locations(*, workplace_type: str | None, all_locations: tuple[str, ...]) -> tuple[str, ...]:
    if workplace_type != "remote":
        return ()
    return tuple(location for location in all_locations if location.casefold() != "remote")


def _remote_in_country(workplace_type: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if workplace_type == "remote":
        return True if remote_locations else None
    if workplace_type in {"hybrid", "on-site", "onsite"}:
        return False
    return None


def _remote_global(workplace_type: str | None, remote_locations: tuple[str, ...]) -> bool | None:
    if workplace_type == "remote":
        return False if remote_locations else None
    if workplace_type == "hybrid":
        return False
    return None


def _location_text(primary_location: object, all_locations: tuple[str, ...]) -> str | None:
    if all_locations:
        return ", ".join(all_locations)
    return _text(primary_location).strip() or None


def _posted_at(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _plain_text(value: object) -> str | None:
    text = _text(value).strip()
    return text or None


def _required_text(value: object, field_name: str) -> str:
    text = _text(value).strip()
    if not text:
        raise ValueError(f"Truv Lever posting is missing {field_name}")
    return text


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _join_text(*parts: str | None) -> str | None:
    text = "\n".join(part.strip() for part in parts if part and part.strip())
    return text or None
