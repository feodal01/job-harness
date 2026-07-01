"""Contract-first GeekJob aggregator source."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import ClassVar

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
from job_harness.v2.runtime.sources._url import absolute_url
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://geekjob.ru"
_SEARCH_URL = f"{_BASE_URL}/vacancies"
_LINK_PATTERN = re.compile(r"^/vacancy/[a-f0-9]+$")
_SALARY_RE = re.compile(r"(?:от\s*)?(?:~\s*)?\d[\d\s.,]*(?:K|к)?(?:\s*(?:₽|\$|€|руб))?", re.I)
_SALARY_LINE_RE = re.compile(
    r"^\s*(?:от\s*)?(?:~\s*)?\d[\d\s.,]*(?:K|к)?(?:\s*(?:₽|\$|€|руб))?"
    r"(?:\s*[—–-]\s*(?:~\s*)?\d[\d\s.,]*(?:K|к)?(?:\s*(?:₽|\$|€|руб))?)?\s*$",
    re.I,
)
_DATE_RE = re.compile(r"\b\d{1,2}\s+[а-яё]+\b", re.I)
_MAX_UPPERCASE_LOCATION_TOKEN_LENGTH = 3


class GeekJobSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("geekjob")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("geekjob")

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
        company_profile_url = _company_profile_url(response.body)
        if company_profile_url is None:
            return listing
        return replace(
            listing,
            raw=_merge_company_facts(listing.raw, {"companyProfileUrl": company_profile_url}),
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
        if self._depth == 0:
            self.anchors.append(_Anchor(href=self._href, text=_normalize_text(" ".join(self._parts))))
            self._href = None
            self._parts = []


def _parse_listings(body: str) -> tuple[RawListing, ...]:
    collector = _AnchorCollector()
    collector.feed(body)
    grouped: dict[str, list[str]] = {}
    for anchor in collector.anchors:
        if not _LINK_PATTERN.match(anchor.href):
            continue
        grouped.setdefault(anchor.href, []).append(anchor.text)

    listings: list[RawListing] = []
    for href, texts in grouped.items():
        listing = _listing_from_texts(href, texts)
        if listing is not None:
            listings.append(listing)
    return tuple(listings)


def _company_profile_url(body: str) -> str | None:
    collector = _AnchorCollector()
    collector.feed(body)
    for anchor in collector.anchors:
        if re.fullmatch(r"/company/[a-f0-9]+", anchor.href):
            return absolute_url(_BASE_URL, anchor.href)
    return None


def _listing_from_texts(href: str, texts: list[str]) -> RawListing | None:
    cleaned = [_normalize_text(text) for text in texts if _normalize_text(text)]
    meaningful = [
        text
        for text in cleaned
        if not _DATE_RE.fullmatch(text)
        and text != "chevron_right"
        and not (len(text) <= _MAX_UPPERCASE_LOCATION_TOKEN_LENGTH and text.isupper())
    ]
    if not meaningful:
        return None

    title = ""
    company = ""
    location_text: str | None = None
    salary_text: str | None = None
    for text in meaningful:
        salary_candidate = _salary_from_text(text)
        if salary_text is None and salary_candidate:
            salary_text = salary_candidate
        if location_text is None and salary_candidate and not _SALARY_LINE_RE.fullmatch(text):
            location_text = text
            continue
        if salary_candidate is not None and _SALARY_LINE_RE.fullmatch(text):
            continue
        if not title:
            title = text
            continue
        if not company:
            company = text
            break

    if not title:
        return None

    blob = " ".join(meaningful)
    source_listing_id = href.rsplit("/", 1)[-1]
    remote = _is_remote(blob) or None
    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=absolute_url(_BASE_URL, href),
        source="geekjob",
        company=company or None,
        country=None,
        city=None,
        location_text=location_text,
        salary_text=salary_text,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=_posted_at_from_texts(meaningful),
        remote_in_country=remote,
        remote_global=False if remote else None,
        relocation=None,
        native_grade=None,
        description=None,
        requirements=None,
        skills=(),
        raw_text=blob or None,
        raw={"href": href},
    )


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


def _merge_company_facts(raw: dict[str, object], facts: dict[str, object]) -> dict[str, object]:
    existing = raw.get("company")
    company = dict(existing) if isinstance(existing, dict) else {}
    company.update(facts)
    return {**raw, "company": company}


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


def _posted_at_from_texts(texts: list[str]) -> str | None:
    for text in texts:
        if _DATE_RE.fullmatch(text):
            return text
    return None


def _normalize_text(value: str) -> str:
    return " ".join(value.split())
