"""Contract-first Talanto aggregator source."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast
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
from job_harness.v2.runtime.sources._html import ScriptCollector, html_to_text
from job_harness.v2.runtime.sources._url import absolute_url
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://talanto.work/"
_DETAIL_BASE_URL = "https://talanto.work"
_NEXT_FLIGHT_PREFIX = "self.__next_f.push("
_NEXT_FLIGHT_PAYLOAD_INDEX = 1
_NEXT_FLIGHT_MIN_LENGTH = 2

_GRADE_MAP = {
    "intern": "intern",
    "junior": "junior",
    "mid": "middle",
    "middle": "middle",
    "senior": "senior",
    "lead": "lead",
}

_COUNTRY_BY_LOCATION_TOKEN = {
    "алматы": "KZ",
    "beograd": "RS",
    "cyprus": "CY",
    "ge": "GE",
    "gomel": "BY",
    "kyiv": "UA",
    "limassol": "CY",
    "lisbon": "PT",
    "metro manila": "PH",
    "moscow": "RU",
    "pl": "PL",
    "poznan": "PL",
    "poznań": "PL",
    "pt": "PT",
    "rs": "RS",
    "russia": "RU",
    "t'bilisi": "GE",
    "warszawa": "PL",
    "гомель": "BY",
    "київ": "UA",
    "киев": "UA",
    "москва": "RU",
    "россия": "RU",
    "ташкент": "UZ",
}
_COUNTRY_CODE_TOKENS = {"cy", "ge", "kz", "ph", "pl", "pt", "rs", "ru", "ua", "uz"}


class TalantoSource(DetailEnrichmentScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("talanto")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("talanto")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=f"{_BASE_URL}?{urlencode({'q': query_variant})}",
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        jobs, total = _extract_initial_jobs(response.body)
        if total == 0 and not jobs:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        listings = tuple(_talanto_listing(job) for job in jobs)
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
        description = _detail_description(response.body)
        if description is None:
            raise ValueError("Talanto detail page does not contain vacancy description")
        return replace(
            listing,
            description=description,
            raw_text=_join_text(listing.raw_text, description),
        )


def _detail_description(body: str) -> str | None:
    decoder = json.JSONDecoder()
    for payload in _next_flight_payloads(body):
        start = 0
        marker = '"description":'
        while True:
            index = payload.find(marker, start)
            if index == -1:
                break
            try:
                value, _end = decoder.raw_decode(payload[index + len(marker) :])
            except json.JSONDecodeError:
                start = index + len(marker)
                continue
            if isinstance(value, str) and value.startswith("<"):
                return html_to_text(value)
            start = index + len(marker)
    return None


def _extract_initial_jobs(body: str) -> tuple[tuple[dict[str, Any], ...], int]:
    decoder = json.JSONDecoder()
    for payload in _next_flight_payloads(body):
        jobs_marker = '"initialJobs":'
        jobs_index = payload.find(jobs_marker)
        if jobs_index == -1:
            continue
        jobs_start = jobs_index + len(jobs_marker)
        jobs_value, _jobs_end = decoder.raw_decode(payload[jobs_start:])
        total = _extract_initial_total(payload, decoder)
        if not isinstance(jobs_value, list):
            raise ValueError("Talanto initialJobs value is not a list")
        jobs: list[dict[str, Any]] = []
        for item in jobs_value:
            if not isinstance(item, dict):
                raise ValueError("Talanto initialJobs contains a non-object item")
            jobs.append(cast(dict[str, Any], item))
        return tuple(jobs), total
    raise ValueError("Talanto response does not contain initialJobs payload")


def _extract_initial_total(payload: str, decoder: json.JSONDecoder) -> int:
    total_marker = '"initialTotal":'
    total_index = payload.find(total_marker)
    if total_index == -1:
        raise ValueError("Talanto payload does not contain initialTotal")
    value, _end = decoder.raw_decode(payload[total_index + len(total_marker) :])
    if not isinstance(value, int):
        raise ValueError("Talanto initialTotal value is not an integer")
    return value


def _next_flight_payloads(body: str) -> tuple[str, ...]:
    collector = ScriptCollector()
    collector.feed(body)
    payloads: list[str] = []
    for _attrs, text in collector.scripts:
        if not text.startswith(_NEXT_FLIGHT_PREFIX):
            continue
        inner = text[len(_NEXT_FLIGHT_PREFIX) :].strip()
        if inner.endswith(";"):
            inner = inner[:-1].strip()
        if inner.endswith(")"):
            inner = inner[:-1].strip()
        value = json.loads(inner)
        if (
            isinstance(value, list)
            and len(value) >= _NEXT_FLIGHT_MIN_LENGTH
            and isinstance(value[_NEXT_FLIGHT_PAYLOAD_INDEX], str)
        ):
            payloads.append(value[_NEXT_FLIGHT_PAYLOAD_INDEX])
    return tuple(payloads)


def _talanto_listing(job: dict[str, Any]) -> RawListing:
    source_listing_id = _text(job.get("id")).strip()
    title = _text(job.get("title")).strip()
    company = _text_or_none(job.get("company"))
    location_text = _text_or_none(job.get("location"))
    location = _parse_location(location_text)
    remote_type = _text(job.get("remote_type")).strip().casefold()
    skills = _skills(job.get("skills"))

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=absolute_url(_DETAIL_BASE_URL, f"/jobs/{source_listing_id}"),
        source="talanto",
        company=company,
        country=location.country,
        city=location.city,
        location_text=location_text,
        salary_text=None,
        salary_min=_int_or_none(job.get("salary_min")),
        salary_max=_int_or_none(job.get("salary_max")),
        salary_currency=_text_or_none(job.get("salary_currency")),
        posted_at=_text_or_none(job.get("published_at")),
        remote_in_country=remote_type == "remote",
        remote_global=None,
        relocation=None,
        native_grade=_grade(_text(job.get("level"))),
        description=None,
        requirements=None,
        skills=skills,
        raw_text=_join_text(
            title,
            company,
            location_text,
            remote_type,
            _text(job.get("level")),
            _text(job.get("employment_type")),
            _salary_raw_text(job),
            " ".join(skills),
        ),
        raw={
            "id": job.get("id"),
            "remote_type": job.get("remote_type"),
            "level": job.get("level"),
            "employment_type": job.get("employment_type"),
            "salary_min_usd": job.get("salary_min_usd"),
            "salary_max_usd": job.get("salary_max_usd"),
            "company_id": job.get("company_id"),
            "freshness_status": job.get("freshness_status"),
            "last_verified_at": job.get("last_verified_at"),
        },
    )


class _Location:
    def __init__(self, *, city: str | None, country: str | None) -> None:
        self.city = city
        self.country = country


def _parse_location(location_text: str | None) -> _Location:
    if location_text is None:
        return _Location(city=None, country=None)
    normalized = location_text.strip()
    if not normalized or normalized.casefold() == "remote" or normalized.casefold().startswith("удал"):
        return _Location(city=None, country=_country_from_text(normalized))

    if ";" in normalized:
        parts = [part.strip() for part in normalized.split(";") if part.strip()]
        city = next((part for part in parts if not _is_country_code_token(part)), None)
        return _Location(city=city, country=_first_country_code(parts) or _country_from_text(normalized))

    if "," in normalized:
        city, country_part = (part.strip() for part in normalized.rsplit(",", 1))
        return _Location(city=city or None, country=_country_from_text(country_part) or _country_from_text(normalized))

    return _Location(city=normalized, country=_country_from_text(normalized))


def _country_from_text(value: str) -> str | None:
    folded = value.casefold()
    for token, country in _COUNTRY_BY_LOCATION_TOKEN.items():
        if token in folded:
            return country
    return None


def _is_country_code_token(value: str) -> bool:
    return value.strip().casefold() in _COUNTRY_CODE_TOKENS


def _first_country_code(parts: list[str]) -> str | None:
    for part in parts:
        token = part.strip().casefold()
        if token in _COUNTRY_CODE_TOKENS:
            return token.upper()
    return None


def _skills(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("Talanto job skills value is not a list")
    return tuple(skill for item in value for skill in (_text(item).strip(),) if skill)


def _grade(value: str) -> str | None:
    normalized = value.strip().casefold()
    if not normalized:
        return None
    return _GRADE_MAP.get(normalized, normalized)


def _salary_raw_text(job: dict[str, Any]) -> str | None:
    currency = _text_or_none(job.get("salary_currency"))
    values = tuple(
        str(value)
        for value in (_int_or_none(job.get("salary_min")), _int_or_none(job.get("salary_max")))
        if value is not None
    )
    if not values:
        return None
    return " ".join((*values, currency or ""))


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text_or_none(value: object) -> str | None:
    text = _text(value).strip()
    return text or None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
