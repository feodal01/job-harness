"""Contract-first Habr Career source."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urlparse

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
from job_harness.v2.contracts.enums import Grade
from job_harness.v2.runtime.sources._html import ClassTextCollector, ScriptCollector, html_to_text
from job_harness.v2.runtime.sources._url import absolute_url, update_query
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://career.habr.com/vacancies"
_DETAIL_BASE_URL = "https://career.habr.com"


class HabrCareerSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("habr_career")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("habr_career")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        grade_values = request.grades or (None,)
        fetch_requests: list[SourceFetchRequest] = []
        for query_variant in request.query_variants:
            for grade in grade_values:
                params = {"q": query_variant, "type": "all"}
                if grade is not None:
                    params["qid"] = str(_habr_grade_id(grade))
                fetch_requests.append(
                    SourceFetchRequest(
                        source_id=self.descriptor.source_id,
                        query_variant=query_variant,
                        url=f"{_BASE_URL}?{urlencode(params)}",
                    )
                )
        return tuple(fetch_requests)

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _extract_habr_payload(response.body)
        vacancy_block = payload.get("vacancies")
        if not isinstance(vacancy_block, dict):
            raise ValueError("Habr payload does not contain vacancies object")

        items = vacancy_block.get("list")
        meta = vacancy_block.get("meta")
        if not isinstance(items, list) or not isinstance(meta, dict):
            raise ValueError("Habr vacancies object is malformed")

        total_results = _int_value(meta.get("totalResults"))
        if total_results == 0 and not items:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        listings = tuple(_habr_listing(item) for item in items if isinstance(item, dict))
        parallel_requests = _parallel_page_requests(
            meta=meta,
            request=request,
            page_size=len(listings),
            source_limit=self.descriptor.source_limit,
        )
        next_request = None if parallel_requests else _next_page_request(meta=meta, request=request)
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=listings,
            next_request=next_request,
            parallel_requests=parallel_requests,
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
        description = _habr_detail_description(response.body)
        if description is None:
            raise ValueError("Habr detail page does not contain vacancy description")
        company_facts = _habr_detail_company_facts(response.body)
        return replace(
            listing,
            description=description,
            raw=_merge_company_facts(listing.raw, company_facts),
            raw_text=_join_text(listing.raw_text, description),
        )


def _habr_detail_description(body: str) -> str | None:
    collector = ScriptCollector()
    collector.feed(body)
    for _attrs, text in collector.scripts:
        if not text.startswith("{") or '"vacancy"' not in text:
            continue
        data = json.loads(text)
        vacancy = data.get("vacancy")
        if not isinstance(vacancy, dict):
            continue
        raw_description = vacancy.get("description")
        if isinstance(raw_description, str) and raw_description.strip():
            parsed = _habr_description_text(raw_description, vacancy)
            if parsed:
                return parsed

    dom_collector = ClassTextCollector(
        tag_name="div",
        class_name="vacancy-description__text",
    )
    dom_collector.feed(body)
    return dom_collector.text()


def _habr_detail_company_facts(body: str) -> dict[str, object]:
    facts = _habr_vacancy_company_facts(body)
    linked_data_site_url = _hiring_organization_site_url(body)
    block_facts = _habr_company_block_facts(body)
    if linked_data_site_url:
        facts["companySiteUrl"] = linked_data_site_url
    for key, value in block_facts.items():
        facts.setdefault(key, value)
    return facts


def _habr_vacancy_company_facts(body: str) -> dict[str, object]:
    collector = ScriptCollector()
    collector.feed(body)
    for _attrs, text in collector.scripts:
        if not text.startswith("{") or '"vacancy"' not in text:
            continue
        data = json.loads(text)
        vacancy = data.get("vacancy")
        if not isinstance(vacancy, dict):
            continue
        company = vacancy.get("company")
        if isinstance(company, dict):
            return _company_facts(company)
    return {}


def _hiring_organization_site_url(body: str) -> str | None:
    collector = ScriptCollector()
    collector.feed(body)
    for attrs, text in collector.scripts:
        if attrs.get("type") != "application/ld+json" or not text.startswith("{"):
            continue
        data = json.loads(text)
        if not isinstance(data, dict):
            continue
        organization = data.get("hiringOrganization")
        if not isinstance(organization, dict):
            continue
        same_as = _str_value(organization.get("sameAs")).strip()
        if same_as:
            return absolute_url(_DETAIL_BASE_URL, same_as)
    return None


def _habr_company_block_facts(body: str) -> dict[str, object]:
    collector = _HabrCompanyBlockCollector()
    collector.feed(body)
    facts: dict[str, object] = {}
    for anchor in collector.anchors:
        href = anchor.href.strip()
        if not href:
            continue
        absolute = absolute_url(_DETAIL_BASE_URL, href)
        parsed = urlparse(absolute)
        if parsed.netloc == "career.habr.com" and parsed.path.startswith("/companies/"):
            if parsed.path.endswith("/vacancies") or anchor.text.casefold().startswith("все вакансии"):
                facts["companyVacanciesUrl"] = absolute
            else:
                facts.setdefault("companyProfileUrl", absolute)
            continue
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            facts.setdefault("companySiteUrl", absolute)
    return facts


_MIN_PARSED_DESCRIPTION_CHARS = 120


def _habr_description_text(raw_description: str, vacancy: dict[str, object]) -> str | None:
    parsed = html_to_text(raw_description)
    banner = _str_value(vacancy.get("bannerDescription")).strip()
    image_urls = _image_urls_from_html(raw_description)
    sections: list[str] = []
    if parsed and len(parsed) >= _MIN_PARSED_DESCRIPTION_CHARS:
        sections.append(parsed)
    elif banner:
        sections.append(banner)
    elif parsed:
        sections.append(parsed)
    if image_urls:
        sections.append("Vacancy details are published as images:\n" + "\n".join(image_urls))
    if not sections:
        return None
    return "\n\n".join(sections)


def _image_urls_from_html(value: str) -> tuple[str, ...]:
    urls: list[str] = []
    for match in re.finditer(r"""<img[^>]+src=["']([^"']+)["']""", value, flags=re.IGNORECASE):
        url = match.group(1).strip()
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


class _HabrCompanyBlockCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[_Anchor] = []
        self._depth: int | None = None
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        classes = attr_map.get("class", "").split()
        if self._depth is None:
            if tag == "div" and "vacancy-company" in classes:
                self._depth = 1
            return
        if tag not in _VOID_TAGS:
            self._depth += 1
        if tag == "a":
            self._href = attr_map.get("href", "").strip()
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is None:
            return
        text = " ".join(data.split())
        if text:
            self._text_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._depth is None:
            return
        if tag == "a" and self._href is not None:
            self.anchors.append(_Anchor(href=self._href, text=" ".join(self._text_parts)))
            self._href = None
            self._text_parts = []
        if tag not in _VOID_TAGS:
            self._depth -= 1
        if self._depth == 0:
            self._depth = None


@dataclass(frozen=True)
class _Anchor:
    href: str
    text: str


def _extract_habr_payload(body: str) -> dict[str, Any]:
    collector = ScriptCollector()
    collector.feed(body)
    for _attrs, text in collector.scripts:
        if text.startswith("{") and '"vacancies"' in text:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
    raise ValueError("Habr response does not contain vacancy JSON payload")


def _habr_listing(item: dict[str, Any]) -> RawListing:
    source_listing_id = str(item.get("id") or "")
    href = _str_value(item.get("href"))
    title = _str_value(item.get("title")).strip()
    company = _nested_str(item, "company", "title")
    company_facts = _company_facts(item.get("company"))
    salary = item.get("salary") if isinstance(item.get("salary"), dict) else {}
    raw_locations = item.get("locations")
    locations = raw_locations if isinstance(raw_locations, list) else []
    raw_skills = item.get("skills")
    if not isinstance(raw_skills, list):
        raise ValueError("Habr vacancy item has malformed skills")
    skills = tuple(
        _str_value(skill.get("title"))
        for skill in raw_skills
        if isinstance(skill, dict) and _str_value(skill.get("title"))
    )
    city_values = tuple(
        _str_value(location.get("title"))
        for location in locations
        if isinstance(location, dict) and _str_value(location.get("title"))
    )
    posted_at = _nested_str(item, "publishedDate", "date") or None
    salary_text = _str_value(salary.get("formatted")).strip() if isinstance(salary, dict) else ""
    salary_currency = _currency(_str_value(salary.get("currency"))) if isinstance(salary, dict) else None

    raw: dict[str, object] = {
        "id": item.get("id"),
        "href": href,
        "publishedDate": item.get("publishedDate"),
        "qualification": item.get("qualification"),
    }
    if company_facts:
        raw["company"] = company_facts

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=absolute_url(_DETAIL_BASE_URL, href),
        source="habr_career",
        company=company or None,
        country="Россия",
        city=city_values[0] if city_values else None,
        location_text=", ".join(city_values) or None,
        salary_text=salary_text or None,
        salary_min=_int_value(salary.get("from")) if isinstance(salary, dict) else None,
        salary_max=_int_value(salary.get("to")) if isinstance(salary, dict) else None,
        salary_currency=salary_currency,
        posted_at=posted_at,
        remote_in_country=bool(item.get("remoteWork")),
        remote_global=None,
        relocation=None,
        native_grade=_grade_value(_str_value(item.get("qualification"))),
        description=None,
        requirements=None,
        skills=skills,
        raw_text=_join_text(title, company, ", ".join(city_values), salary_text, " ".join(skills)),
        raw=raw,
    )


def _company_facts(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    facts: dict[str, object] = {}
    company_id = _int_value(value.get("id"))
    if company_id is not None:
        facts["id"] = company_id
    for source_key, fact_key in (
        ("alias_name", "aliasName"),
        ("title", "title"),
        ("accredited", "accredited"),
    ):
        source_value = value.get(source_key)
        if isinstance(source_value, bool | str):
            facts[fact_key] = source_value
    href = _str_value(value.get("href")).strip()
    if href:
        facts["companyProfileUrl"] = absolute_url(_DETAIL_BASE_URL, href)
        facts["companyVacanciesUrl"] = absolute_url(_DETAIL_BASE_URL, f"{href.rstrip('/')}/vacancies")
    return facts


def _merge_company_facts(raw: dict[str, object], company_facts: dict[str, object]) -> dict[str, object]:
    if not company_facts:
        return raw
    existing_company = raw.get("company")
    company = dict(existing_company) if isinstance(existing_company, dict) else {}
    company.update(company_facts)
    return {**raw, "company": company}


def _next_page_request(
    *,
    meta: dict[str, Any],
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    current_page = _int_value(meta.get("currentPage")) or 1
    total_pages = _int_value(meta.get("totalPages")) or 0
    if current_page >= total_pages:
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=update_query(request.url, {"page": str(current_page + 1)}),
        method=request.method,
        headers=dict(request.headers),
        body=request.body,
    )


def _parallel_page_requests(
    *,
    meta: dict[str, Any],
    request: SourceFetchRequest,
    page_size: int,
    source_limit: int,
) -> tuple[SourceFetchRequest, ...]:
    current_page = _int_value(meta.get("currentPage")) or 1
    total_pages = _int_value(meta.get("totalPages")) or 0
    if current_page != 1 or page_size < 1 or current_page >= total_pages:
        return ()
    last_needed_page = min(total_pages, (source_limit + page_size - 1) // page_size)
    return tuple(
        SourceFetchRequest(
            source_id=request.source_id,
            query_variant=request.query_variant,
            url=update_query(request.url, {"page": str(page)}),
            method=request.method,
            headers=dict(request.headers),
            body=request.body,
        )
        for page in range(current_page + 1, last_needed_page + 1)
    )


def _habr_grade_id(grade: Grade) -> int:
    return {
        Grade.INTERN: 1,
        Grade.JUNIOR: 3,
        Grade.MIDDLE: 4,
        Grade.SENIOR: 5,
        Grade.LEAD: 6,
    }[grade]


def _grade_value(value: str) -> str | None:
    lowered = value.strip().lower()
    if not lowered:
        return None
    if lowered == "senior":
        return "senior"
    if lowered == "middle":
        return "middle"
    if lowered == "junior":
        return "junior"
    if lowered == "lead":
        return "lead"
    return lowered


def _currency(value: str) -> str | None:
    normalized = value.strip().upper()
    if not normalized:
        return None
    if normalized == "RUR":
        return "RUB"
    return normalized


def _nested_str(value: dict[str, Any], key: str, nested_key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, dict):
        return ""
    return _str_value(nested.get(nested_key)).strip()


def _str_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_value(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None


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
