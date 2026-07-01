"""Resolve career pages from source-specific application-channel policies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

from job_harness.v2.contracts import SourceFetchRequest
from job_harness.v2.ports import ArtifactFetcher
from job_harness.v2.runtime.application_channel_sources import ApplicationChannelResolutionPolicy
from job_harness.v2.runtime.company_contacts import (
    best_contact_page_url,
    merge_company_contacts,
    site_contacts_from_html,
)
from job_harness.v2.runtime.errors import ClassifiedSourceError

_NON_EMPLOYER_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "t.me",
    "telegram.me",
    "twitter.com",
    "x.com",
    "youtube.com",
)
_TOKEN_BOUNDARY_MARKERS = frozenset({"job", "jobs", "rabota", "работа"})
_MAX_ANCHORS = 400


@dataclass(frozen=True)
class SiteResolutionRequest:
    site_url: str
    policy: ApplicationChannelResolutionPolicy
    channel_source: str

    def __post_init__(self) -> None:
        if not self.channel_source.strip():
            raise ValueError("channel_source must be non-empty")


@dataclass(frozen=True)
class SiteResolution:
    channel: dict[str, str] | None
    attempted: bool
    resolved: bool
    failed: bool
    contacts: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class _Anchor:
    href: str
    text: str


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or len(self.anchors) >= _MAX_ANCHORS:
            return
        attr_map = {name.casefold(): value or "" for name, value in attrs}
        href = attr_map.get("href", "").strip()
        if not href:
            return
        self._href = href
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            text = " ".join(data.split())
            if text:
                self._text_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        self.anchors.append(_Anchor(href=self._href, text=" ".join(self._text_parts)))
        self._href = None
        self._text_parts = []


async def resolve_site(
    request: SiteResolutionRequest,
    *,
    fetcher: ArtifactFetcher,
) -> SiteResolution:
    if _looks_like_career_url(request.site_url, request.policy):
        return SiteResolution(
            channel=_career_channel(
                request.site_url,
                status="source_provided",
                source=request.channel_source,
            ),
            attempted=False,
            resolved=True,
            failed=False,
        )
    try:
        response = await fetcher.fetch(
            SourceFetchRequest(
                source_id=request.policy.source_id,
                query_variant="application_channel",
                url=request.site_url,
            )
        )
    except (ClassifiedSourceError, OSError, TimeoutError, ValueError):
        return SiteResolution(channel=None, attempted=True, resolved=False, failed=True)

    contacts = site_contacts_from_html(
        base_url=response.url,
        html=response.body,
        source="company_site_homepage",
    )
    contact_page_contacts = await _contacts_from_contact_page(
        request=request,
        base_url=response.url,
        html=response.body,
        fetcher=fetcher,
    )
    contacts = merge_company_contacts(contacts, contact_page_contacts)

    career_url = _best_career_link(base_url=response.url, html=response.body, policy=request.policy)
    if career_url is None:
        return SiteResolution(channel=None, attempted=True, resolved=False, failed=False, contacts=contacts)
    return SiteResolution(
        channel=_career_channel(
            career_url,
            status="resolved",
            source="company_site_homepage",
        ),
        attempted=True,
        resolved=True,
        failed=False,
        contacts=contacts,
    )


def clean_http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if "://" not in stripped and _looks_like_non_http_scheme(stripped):
        return None
    parsed = urlparse(stripped if "://" in stripped else f"https://{stripped}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if _is_non_employer_domain(parsed.netloc):
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def is_non_career_link_domain(host: str, policy: ApplicationChannelResolutionPolicy) -> bool:
    folded = host.casefold()
    return any(domain == folded or folded.endswith(f".{domain}") for domain in policy.non_career_link_domains)


def _career_channel(url: str, *, status: str, source: str) -> dict[str, str]:
    return {
        "type": "company_career_page",
        "label": "Careers",
        "url": url,
        "status": status,
        "source": source,
    }


async def _contacts_from_contact_page(
    *,
    request: SiteResolutionRequest,
    base_url: str,
    html: str,
    fetcher: ArtifactFetcher,
) -> tuple[dict[str, str], ...]:
    contact_page_url = best_contact_page_url(base_url=base_url, html=html)
    if contact_page_url is None:
        return ()
    try:
        response = await fetcher.fetch(
            SourceFetchRequest(
                source_id=request.policy.source_id,
                query_variant="company_contact_page",
                url=contact_page_url,
            )
        )
    except (ClassifiedSourceError, OSError, TimeoutError, ValueError):
        return ()
    return site_contacts_from_html(
        base_url=response.url,
        html=response.body,
        source="company_site_contact_page",
    )


def _best_career_link(*, base_url: str, html: str, policy: ApplicationChannelResolutionPolicy) -> str | None:
    collector = _AnchorCollector()
    collector.feed(html)
    scored: list[tuple[int, str]] = []
    for anchor in collector.anchors:
        href = urljoin(base_url, anchor.href)
        url = clean_http_url(href)
        if url is None:
            continue
        score = _career_link_score(url=url, text=anchor.text, base_url=base_url, policy=policy)
        if score > 0:
            scored.append((score, url))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def _career_link_score(
    *,
    url: str,
    text: str,
    base_url: str,
    policy: ApplicationChannelResolutionPolicy,
) -> int:
    parsed = urlparse(url)
    if _is_non_employer_domain(parsed.netloc) or is_non_career_link_domain(parsed.netloc, policy):
        return 0
    url_haystack = f"{parsed.netloc} {parsed.path} {parsed.query}".casefold()
    url_match = _has_career_url_marker(url_haystack, policy)
    text_match = _has_career_text_marker(text, policy)
    if not url_match and not text_match:
        return 0

    score = 1
    if _same_host_or_ats(url, base_url, policy):
        score += 5
    if _looks_like_career_url(url, policy):
        score += 8
    if text_match:
        score += 5
    return score


def _looks_like_career_url(url: str, policy: ApplicationChannelResolutionPolicy) -> bool:
    parsed = urlparse(url)
    haystack = f"{parsed.netloc} {parsed.path}".casefold()
    return any(domain in parsed.netloc.casefold() for domain in policy.ats_domains) or _has_career_url_marker(
        haystack,
        policy,
    )


def _has_career_url_marker(haystack: str, policy: ApplicationChannelResolutionPolicy) -> bool:
    return any(_career_url_marker_matches(marker, haystack) for marker in policy.career_markers)


def _career_url_marker_matches(marker: str, haystack: str) -> bool:
    if marker not in _TOKEN_BOUNDARY_MARKERS:
        return marker in haystack
    return re.search(rf"(?<![a-zа-яё0-9]){re.escape(marker)}(?![a-zа-яё0-9])", haystack) is not None


def _has_career_text_marker(text: str, policy: ApplicationChannelResolutionPolicy) -> bool:
    folded = " ".join(text.casefold().split())
    if not folded:
        return False
    if "@" in folded and " " not in folded:
        return False
    return folded in policy.career_text_exact or any(marker in folded for marker in policy.career_text_markers)


def _same_host_or_ats(url: str, base_url: str, policy: ApplicationChannelResolutionPolicy) -> bool:
    host = urlparse(url).netloc.casefold()
    base_host = urlparse(base_url).netloc.casefold()
    return host == base_host or any(domain in host for domain in policy.ats_domains)


def _is_non_employer_domain(host: str) -> bool:
    folded = host.casefold()
    return any(domain == folded or folded.endswith(f".{domain}") for domain in _NON_EMPLOYER_DOMAINS)


def _looks_like_non_http_scheme(value: str) -> bool:
    before_slash = value.split("/", 1)[0]
    scheme, separator, remainder = before_slash.partition(":")
    if not separator:
        return False
    if remainder.isdigit() and ("." in scheme or scheme == "localhost"):
        return False
    return bool(scheme)
