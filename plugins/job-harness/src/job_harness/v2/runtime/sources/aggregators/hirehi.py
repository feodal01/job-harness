"""Contract-first HireHi aggregator source."""

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
from job_harness.v2.runtime.sources._detail_html import json_ld_job_posting_description
from job_harness.v2.runtime.sources._url import absolute_url
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://hirehi.ru"
_SEARCH_PATH = "/jobs_new"
_LINK_PATTERN = re.compile(
    r"^/(?:qa|marketing|devops|analytics|development|design|management|backend|frontend|fullstack|python|java|go|mobile|ml-ai)/[^/]+-\d+$"
)
_SALARY_RE = re.compile(r"(?:от\s*)?(?:~\s*)?\d[\d\s.,]*(?:K|к)?(?:\s*(?:₽|\$|€|руб))?", re.I)
_GRADE_PREFIXES = ("senior", "middle", "junior", "lead")


class HireHiSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("hirehi")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("hirehi")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=f"{_BASE_URL}{_SEARCH_PATH}?{urlencode({'query': query_variant})}",
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
        description = json_ld_job_posting_description(response.body)
        if description is None:
            raise ValueError("HireHi detail page does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
        )


@dataclass(frozen=True)
class _Anchor:
    href: str
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
            text = _normalize_text(" ".join(self._parts))
            href = self._href or ""
            self._href = None
            self._parts = []
            self._depth = 0
            if text:
                self.anchors.append(_Anchor(href=href, text=text))


def _parse_listings(body: str) -> tuple[RawListing, ...]:
    collector = _AnchorCollector()
    collector.feed(body)
    seen: set[str] = set()
    listings: list[RawListing] = []
    for anchor in collector.anchors:
        if not _LINK_PATTERN.match(anchor.href):
            continue
        if anchor.href in seen:
            continue
        seen.add(anchor.href)
        listing = _listing_from_anchor(anchor)
        if listing is not None:
            listings.append(listing)
    return tuple(listings)


def _listing_from_anchor(anchor: _Anchor) -> RawListing | None:
    text = anchor.text
    if not text:
        return None

    title = text
    company = ""
    if " в " in text:
        title, rest = text.split(" в ", 1)
        company = rest.split(",", 1)[0].strip()

    title = title.strip()
    if not title:
        return None

    normalized_title = title.casefold()
    native_grade: str | None = None
    for grade in _GRADE_PREFIXES:
        prefix = f"{grade} "
        if normalized_title.startswith(prefix):
            native_grade = grade
            title = title[len(prefix) :].strip()
            break

    salary_text = _salary_from_text(text)
    remote = _is_remote(text) or None
    work_format = _work_format(text)
    raw: dict[str, object] = {"href": anchor.href}
    if work_format:
        raw["work_format"] = work_format

    listing_id = anchor.href.rsplit("/", 1)[-1]
    return RawListing(
        source_listing_id=listing_id,
        title=title,
        url=absolute_url(_BASE_URL, anchor.href),
        source="hirehi",
        company=company or None,
        country="RU",
        city=None,
        location_text=_location_text(text),
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=remote,
        remote_global=remote,
        relocation=None,
        native_grade=native_grade,
        description=None,
        requirements=None,
        skills=(),
        raw_text=text,
        raw=raw,
    )


def _location_text(text: str) -> str | None:
    if "офис" in text.casefold():
        for part in text.split(","):
            cleaned = part.strip()
            if cleaned.casefold().startswith("офис"):
                return cleaned.removeprefix("офис").strip()
    if "гибрид" in text.casefold():
        return "гибрид"
    if _is_remote(text):
        return "удалённо"
    return None


def _work_format(text: str) -> str | None:
    folded = text.casefold()
    if "удал" in folded or "remote" in folded:
        return "remote"
    if "гибрид" in folded:
        return "hybrid"
    if "офис" in folded:
        return "office"
    return None


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


def _is_remote(text: str) -> bool:
    folded = text.casefold()
    return "remote" in folded or "удал" in folded


def _salary_from_text(text: str) -> str | None:
    match = _SALARY_RE.search(text)
    if match is None:
        return None
    return _normalize_text(match.group(0)).removeprefix("~").strip()


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
