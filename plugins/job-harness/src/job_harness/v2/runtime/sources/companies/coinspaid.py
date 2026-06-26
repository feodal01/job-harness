"""Contract-first CoinsPaid career source backed by Lever postings JSON."""

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

_BOARD_URL = "https://api.eu.lever.co/v0/postings/coinspaid?mode=json"
_SOURCE_ID = "career:coinspaid"
_COMPANY = "CoinsPaid"


class CoinsPaidCareerSource(SourceScraper):
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
        raise ValueError("CoinsPaid Lever response is not a JSON array")
    return value


def _listing(posting: dict[str, Any]) -> RawListing:
    posting_id = _text(posting.get("id")).strip()
    title = _text(posting.get("text")).strip()
    if not posting_id or not title:
        raise ValueError("CoinsPaid Lever posting is missing id or text")

    categories = posting.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("CoinsPaid Lever posting categories are malformed")

    location_text = _text(categories.get("location")).strip() or None
    all_locations = _text_values(categories.get("allLocations"))
    workplace_type = _text(posting.get("workplaceType")).strip().casefold() or None
    workplace_format = _workplace_format(workplace_type)
    location = _location(location_text)
    sections = _sections(posting.get("lists"))
    description = _plain_text(posting.get("descriptionPlain"))
    requirements = sections.get("Requirements")
    raw = {
        "id": posting_id,
        "apply_url": _text(posting.get("applyUrl")).strip() or None,
        "created_at": posting.get("createdAt"),
        "department": _text(categories.get("department")).strip() or None,
        "team": _text(categories.get("team")).strip() or None,
        "commitment": _text(categories.get("commitment")).strip() or None,
        "all_locations": all_locations,
        "workplace_type": workplace_type,
    }
    work_formats = _work_formats(location.work_format, workplace_format)
    if work_formats:
        raw["work_format"] = work_formats
    if location.remote_locations:
        raw["remote_locations"] = location.remote_locations

    return RawListing(
        source_listing_id=posting_id,
        title=title,
        url=strip_query(_text(posting.get("hostedUrl")).strip()),
        source=_SOURCE_ID,
        company=_COMPANY,
        country=location.country,
        city=location.city,
        location_text=location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_posted_at(posting.get("createdAt")),
        remote_in_country=_remote_in_country(location, workplace_format),
        remote_global=location.remote_global,
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections=sections,
        skills=(),
        raw_text=_join_text(
            title,
            _text(categories.get("department")),
            _text(categories.get("team")),
            location_text,
            " ".join(all_locations),
            description,
            requirements,
        ),
        raw=raw,
    )


class _Location:
    def __init__(
        self,
        *,
        country: str | None,
        city: str | None,
        remote: bool | None,
        remote_global: bool | None,
        remote_locations: tuple[str, ...],
        work_format: str | None,
    ) -> None:
        self.country = country
        self.city = city
        self.remote = remote
        self.remote_global = remote_global
        self.remote_locations = remote_locations
        self.work_format = work_format


def _location(location_text: str | None) -> _Location:
    if location_text is None:
        return _Location(
            country=None,
            city=None,
            remote=None,
            remote_global=None,
            remote_locations=(),
            work_format=None,
        )

    normalized = location_text.casefold()
    if normalized.startswith("remote"):
        remote_global = "worldwide" in normalized or normalized == "remote"
        return _Location(
            country=None,
            city=None,
            remote=None if remote_global else True,
            remote_global=remote_global,
            remote_locations=() if remote_global else _remote_locations(location_text),
            work_format="remote",
        )

    city = location_text if location_text == "New York" else None
    return _Location(
        country=None if city is not None else location_text,
        city=city,
        remote=False,
        remote_global=False,
        remote_locations=(),
        work_format=None,
    )


def _remote_locations(location_text: str) -> tuple[str, ...]:
    normalized = location_text.casefold()
    if "european region" in normalized:
        return ("Europe",)
    return ()


def _workplace_format(value: str | None) -> str | None:
    if value in {"on-site", "onsite"}:
        return "office"
    if value in {"hybrid", "office", "remote"}:
        return value
    return None


def _remote_in_country(location: _Location, workplace_format: str | None) -> bool | None:
    if workplace_format == "remote":
        return True
    return location.remote


def _work_formats(*values: str | None) -> tuple[str, ...]:
    formats: list[str] = []
    for value in values:
        if value and value not in formats:
            formats.append(value)
    return tuple(formats)


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


def _posted_at(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _plain_text(value: object) -> str | None:
    text = _text(value).strip()
    return text or None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
