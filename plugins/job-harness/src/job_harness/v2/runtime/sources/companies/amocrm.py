"""Contract-first amoCRM career source backed by server-rendered jobs pages."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from job_harness.v2.contracts import (
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
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://www.amocrm.ru"
_JOBS_URL = f"{_BASE_URL}/jobs/"
_SOURCE_ID = "career:amocrm"
_COMPANY = "amoCRM"
_COUNTRY = "RU"
_SECTION_MARKERS = (
    "будет плюсом",
    "вас жд",
    "задачи",
    "нам важно",
    "обучение",
    "обязанности",
    "ожидан",
    "предлагаем",
    "предстоит",
    "подходишь",
    "стек",
    "требован",
    "условия",
    "факты",
    "чем предстоит",
    "что нужно",
)
_REQUIREMENTS_MARKERS = ("ожидан", "подходишь", "требован", "важно")
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
_SKIPPED_DETAIL_TAGS = {"button", "iframe", "script", "style"}


class AmoCRMCareerSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(_SOURCE_ID)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(_SOURCE_ID)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return (
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=request.query_variants[0],
                url=_JOBS_URL,
            ),
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        parser = _AmoCRMListParser()
        parser.feed(response.body)
        if not parser.items:
            raise ValueError("amoCRM jobs page contains no vacancy cards")
        listings = tuple(_listing(item) for item in parser.items)
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
        parser = _AmoCRMDetailParser()
        parser.feed(response.body)
        description = parser.description()
        if description is None:
            raise ValueError("amoCRM detail page does not contain vacancy description")
        sections = parser.sections()
        return replace(
            listing,
            description=description,
            requirements=_requirements(sections),
            additional_sections=sections,
            raw_text=_join_text(listing.raw_text, description),
        )


@dataclass(frozen=True)
class _AmoCRMListItem:
    title: str = ""
    category: str = ""
    url: str = ""


class _AmoCRMListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[_AmoCRMListItem] = []
        self._current: _MutableListItem | None = None
        self._item_depth: int | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if self._current is None:
            if tag == "article" and "jobs__item" in classes:
                self._current = _MutableListItem()
                self._item_depth = 1
            return

        if tag not in _VOID_TAGS:
            self._item_depth = self._depth() + 1
        if tag == "h3" and "jobs__item_category" in classes:
            self._field = "category"
        elif tag == "div" and "jobs__item_name" in classes:
            self._field = "title"
        elif tag == "a" and "jobs__item_link" in classes:
            self._current.url = _absolute_url(values.get("href", ""))

    def handle_data(self, data: str) -> None:
        if self._current is None or self._field is None:
            return
        text = _squash(data)
        if text:
            self._current.append(self._field, text)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if self._field is not None and tag in {"div", "h3"}:
            self._field = None
        if tag not in _VOID_TAGS:
            self._item_depth = self._depth() - 1
        if self._depth() <= 0:
            item = self._current.item()
            if item.title and item.url:
                self.items.append(item)
            self._current = None
            self._item_depth = None
            self._field = None

    def _depth(self) -> int:
        if self._item_depth is None:
            raise ValueError("amoCRM list parser depth is unavailable outside a vacancy card")
        return self._item_depth


class _AmoCRMDetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._content_depth: int | None = None
        self._skip_depth: int | None = None
        self._strong_depth: int | None = None
        self._strong_parts: list[str] = []
        self._description_parts: list[str] = []
        self._current_section: str | None = None
        self._sections: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = values.get("class", "").split()
        if self._content_depth is None:
            if tag == "div" and "content-block__main" in classes:
                self._content_depth = 1
            return

        if tag not in _VOID_TAGS:
            self._content_depth = self._depth() + 1
        if self._skip_depth is not None:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIPPED_DETAIL_TAGS:
            self._skip_depth = self._depth()
            return
        if tag == "strong":
            self._strong_depth = self._depth()
            self._strong_parts = []

    def handle_data(self, data: str) -> None:
        if self._content_depth is None or self._skip_depth is not None:
            return
        text = _squash(data)
        if not text:
            return
        if text == ":":
            return
        self._description_parts.append(text)
        if self._strong_depth is not None:
            self._strong_parts.append(text)
        elif self._current_section is not None:
            self._sections.setdefault(self._current_section, []).append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._content_depth is None:
            return
        if self._skip_depth == self._depth():
            self._skip_depth = None
        if self._strong_depth == self._depth() and tag == "strong":
            self._set_section_from_strong_text()
            self._strong_depth = None
            self._strong_parts = []
        if tag not in _VOID_TAGS:
            self._content_depth = self._depth() - 1
        if self._depth() <= 0:
            self._content_depth = None
            self._current_section = None

    def description(self) -> str | None:
        text = "\n".join(self._description_parts).strip()
        return text or None

    def sections(self) -> dict[str, str]:
        return {
            label: "\n".join(parts).strip()
            for label, parts in self._sections.items()
            if "\n".join(parts).strip()
        }

    def _set_section_from_strong_text(self) -> None:
        strong_text = _join_text(*self._strong_parts)
        label = _section_label(strong_text)
        if label is None:
            if self._current_section is not None and strong_text is not None:
                self._sections.setdefault(self._current_section, []).append(strong_text)
            return
        self._current_section = label
        self._sections.setdefault(label, [])

    def _depth(self) -> int:
        if self._content_depth is None:
            raise ValueError("amoCRM detail parser depth is unavailable outside content")
        return self._content_depth


@dataclass
class _MutableListItem:
    url: str = ""
    parts: dict[str, list[str]] = field(
        default_factory=lambda: {
            "category": [],
            "title": [],
        }
    )

    def append(self, field_name: str, text: str) -> None:
        self.parts[field_name].append(text)

    def item(self) -> _AmoCRMListItem:
        return _AmoCRMListItem(
            title=_join_text(*self.parts["title"]) or "",
            category=_join_text(*self.parts["category"]) or "",
            url=self.url,
        )


def _listing(item: _AmoCRMListItem) -> RawListing:
    return RawListing(
        source_listing_id=_source_listing_id(item.url),
        title=item.title,
        url=item.url,
        source=_SOURCE_ID,
        company=_COMPANY,
        country=_COUNTRY,
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
        raw_text=_join_text(item.title, item.category),
        raw={
            "category": item.category,
        },
    )


def _absolute_url(value: str) -> str:
    url = urljoin(_JOBS_URL, value.strip())
    return url.split("#", 1)[0].split("?", 1)[0]


def _source_listing_id(url: str) -> str | None:
    path = urlparse(url).path.strip("/")
    if not path:
        return None
    return path.rsplit("/", 1)[-1] or None


def _requirements(sections: dict[str, str]) -> str | None:
    for label, text in sections.items():
        lowered = label.casefold()
        if any(marker in lowered for marker in _REQUIREMENTS_MARKERS):
            return text
    return None


def _section_label(value: str | None) -> str | None:
    if value is None:
        return None
    raw_label = value.strip()
    label = raw_label.rstrip(":").strip()
    lowered = label.casefold()
    if not lowered or lowered == _COMPANY.casefold():
        return None
    if raw_label.endswith(":"):
        return label
    if not any(marker in lowered for marker in _SECTION_MARKERS):
        return None
    return label


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None


def _squash(value: str) -> str:
    return " ".join(value.split())
