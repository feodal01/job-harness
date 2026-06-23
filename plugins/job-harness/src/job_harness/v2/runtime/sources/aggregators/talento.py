"""Contract-first Talento aggregator source."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import ClassVar
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
from job_harness.v2.runtime.sources._next_flight import (
    longest_html_description_from_payloads,
    next_flight_payloads,
)
from job_harness.v2.runtime.sources._url import absolute_url
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://talento.works"
_LINK_PATTERN = re.compile(r"^/jobs/[a-f0-9-]+$")


class TalentoSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("talento")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("talento")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=f"{_BASE_URL}/?{urlencode({'q': query_variant})}",
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        listings = _parse_listings(response.body)
        if not listings:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
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
        description = longest_html_description_from_payloads(next_flight_payloads(response.body))
        if description is None:
            raise ValueError("Talento detail page does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
        )


@dataclass(frozen=True)
class _Anchor:
    href: str
    aria_label: str
    text: str


class _AnchorCollector(HTMLParser):
    _VOID_TAGS: ClassVar[frozenset[str]] = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._href: str | None = None
        self._aria_label = ""
        self._parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self._href is None:
            attr_map = {key: value or "" for key, value in attrs}
            self._href = attr_map.get("href", "")
            self._aria_label = attr_map.get("aria-label", "")
            self._parts = []
            self._depth = 1
            return
        if self._href is not None and tag not in self._VOID_TAGS:
            self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._href is None:
            return
        if tag not in self._VOID_TAGS:
            self._depth -= 1
        if self._depth == 0:
            self.anchors.append(
                _Anchor(
                    href=self._href,
                    aria_label=self._aria_label.strip(),
                    text=_normalize_text(" ".join(self._parts)),
                )
            )
            self._href = None
            self._aria_label = ""
            self._parts = []


def _parse_listings(body: str) -> tuple[RawListing, ...]:
    collector = _AnchorCollector()
    collector.feed(body)
    seen: set[str] = set()
    listings: list[RawListing] = []
    for anchor in collector.anchors:
        if not _LINK_PATTERN.match(anchor.href) or anchor.href in seen:
            continue
        listing = _listing_from_anchor(anchor)
        if listing is None:
            continue
        seen.add(anchor.href)
        listings.append(listing)
    return tuple(listings)


def _listing_from_anchor(anchor: _Anchor) -> RawListing | None:
    label = _normalize_text(anchor.aria_label or anchor.text)
    if not label:
        return None

    company = ""
    title = label
    if ": " in label:
        company, title = label.split(": ", 1)
        company = company.strip()
        title = title.strip()
    if not title:
        return None

    source_listing_id = anchor.href.rsplit("/", 1)[-1]
    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=absolute_url(_BASE_URL, anchor.href),
        source="talento",
        company=company or None,
        country=None,
        city=None,
        location_text=None,
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
        skills=(),
        raw_text=label,
        raw={"href": anchor.href, "aria_label": anchor.aria_label or None},
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
