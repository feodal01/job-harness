"""Resolve source-specific official-site links from aggregator company profiles."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from job_harness.v2.contracts import SourceFetchRequest
from job_harness.v2.ports import ArtifactFetcher
from job_harness.v2.runtime.application_channel_resolver import clean_http_url
from job_harness.v2.runtime.application_channel_sources import ApplicationChannelResolutionPolicy
from job_harness.v2.runtime.company_contacts import profile_contacts_from_html
from job_harness.v2.runtime.errors import ClassifiedSourceError

_MAX_ANCHORS = 400


@dataclass(frozen=True)
class ProfileSiteResolutionRequest:
    profile_url: str
    policy: ApplicationChannelResolutionPolicy


@dataclass(frozen=True)
class ProfileSiteResolution:
    site_url: str | None
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


async def resolve_profile_site(
    request: ProfileSiteResolutionRequest,
    *,
    fetcher: ArtifactFetcher,
) -> ProfileSiteResolution:
    try:
        response = await fetcher.fetch(
            SourceFetchRequest(
                source_id=request.policy.source_id,
                query_variant="application_channel_profile",
                url=request.profile_url,
            )
        )
    except (ClassifiedSourceError, OSError, TimeoutError, ValueError):
        return ProfileSiteResolution(site_url=None, attempted=True, resolved=False, failed=True)

    site_url = official_site_url_from_profile(
        base_url=response.url,
        html=response.body,
        policy=request.policy,
    )
    contacts = profile_contacts_from_html(
        source_id=request.policy.source_id,
        base_url=response.url,
        html=response.body,
    )
    return ProfileSiteResolution(
        site_url=site_url,
        attempted=True,
        resolved=site_url is not None,
        failed=False,
        contacts=contacts,
    )


def official_site_url_from_profile(
    *,
    base_url: str,
    html: str,
    policy: ApplicationChannelResolutionPolicy,
) -> str | None:
    if not policy.profile_site_text_markers:
        return None
    collector = _AnchorCollector()
    collector.feed(html)
    for anchor in collector.anchors:
        if not _has_profile_site_text(anchor.text, policy):
            continue
        url = clean_http_url(urljoin(base_url, anchor.href))
        if url is not None and _is_external_profile_link(url, base_url):
            return url
    return None


def _has_profile_site_text(text: str, policy: ApplicationChannelResolutionPolicy) -> bool:
    folded = " ".join(text.casefold().split())
    return bool(folded) and any(marker in folded for marker in policy.profile_site_text_markers)


def _is_external_profile_link(url: str, base_url: str) -> bool:
    host = urlparse(url).netloc.casefold()
    base_host = urlparse(base_url).netloc.casefold()
    return host != base_host and not host.endswith(f".{base_host}") and not base_host.endswith(f".{host}")
