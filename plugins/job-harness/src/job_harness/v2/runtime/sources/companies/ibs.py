"""Contract-first IBS career source backed by Bitrix SEF vacancy pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from urllib.parse import urljoin

from job_harness.v2.contracts import (
    AttemptEvidence,
    DetailEnrichmentScraper,
    RawListing,
    RemoteMode,
    RequiredParserFixtures,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceSearchParseResult,
)
from job_harness.v2.runtime.sources._html import html_to_text
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://ibs.ru/career/vacancies/"
_BASE_FILTER_URL = "https://ibs.ru/career/vacancies/filter"

_FORMAT_SECTION = "Формат работы"
_REMOTE_DISCOVERY_FRAGMENT = "#job-harness-remote-in-country"
_REMOTE_MARKERS = ("удаленно", "удалённо")
_SOURCE_ID_RE = re.compile(r"_(?P<id>\d+)$")
_COUNT_SUFFIX_RE = re.compile(r"\s*\(\s*\d+\s*\)\s*$")


class IBSCareerSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("career:ibs")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("career:ibs")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return (
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=request.query_variants[0],
                url=_initial_url(use_remote_collection_hint=_use_remote_collection_hint(request)),
            ),
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        next_request = _next_filter_request(response.body, request)
        if next_request is not None:
            return SourceSearchParseResult(
                outcome=SourceOutcome.SUCCESS,
                listings=(),
                next_request=next_request,
            )

        parser = _IBSListParser()
        parser.feed(response.body)
        if not parser.items and _looks_like_no_results(response.body):
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        listings = tuple(_listing(item) for item in parser.items)
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=listings,
            next_request=_next_page_request(response.body, request),
        )

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
        parser = _IBSDetailParser()
        parser.feed(response.body)
        description = parser.description()
        if description is None:
            raise ValueError("IBS detail page does not contain vacancy description")
        return replace(
            listing,
            description=description,
            requirements=parser.section("Наши ожидания"),
            additional_sections=parser.sections,
            raw_text=_join_text(listing.raw_text, description),
        )


def _use_remote_collection_hint(request: SearchRequest) -> bool:
    return request.remote_mode == RemoteMode.COMPATIBLE_REMOTE


def _initial_url(*, use_remote_collection_hint: bool) -> str:
    if use_remote_collection_hint:
        return f"{_BASE_URL}{_REMOTE_DISCOVERY_FRAGMENT}"
    return _BASE_URL


def _next_filter_request(body: str, request: SourceFetchRequest) -> SourceFetchRequest | None:
    if _REMOTE_DISCOVERY_FRAGMENT not in request.url:
        return None
    if "format-is-" in request.url:
        return None

    remote_option = _remote_filter_option(body)
    if remote_option is None:
        return None

    url = f"{_BASE_FILTER_URL}/{remote_option.sef_segment}/apply/"
    if request.url.rstrip("/") == url.rstrip("/"):
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=url,
    )


def _next_page_request(body: str, request: SourceFetchRequest) -> SourceFetchRequest | None:
    parser = _IBSPaginationParser()
    parser.feed(body)
    if not parser.next_url:
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=parser.next_url,
    )


def _remote_filter_option(body: str) -> _IBSFilterOption | None:
    parser = _IBSFilterParser()
    parser.feed(body)
    for option in parser.options:
        if option.section != _FORMAT_SECTION or not option.enabled:
            continue
        if any(marker in option.match_text for marker in _REMOTE_MARKERS):
            return option
    return None


def _looks_like_no_results(body: str) -> bool:
    text = html_to_text(body) or ""
    lowered = text.casefold()
    return "0 ваканс" in lowered or "ничего не найден" in lowered


def _listing(item: _IBSListItem) -> RawListing:
    source_listing_id = _source_listing_id(item)
    skills = tuple(tag for tag in item.tags if not _is_work_format_tag(tag))
    work_format_tags = tuple(tag for tag in item.tags if _is_work_format_tag(tag))
    work_format = ", ".join(work_format_tags) or None
    remote = any(any(marker in tag.casefold() for marker in _REMOTE_MARKERS) for tag in item.tags)
    raw_text = _join_text(item.title, item.description, item.location_text, " ".join(item.tags))
    return RawListing(
        source_listing_id=source_listing_id,
        title=item.title,
        url=item.url,
        source="career:ibs",
        company="IBS",
        country="Россия",
        city=None,
        location_text=item.location_text,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=None,
        remote_in_country=remote,
        remote_global=False if remote else None,
        relocation=None,
        native_grade=None,
        description=item.description or None,
        requirements=None,
        skills=skills,
        raw_text=raw_text,
        raw={
            "html_id": item.html_id,
            "work_format": work_format,
            "work_format_tags": work_format_tags,
            "tags": item.tags,
        },
    )


def _source_listing_id(item: _IBSListItem) -> str | None:
    if item.html_id:
        match = _SOURCE_ID_RE.search(item.html_id)
        if match:
            return match.group("id")
    return item.url.rstrip("/").rsplit("/", 1)[-1] or None


def _is_work_format_tag(tag: str) -> bool:
    lowered = tag.casefold()
    return any(marker in lowered for marker in ("удаленно", "удалённо", "офисе", "гибрид"))


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None


@dataclass(frozen=True)
class _IBSFilterOption:
    section: str
    slug: str
    label: str
    enabled: bool

    @property
    def match_text(self) -> str:
        return f"{self.label} {self.slug}".casefold()

    @property
    def sef_segment(self) -> str:
        if self.section == _FORMAT_SECTION:
            return f"format-is-{self.slug}"
        raise ValueError(f"unsupported IBS filter section: {self.section}")


class _IBSFilterParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.options: list[_IBSFilterOption] = []
        self._block_depth: int | None = None
        self._current_section = ""
        self._header_depth: int | None = None
        self._header_parts: list[str] = []
        self._option_depth: int | None = None
        self._option_slug: str | None = None
        self._option_enabled = True
        self._option_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if self._block_depth is None:
            if tag == "div" and "jobs-filter-block" in classes:
                self._block_depth = 1
                self._current_section = ""
            return

        if tag not in _VOID_TAGS:
            self._block_depth += 1
        if tag == "div" and "jobs-filter-block-header" in classes:
            self._header_depth = self._block_depth
            self._header_parts = []
        if tag == "label" and values.get("data-jobs-tags-checkbox"):
            self._option_depth = self._block_depth
            self._option_slug = values["data-jobs-tags-checkbox"].strip()
            self._option_enabled = "disabled" not in classes
            self._option_parts = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._header_depth is not None:
            self._header_parts.append(text)
        if self._option_depth is not None:
            self._option_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._block_depth is None:
            return
        if self._header_depth == self._block_depth and tag == "div":
            self._current_section = _squash(self._header_parts)
            self._header_depth = None
            self._header_parts = []
        if self._option_depth == self._block_depth and tag == "label":
            self._append_option()
            self._option_depth = None
            self._option_slug = None
            self._option_parts = []
        if tag not in _VOID_TAGS:
            self._block_depth -= 1
        if self._block_depth <= 0:
            self._block_depth = None
            self._current_section = ""

    def _append_option(self) -> None:
        if not self._option_slug or not self._current_section:
            return
        label = _COUNT_SUFFIX_RE.sub("", _squash(self._option_parts)).strip()
        if not label:
            return
        self.options.append(
            _IBSFilterOption(
                section=self._current_section,
                slug=self._option_slug,
                label=label,
                enabled=self._option_enabled,
            )
        )


class _IBSListItem:
    def __init__(self, *, url: str, html_id: str | None) -> None:
        self.url = url
        self.html_id = html_id
        self.title = ""
        self.description = ""
        self.location_text: str | None = ""
        self.tags: tuple[str, ...] = ()
        self._field_parts: dict[str, list[str]] = {
            "title": [],
            "description": [],
            "location": [],
            "tags": [],
        }

    def add(self, field: str, text: str) -> None:
        self._field_parts[field].append(text)

    def finish(self) -> None:
        self.title = _squash(self._field_parts["title"])
        self.description = _squash(self._field_parts["description"])
        self.location_text = _squash(self._field_parts["location"]) or None
        self.tags = tuple(
            tag.lstrip("#").strip()
            for tag in self._field_parts["tags"]
            if tag.lstrip("#").strip()
        )


class _IBSListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[_IBSListItem] = []
        self._item: _IBSListItem | None = None
        self._depth = 0
        self._field: str | None = None
        self._field_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if self._item is None:
            if tag == "a" and "jobs-item" in classes:
                self._item = _IBSListItem(
                    url=urljoin(_BASE_URL, values.get("href", "")),
                    html_id=values.get("id") or None,
                )
                self._depth = 1
            return
        if tag not in _VOID_TAGS:
            self._depth += 1
        if tag == "span" and "jobs-item-title" in classes:
            self._field = "title"
            self._field_depth = self._depth
        elif tag == "span" and "jobs-item-desc" in classes:
            self._field = "description"
            self._field_depth = self._depth
        elif tag == "span" and "jobs-item-location" in classes:
            self._field = "location"
            self._field_depth = self._depth
        elif tag == "span" and "jobs-item-tags" in classes:
            self._field = "tags"
            self._field_depth = self._depth

    def handle_data(self, data: str) -> None:
        if self._item is None or self._field is None:
            return
        text = " ".join(data.split())
        if text:
            self._item.add(self._field, text)

    def handle_endtag(self, tag: str) -> None:
        if self._item is None:
            return
        if self._field_depth == self._depth and tag == "span":
            self._field = None
            self._field_depth = None
        if tag not in _VOID_TAGS:
            self._depth -= 1
        if self._depth == 0:
            self._item.finish()
            if self._item.title and self._item.url:
                self.items.append(self._item)
            self._item = None
            self._field = None
            self._field_depth = None


class _IBSPaginationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.next_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.next_url is not None or tag != "a":
            return
        values = {key: value or "" for key, value in attrs}
        if "next" not in values.get("class", "").split():
            return
        href = values.get("href", "").strip()
        if href:
            self.next_url = urljoin(_BASE_URL, href)


class _IBSDetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: dict[str, str] = {}
        self._in_content_right = False
        self._depth = 0
        self._current_section = "Описание"
        self._section_parts: dict[str, list[str]] = {self._current_section: []}
        self._heading_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if not self._in_content_right:
            if tag == "div" and "content-right" in classes:
                self._in_content_right = True
                self._depth = 1
            return
        if tag not in _VOID_TAGS:
            self._depth += 1
        if tag == "h3":
            self._heading_parts = []
        if tag == "div" and "jobs-detail-button" in classes:
            self._in_content_right = False

    def handle_data(self, data: str) -> None:
        if not self._in_content_right:
            return
        text = " ".join(data.split())
        if not text or text == "Откликнуться на вакансию":
            return
        if self._heading_parts is not None:
            self._heading_parts.append(text)
            return
        self._section_parts.setdefault(self._current_section, []).append(text)

    def handle_endtag(self, tag: str) -> None:
        if not self._in_content_right:
            return
        if tag == "h3":
            label = _squash(self._heading_parts or [])
            self._heading_parts = None
            if label:
                self._current_section = label
                self._section_parts.setdefault(self._current_section, [])
        if tag not in _VOID_TAGS:
            self._depth -= 1
        if self._depth <= 0:
            self._in_content_right = False

    def close(self) -> None:
        super().close()
        self.sections = {
            label: _squash(parts)
            for label, parts in self._section_parts.items()
            if _squash(parts)
        }

    def description(self) -> str | None:
        self.close()
        return _join_text(*self.sections.values())

    def section(self, label: str) -> str | None:
        self.close()
        return self.sections.get(label)


def _squash(parts: list[str]) -> str:
    return " ".join(part for part in parts if part).strip()


_VOID_TAGS = {
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
