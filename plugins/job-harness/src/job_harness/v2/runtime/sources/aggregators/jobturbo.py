"""Contract-first JobTurbo aggregator source."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, ClassVar

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
from job_harness.v2.runtime.sources._url import absolute_url
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://jobturbo.ru"
_SEARCH_URL = f"{_BASE_URL}/vakansii/remote"
_JSON_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_VACANCY_PATH_RE = re.compile(r"/vakansiya/(\d+)")
_GRADE_MARKERS = (
    ("lead", "lead"),
    ("senior", "senior"),
    ("middle", "middle"),
    ("mid/", "middle"),
    ("junior", "junior"),
)


class JobTurboSource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("jobturbo")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("jobturbo")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_SEARCH_URL,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        listings = tuple(
            listing
            for listing in _parse_listings(response.body)
            if _listing_matches_query(listing, request.query_variant)
        )
        if not listings:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        return SourceSearchParseResult(outcome=SourceOutcome.SUCCESS, listings=listings)


def _parse_listings(html: str) -> tuple[RawListing, ...]:
    anchor_texts = _vacancy_anchor_texts(html)
    listings: list[RawListing] = []
    for item in _iter_json_ld_list_items(html):
        listing = _listing_from_item(item, anchor_texts)
        if listing is not None:
            listings.append(listing)
    return tuple(listings)


def _iter_json_ld_list_items(html: str) -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    for payload in _JSON_LD_RE.findall(html):
        data = json.loads(payload)
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("@type") == "ItemList":
                list_items = entry.get("itemListElement")
                if isinstance(list_items, list):
                    items.extend(item for item in list_items if isinstance(item, dict))
    return tuple(items)


def _listing_from_item(item: dict[str, Any], anchor_texts: tuple[_VacancyAnchor, ...]) -> RawListing | None:
    title = _text(item.get("name")).strip()
    if not title:
        return None

    url = _text(item.get("url")).strip()
    if not url:
        resolved = _resolve_url(title, anchor_texts)
        if resolved is None:
            return None
        url = resolved
    if "/vakansiya/" not in url:
        return None

    absolute = absolute_url(_BASE_URL, url)
    listing_id_match = _VACANCY_PATH_RE.search(absolute)
    if listing_id_match is None:
        return None

    position = item.get("position")
    raw: dict[str, object] = {"href": url if url.startswith("/") else absolute}
    if isinstance(position, int) and position > 0:
        raw["position"] = position

    return RawListing(
        source_listing_id=listing_id_match.group(1),
        title=title,
        url=absolute,
        source="jobturbo",
        company=None,
        country=None,
        city=None,
        location_text=None,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=True,
        remote_global=True,
        relocation=None,
        native_grade=_native_grade(title),
        description=None,
        requirements=None,
        skills=(),
        raw_text=title,
        raw=raw,
    )


def _resolve_url(title: str, anchor_texts: tuple[_VacancyAnchor, ...]) -> str | None:
    folded_title = title.casefold()
    for anchor in anchor_texts:
        if folded_title in anchor.text.casefold():
            return anchor.href
    return None


@dataclass(frozen=True)
class _VacancyAnchor:
    href: str
    text: str


class _VacancyAnchorCollector(HTMLParser):
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
        self.anchors: list[_VacancyAnchor] = []
        self._href: str | None = None
        self._parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self._href is None:
            attr_map = {key: value or "" for key, value in attrs}
            self._href = attr_map.get("href", "")
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
        if tag == "a" and self._depth <= 0:
            href = self._href or ""
            text = " ".join(self._parts).split()
            normalized = " ".join(text)
            self._href = None
            self._parts = []
            self._depth = 0
            if "/vakansiya/" in href:
                self.anchors.append(_VacancyAnchor(href=href, text=normalized))


def _vacancy_anchor_texts(html: str) -> tuple[_VacancyAnchor, ...]:
    collector = _VacancyAnchorCollector()
    collector.feed(html)
    return tuple(collector.anchors)


def _listing_matches_query(listing: RawListing, query: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True
    searchable = " ".join(
        str(value or "")
        for value in (
            listing.title,
            listing.company,
            listing.url,
            listing.location_text,
            listing.description,
            listing.requirements,
            " ".join(listing.skills),
            listing.raw_text,
        )
    ).casefold()
    return any(token in searchable for token in tokens)


def _query_tokens(query: str) -> set[str]:
    return {token for token in re.findall(r"[a-zа-яё0-9+#.]+", query.casefold()) if len(token) > 1}


def _native_grade(title: str) -> str | None:
    folded = title.casefold()
    for marker, grade in _GRADE_MARKERS:
        if marker in folded:
            return grade
    return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
