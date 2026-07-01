"""Contract-first Staff.am aggregator source."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

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
from job_harness.v2.runtime.sources._detail_html import extract_next_data, localized_html_to_text
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://staff.am"
_QA_KEYWORDS = ("qa", "test", "тест", "quality")
_DEV_KEYWORDS = (
    "developer",
    "software",
    "backend",
    "frontend",
    "fullstack",
    "python",
    "java",
    "golang",
    "go ",
    "разработ",
)


class StaffAmSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("staff_am")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("staff_am")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_search_url(query_variant),
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = extract_next_data(response.body)
        props = payload.get("props")
        if not isinstance(props, dict):
            raise ValueError("Staff.am __NEXT_DATA__ props is malformed")
        page_props = props.get("pageProps")
        if not isinstance(page_props, dict):
            raise ValueError("Staff.am __NEXT_DATA__ pageProps is malformed")

        jobs = page_props.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("Staff.am jobs payload is malformed")

        listings = tuple(
            listing
            for job in jobs
            if isinstance(job, dict)
            for listing in (_listing_from_job(job),)
            if listing is not None and _listing_matches_query(listing, request.query_variant)
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
        description = _staff_detail_description(response.body)
        if description is None:
            raise ValueError("Staff.am detail page does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
        )


def _search_url(query_variant: str) -> str:
    query = query_variant.casefold()
    if any(keyword in query for keyword in _QA_KEYWORDS):
        return f"{_BASE_URL}/en/jobs/quality-assurance"
    if any(keyword in query for keyword in _DEV_KEYWORDS):
        return f"{_BASE_URL}/en/jobs/software-development"
    return f"{_BASE_URL}/en/jobs"


def _listing_from_job(job: dict[str, Any]) -> RawListing | None:
    title = _localized(job.get("title")).strip()
    company_data = job.get("companiesStruct")
    company = _localized(company_data.get("title")).strip() if isinstance(company_data, dict) else ""
    category = job.get("category")
    slug = job.get("slug")
    if not isinstance(category, dict) or not isinstance(slug, dict):
        return None

    category_code = _text(category.get("code")).strip()
    job_slug = _localized(slug).strip()
    item_id = job.get("id")
    if not title or not category_code or not job_slug or item_id is None:
        return None

    city_data = job.get("job_city")
    city = _localized(city_data.get("title")).strip() if isinstance(city_data, dict) else ""
    activated_at = job.get("activated_at")
    posted_at: str | None = None
    if isinstance(activated_at, dict):
        posted_at = _text(activated_at.get("staffam")).strip() or None

    is_remote = bool(job.get("is_remote")) or None
    category_name = _localized(category.get("title")).strip()
    raw: dict[str, object] = {"id": item_id}
    company_facts = _company_facts(company_data)
    if company_facts:
        raw["company"] = company_facts
    if category_name:
        raw["category"] = category_name
    if bool(job.get("is_featured")):
        raw["featured"] = True
    if bool(job.get("is_hot")):
        raw["hot"] = True

    return RawListing(
        source_listing_id=str(item_id),
        title=title,
        url=f"{_BASE_URL}/en/jobs/{category_code}/{job_slug}",
        source="staff_am",
        company=company or None,
        country="Armenia",
        city=city or None,
        location_text=city or None,
        salary_text=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        posted_at=posted_at,
        remote_in_country=is_remote,
        remote_global=False if is_remote else None,
        relocation=bool(job.get("is_repat")) or None,
        native_grade=_native_grade(title),
        description=None,
        requirements=None,
        skills=(),
        raw_text=_join_text(title, company, city, category_name),
        raw=raw,
    )


def _staff_detail_description(body: str) -> str | None:
    payload = extract_next_data(body)
    props = payload.get("props")
    if not isinstance(props, dict):
        raise ValueError("Staff.am __NEXT_DATA__ props is malformed")
    page_props = props.get("pageProps")
    if not isinstance(page_props, dict):
        raise ValueError("Staff.am __NEXT_DATA__ pageProps is malformed")
    job = page_props.get("job")
    if not isinstance(job, dict):
        raise ValueError("Staff.am detail page does not contain job object")

    sections: list[str] = []
    for field in (
        "description",
        "responsibilities",
        "required_qualifications",
        "additional_information",
        "application_procedures",
    ):
        text = localized_html_to_text(job.get(field))
        if text:
            sections.append(text)
    if not sections:
        return None
    return "\n\n".join(sections)


def _company_facts(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    facts: dict[str, object] = {}
    company_id = value.get("id")
    if company_id is not None:
        facts["id"] = company_id
    slug = _localized(value.get("slug")).strip()
    if slug:
        facts["companyProfileUrl"] = f"{_BASE_URL}/en/company/{slug}"
    return facts


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
            listing.city,
            listing.location_text,
            listing.description,
            listing.requirements,
            " ".join(listing.skills),
            listing.raw_text,
            " ".join(str(value) for value in listing.raw.values()),
        )
    ).casefold()
    return any(token in searchable for token in tokens)


def _query_tokens(query: str) -> set[str]:
    return {token for token in re.findall(r"[a-zа-яё0-9+#.]+", query.casefold()) if len(token) > 1}


def _native_grade(title: str) -> str | None:
    folded = title.casefold()
    for grade in ("senior", "middle", "junior", "lead"):
        if grade in folded:
            return grade
    return None


def _localized(value: object) -> str:
    if isinstance(value, dict):
        for key in ("en", "ru", "am", "am_en"):
            text = _text(value.get(key)).strip()
            if text:
                return text
        return ""
    return _text(value)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
