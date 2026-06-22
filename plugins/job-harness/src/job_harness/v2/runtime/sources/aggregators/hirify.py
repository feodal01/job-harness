"""Contract-first Hirify aggregator source."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_API_URL = "https://api.hirify.me/api/vacancies"
_PUBLIC_BASE_URL = "https://hirify.me/jobs"
_REGION_COUNTRY = {
    "armenia": "AM",
    "azerbaijan": "AZ",
    "belarus": "BY",
    "georgia": "GE",
    "kazakhstan": "KZ",
    "kyrgyzstan": "KG",
    "moldova": "MD",
    "russia": "RU",
    "tajikistan": "TJ",
    "turkmenistan": "TM",
    "ukraine": "UA",
    "uzbekistan": "UZ",
    "united_states": "US",
}
_COUNTRY_BY_TEXT = {
    "арм": "AM",
    "armen": "AM",
    "азер": "AZ",
    "azer": "AZ",
    "беларус": "BY",
    "belarus": "BY",
    "казахстан": "KZ",
    "kazakh": "KZ",
    "киргиз": "KG",
    "кыргыз": "KG",
    "kyrgyz": "KG",
    "молдов": "MD",
    "moldov": "MD",
    "росси": "RU",
    "russia": "RU",
    "таджик": "TJ",
    "tajik": "TJ",
    "узбек": "UZ",
    "uzbek": "UZ",
    "туркмен": "TM",
    "turkmen": "TM",
    "грузи": "GE",
    "georgia": "GE",
    "украин": "UA",
    "ukrain": "UA",
}


class HirifySource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("hirify")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("hirify")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=f"{_API_URL}?{urlencode(_search_params(query_variant, request, page=1))}",
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _json_object(response.body)
        items = payload.get("data")
        if not isinstance(items, list):
            raise ValueError("Hirify payload is malformed")

        total = _positive_int(payload.get("total")) or 0
        current_page = _positive_int(payload.get("current_page")) or 1
        last_page = _positive_int(payload.get("last_page")) or 1

        if total == 0 and not items:
            if current_page > 1:
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(),
                    evidence=AttemptEvidence(multi_step_terminal=True),
                )
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        listings = tuple(
            listing
            for item in items
            if isinstance(item, dict)
            for listing in (_listing_from_item(item),)
            if listing is not None
        )

        next_request = (
            _next_page_request(request, page=current_page + 1)
            if current_page < last_page
            else None
        )

        if not listings:
            if next_request is not None:
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(),
                    next_request=next_request,
                )
            if current_page > 1:
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(),
                    evidence=AttemptEvidence(multi_step_terminal=True),
                )
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=listings,
            next_request=next_request,
        )


def _search_params(query_variant: str, request: SearchRequest, *, page: int) -> dict[str, str]:
    params = {
        "search": query_variant,
        "page": str(page),
    }
    if request.salary_from is not None:
        params["salary_from"] = str(request.salary_from)
    return params


def _next_page_request(request: SourceFetchRequest, *, page: int) -> SourceFetchRequest:
    parsed = urlparse(request.url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["page"] = str(page)
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=urlunparse(parsed._replace(query=urlencode(params))),
    )


def _listing_from_item(item: dict[str, Any]) -> RawListing | None:
    title = _text(item.get("title")).strip()
    slug = _text(item.get("slug")).strip()
    item_id = item.get("id")
    if not title or not slug or item_id is None:
        return None

    company = _company_from_item(item)
    location_text = _text(item.get("location")).strip() or None
    country = _country_from_item(item, location_text)
    work_format = item.get("work_format")
    work_format_text = " ".join(_text(value) for value in work_format) if isinstance(work_format, list) else ""
    remote = _is_remote(work_format_text) or None
    salary_min, salary_max, currency = _salary_fields(item)
    skills = _named_values(item.get("tags"))
    specializations = _named_values(item.get("specializations"))
    grades = _named_values(item.get("grades"))
    native_grade = grades[0].casefold() if grades else _text(item.get("grade")).strip().casefold() or None
    regions = _region_codes(item.get("regions"))
    description = _text(item.get("tldr")).strip() or None
    posted_at = _text(item.get("updated_at") or item.get("created_at")).strip() or None

    raw: dict[str, object] = {"id": item_id}
    if not company:
        raw["company_missing"] = True
    source_name = _text(item.get("source")).strip()
    source_secondary = _text(item.get("source_secondary")).strip()
    if source_name:
        raw["external_source"] = source_name
    if source_secondary:
        raw["external_source_secondary"] = source_secondary
    if work_format_text:
        raw["work_format"] = work_format_text.casefold()
    remote_type = _text(item.get("remote_type")).strip()
    if remote_type:
        raw["remote_type"] = remote_type
    work_type = _text(item.get("work_type")).strip()
    if work_type:
        raw["work_type"] = work_type
    if regions:
        raw["regions"] = regions
    if specializations:
        raw["specializations"] = specializations
    english_level = _text(item.get("english_level")).strip()
    if english_level:
        raw["english_level"] = english_level
    linkedin = _text(item.get("linkedin")).strip()
    if linkedin:
        raw["linkedin"] = linkedin
    application_channel = _text(item.get("application_channel")).strip()
    if application_channel:
        raw["application_channel"] = application_channel

    return RawListing(
        source_listing_id=str(item_id),
        title=title,
        url=f"{_PUBLIC_BASE_URL}/{slug}",
        source="hirify",
        company=company or None,
        country=country,
        city=location_text,
        location_text=location_text,
        salary_text=_salary_text(salary_min, salary_max, currency),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=currency,
        posted_at=posted_at,
        remote_in_country=remote,
        remote_global=remote,
        relocation=None,
        native_grade=native_grade,
        description=description,
        requirements=None,
        skills=skills,
        raw_text=_join_text(title, company, location_text, description, work_format_text, " ".join(skills)),
        raw=raw,
    )


def _company_from_item(item: dict[str, Any]) -> str:
    candidates: list[object] = [
        item.get("company_title"),
        item.get("companyName"),
        item.get("company_name"),
        item.get("employer_title"),
        item.get("employer_name"),
    ]
    for key in ("company", "employer", "organization", "recruiter"):
        nested = item.get(key)
        if isinstance(nested, dict):
            candidates.extend(
                [
                    nested.get("title"),
                    nested.get("name"),
                    nested.get("company_title"),
                    nested.get("display_name"),
                ]
            )
    for candidate in candidates:
        company = _text(candidate).strip()
        if company and company != "%hirify_global%":
            return company
    return ""


def _country_from_item(item: dict[str, Any], location_text: str | None) -> str | None:
    regions = item.get("regions")
    if isinstance(regions, list):
        for region in regions:
            if not isinstance(region, dict):
                continue
            code = _text(region.get("code")).strip().casefold()
            if code in _REGION_COUNTRY:
                return _REGION_COUNTRY[code]
            name = _text(region.get("name_en") or region.get("name")).strip()
            country = _country_from_text(name)
            if country:
                return country
    if location_text:
        return _country_from_text(location_text)
    return _country_from_text(_text(item.get("country")).strip())


def _salary_fields(item: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    salary = item.get("salary")
    if isinstance(salary, dict):
        return (
            _positive_int(salary.get("min")),
            _positive_int(salary.get("max")),
            _text(salary.get("currency")).strip() or None,
        )
    return (
        _positive_int(item.get("salary_from")),
        _positive_int(item.get("salary_to")),
        _text(item.get("currency")).strip() or None,
    )


def _region_codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    codes: list[str] = []
    for region in value:
        if not isinstance(region, dict):
            continue
        code = _text(region.get("code")).strip()
        if code:
            codes.append(code)
    return tuple(codes)


def _named_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for entry in value:
        if isinstance(entry, dict):
            name = _text(entry.get("name_en") or entry.get("name")).strip()
        else:
            name = _text(entry).strip()
        if name:
            names.append(name)
    return tuple(names)


def _json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Hirify response is not a JSON object")
    return value


def _country_from_text(text: str) -> str | None:
    folded = text.casefold()
    for marker, code in _COUNTRY_BY_TEXT.items():
        if marker in folded:
            return code
    return None


def _is_remote(text: str) -> bool:
    folded = text.casefold()
    return "remote" in folded or "удал" in folded


def _salary_text(salary_min: int | None, salary_max: int | None, currency: str | None) -> str | None:
    if salary_min is not None and salary_max is not None:
        return f"{salary_min} - {salary_max} {currency or ''}".strip()
    if salary_min is not None:
        return f"from {salary_min} {currency or ''}".strip()
    if salary_max is not None:
        return f"to {salary_max} {currency or ''}".strip()
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0 and value.is_integer():
        return int(value)
    return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
