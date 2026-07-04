"""Contract-first Hirify aggregator source."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
from job_harness.v2.runtime.sources._html import html_to_text
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_API_URL = "https://api.hirify.me/api/vacancies"
_PUBLIC_BASE_URL = "https://hirify.me/jobs"
_PARALLEL_PAGINATION_WINDOW = 3


class HirifySource(DetailEnrichmentScraper):
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

        parallel_requests = _parallel_page_requests(
            request,
            current_page=current_page,
            last_page=last_page,
            page_size=_positive_int(payload.get("per_page")) or len(items),
            source_limit=self.descriptor.source_limit,
        )
        next_request = (
            _next_page_request(request, page=current_page + 1)
            if current_page < last_page and not parallel_requests
            else None
        )

        if not listings:
            if parallel_requests:
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(),
                    parallel_requests=parallel_requests,
                )
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
            parallel_requests=parallel_requests,
        )

    def build_detail_request(self, listing: RawListing) -> SourceFetchRequest:
        listing_id = listing.source_listing_id
        if not listing_id:
            raise ValueError("Hirify detail request requires source_listing_id")
        return SourceFetchRequest(
            source_id=self.descriptor.source_id,
            query_variant=listing.title,
            url=f"{_API_URL}/{listing_id}",
        )

    def parse_detail_response(
        self,
        response: SourceResponseArtifact,
        listing: RawListing,
    ) -> RawListing:
        payload = _json_object(response.body)
        description = _detail_description(payload)
        if description is None:
            raise ValueError("Hirify detail payload does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
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


def _parallel_page_requests(
    request: SourceFetchRequest,
    *,
    current_page: int,
    last_page: int,
    page_size: int,
    source_limit: int,
) -> tuple[SourceFetchRequest, ...]:
    if page_size < 1:
        return ()
    last_needed_page = min(last_page, (source_limit + page_size - 1) // page_size)
    last_window_page = min(last_needed_page, current_page + _PARALLEL_PAGINATION_WINDOW)
    return tuple(
        _next_page_request(request, page=page)
        for page in range(current_page + 1, last_window_page + 1)
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
    work_format_values = _code_values(work_format)
    work_format_text = " ".join(work_format_values)
    remote_type = _text(item.get("remote_type")).strip().casefold()
    remote_in_country, remote_global = _remote_flags(work_format_values=work_format_values, remote_type=remote_type)
    salary_min, salary_max, currency = _salary_fields(item)
    skills = _named_values(item.get("tags"))
    specializations = _named_values(item.get("specializations"))
    grades = _named_values(item.get("grades"))
    native_grade = grades[0].casefold() if grades else _text(item.get("grade")).strip().casefold() or None
    regions = _region_codes(item.get("regions"))
    description = _text(item.get("tldr")).strip() or None
    posted_at = _text(item.get("updated_at") or item.get("created_at")).strip() or None

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
        remote_in_country=remote_in_country,
        remote_global=remote_global,
        relocation=None,
        native_grade=native_grade,
        description=description,
        requirements=None,
        skills=skills,
        raw_text=_join_text(title, company, location_text, description, country, work_format_text, " ".join(skills)),
        raw=_raw_facts_from_item(
            item,
            item_id=item_id,
            company=company,
            work_format_text=work_format_text,
            remote_type=remote_type,
            regions=regions,
            specializations=specializations,
        ),
    )


def _raw_facts_from_item(
    item: dict[str, Any],
    *,
    item_id: object,
    company: str,
    work_format_text: str,
    remote_type: str,
    regions: tuple[str, ...],
    specializations: tuple[str, ...],
) -> dict[str, object]:
    raw: dict[str, object] = {"id": item_id}
    if not company:
        raw["company_missing"] = True
    for raw_key, item_key in (
        ("external_source", "source"),
        ("external_source_secondary", "source_secondary"),
        ("work_type", "work_type"),
        ("english_level", "english_level"),
        ("linkedin", "linkedin"),
        ("application_channel", "application_channel"),
    ):
        _append_raw_text(raw, raw_key, item.get(item_key))
    for raw_key, values in (
        ("work_format", (work_format_text.casefold(),) if work_format_text else ()),
        ("remote_type", (remote_type,) if remote_type else ()),
        ("remote_restrictions", _code_values(item.get("remote_restrictions"))),
        ("excluded_locations", _code_values(item.get("excluded_locations"))),
        ("regions", regions),
        ("specializations", specializations),
    ):
        _append_raw_values(raw, raw_key, values)
    return raw


def _append_raw_text(raw: dict[str, object], key: str, value: object) -> None:
    text = _text(value).strip()
    if text:
        raw[key] = text


def _append_raw_values(raw: dict[str, object], key: str, values: tuple[str, ...]) -> None:
    if not values:
        return
    raw[key] = values[0] if key in {"remote_type", "work_format"} else values


def _detail_description(payload: dict[str, Any]) -> str | None:
    text = _text(payload.get("text")).strip()
    if text:
        return html_to_text(text) or text
    tldr = _text(payload.get("tldr")).strip()
    return tldr or None


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
            code = _text(region.get("code")).strip()
            if code:
                return code
            name = _text(region.get("name_en") or region.get("name")).strip()
            if name:
                return name
    if location_text:
        return location_text
    return _text(item.get("country")).strip() or None


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


def _code_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        text
        for entry in value
        for text in (_text(entry).strip().casefold(),)
        if text
    )


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


def _remote_flags(
    *,
    work_format_values: tuple[str, ...],
    remote_type: str,
) -> tuple[bool | None, bool | None]:
    if "remote" in work_format_values:
        return True, remote_type == "global" if remote_type else None
    if work_format_values:
        return False, False
    return None, None


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
