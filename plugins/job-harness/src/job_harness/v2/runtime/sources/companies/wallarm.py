"""Contract-first Wallarm career source backed by Recruitee offers JSON."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

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
from job_harness.v2.runtime.sources._html import html_to_text
from job_harness.v2.runtime.sources._url import strip_query
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BOARD_URL = "https://wallarm.recruitee.com/api/offers"
_SOURCE_ID = "career:wallarm"
_COMPANY = "Wallarm"


class WallarmCareerSource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor(_SOURCE_ID)

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds(_SOURCE_ID)

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_BOARD_URL,
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        offers = _offers(response.body)
        if not offers:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(_listing(offer) for offer in offers),
        )


def _offers(body: str) -> tuple[dict[str, Any], ...]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Wallarm Recruitee response is not a JSON object")
    offers = value.get("offers")
    if not isinstance(offers, list):
        raise ValueError("Wallarm Recruitee response does not contain an offers list")
    parsed: list[dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            raise ValueError("Wallarm Recruitee offers list contains a non-object item")
        parsed.append(offer)
    return tuple(parsed)


def _listing(offer: dict[str, Any]) -> RawListing:
    source_listing_id = _required_identifier(offer.get("id"), "id")
    title = _required_text(offer.get("title"), "title")
    url = strip_query(_required_text(offer.get("careers_url"), "careers_url"))
    description = html_to_text(_text(offer.get("description")))
    requirements = html_to_text(_text(offer.get("requirements")))
    salary = _salary(offer.get("salary"))
    work_formats = _work_formats(offer)
    cities = _cities(offer.get("locations"))
    city_text = ", ".join(cities) or _text_or_none(offer.get("city"))
    remote_locations = _remote_locations(offer.get("locations"))
    remote = _optional_bool(offer.get("remote"))
    remote_in_country = _remote_in_country(remote=remote, remote_locations=remote_locations, work_formats=work_formats)
    remote_global = _remote_global(remote=remote, remote_locations=remote_locations)
    raw: dict[str, object] = {
        "id": offer.get("id"),
        "guid": _text_or_none(offer.get("guid")),
        "slug": _text_or_none(offer.get("slug")),
        "category_code": _text_or_none(offer.get("category_code")),
        "department": _text_or_none(offer.get("department")),
        "education_code": _text_or_none(offer.get("education_code")),
        "employment_type_code": _text_or_none(offer.get("employment_type_code")),
        "experience_code": _text_or_none(offer.get("experience_code")),
        "location": _text_or_none(offer.get("location")),
        "country": _text_or_none(offer.get("country")),
        "country_code": _text_or_none(offer.get("country_code")),
        "city": _text_or_none(offer.get("city")),
        "cities": cities,
        "state_name": _text_or_none(offer.get("state_name")),
        "remote": remote,
        "hybrid": _optional_bool(offer.get("hybrid")),
        "on_site": _optional_bool(offer.get("on_site")),
        "locations": _locations(offer.get("locations")),
        "salary": salary.raw,
    }
    if work_formats:
        raw["work_format"] = work_formats
    if remote_locations:
        raw["remote_locations"] = remote_locations

    return RawListing(
        source_listing_id=source_listing_id,
        title=title,
        url=url,
        source=_SOURCE_ID,
        company=_COMPANY,
        country=_text_or_none(offer.get("country_code")),
        city=city_text,
        location_text=_text_or_none(offer.get("location")),
        salary_text=salary.text,
        salary_min=salary.minimum,
        salary_max=salary.maximum,
        salary_currency=salary.currency,
        posted_at=_posted_at(offer.get("published_at")),
        remote_in_country=remote_in_country,
        remote_global=remote_global,
        relocation=None,
        native_grade=None,
        description=description,
        requirements=requirements,
        additional_sections={},
        skills=(),
        raw_text=_join_text(
            title,
            _text_or_none(offer.get("department")),
            _text_or_none(offer.get("location")),
            " ".join(cities),
            " ".join(remote_locations),
            description,
            requirements,
        ),
        raw=raw,
    )


class _Salary:
    def __init__(
        self,
        *,
        text: str | None,
        minimum: int | None,
        maximum: int | None,
        currency: str | None,
        raw: dict[str, object | None],
    ) -> None:
        self.text = text
        self.minimum = minimum
        self.maximum = maximum
        self.currency = currency
        self.raw = raw


def _salary(value: object) -> _Salary:
    if not isinstance(value, dict):
        return _Salary(text=None, minimum=None, maximum=None, currency=None, raw={})
    minimum = _int_or_none(value.get("min"))
    maximum = _int_or_none(value.get("max"))
    currency = _text_or_none(value.get("currency"))
    period = _text_or_none(value.get("period"))
    return _Salary(
        text=_salary_text(minimum=minimum, maximum=maximum, currency=currency, period=period),
        minimum=minimum,
        maximum=maximum,
        currency=currency,
        raw={
            "min": minimum,
            "max": maximum,
            "currency": currency,
            "period": period,
        },
    )


def _salary_text(*, minimum: int | None, maximum: int | None, currency: str | None, period: str | None) -> str | None:
    if minimum is None and maximum is None:
        return None
    amount = _amount_text(minimum=minimum, maximum=maximum)
    parts = tuple(part for part in (currency, amount, period) if part)
    return " ".join(parts) or None


def _amount_text(*, minimum: int | None, maximum: int | None) -> str | None:
    if minimum is not None and maximum is not None:
        return f"{minimum}-{maximum}"
    if minimum is not None:
        return f"from {minimum}"
    if maximum is not None:
        return f"up to {maximum}"
    return None


def _work_formats(offer: dict[str, Any]) -> tuple[str, ...]:
    formats: list[str] = []
    if _optional_bool(offer.get("remote")):
        formats.append("remote")
    if _optional_bool(offer.get("hybrid")):
        formats.append("hybrid")
    if _optional_bool(offer.get("on_site")):
        formats.append("office")
    return tuple(formats)


def _remote_in_country(
    *,
    remote: bool | None,
    remote_locations: tuple[str, ...],
    work_formats: tuple[str, ...],
) -> bool | None:
    if remote is True and remote_locations:
        return True
    if "remote" in work_formats:
        return None
    if remote is False:
        return False
    return None


def _remote_global(*, remote: bool | None, remote_locations: tuple[str, ...]) -> bool | None:
    if remote is True and remote_locations:
        return False
    if remote is False:
        return False
    return None


def _remote_locations(value: object) -> tuple[str, ...]:
    codes: list[str] = []
    if not isinstance(value, list):
        return ()
    for location in value:
        if not isinstance(location, dict):
            continue
        country_code = _text_or_none(location.get("country_code"))
        country = _text_or_none(location.get("country"))
        candidate = country_code or country
        if candidate and candidate not in codes:
            codes.append(candidate)
    return tuple(codes)


def _cities(value: object) -> tuple[str, ...]:
    cities: list[str] = []
    if not isinstance(value, list):
        return ()
    for location in value:
        if not isinstance(location, dict):
            continue
        city = _text_or_none(location.get("city"))
        if city and city not in cities:
            cities.append(city)
    return tuple(cities)


def _locations(value: object) -> tuple[dict[str, str | None], ...]:
    if not isinstance(value, list):
        return ()
    locations: list[dict[str, str | None]] = []
    for location in value:
        if not isinstance(location, dict):
            continue
        locations.append(
            {
                "name": _text_or_none(location.get("name")),
                "country": _text_or_none(location.get("country")),
                "country_code": _text_or_none(location.get("country_code")),
                "city": _text_or_none(location.get("city")),
                "state": _text_or_none(location.get("state")),
            }
        )
    return tuple(locations)


def _posted_at(value: object) -> str | None:
    text = _text_or_none(value)
    if text is None:
        return None
    if not text.endswith(" UTC"):
        return text
    parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
    return parsed.isoformat().replace("+00:00", "Z")


def _required_text(value: object, field: str) -> str:
    text = _text_or_none(value)
    if text is None:
        raise ValueError(f"Wallarm Recruitee offer is missing {field}")
    return text


def _required_identifier(value: object, field: str) -> str:
    if isinstance(value, int):
        return str(value)
    return _required_text(value, field)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text_or_none(value: object) -> str | None:
    text = _text(value).strip()
    return text or None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("Wallarm Recruitee boolean field is malformed")
    return value


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise ValueError("Wallarm Recruitee salary amount is malformed")


def _join_text(*parts: object) -> str | None:
    text = "\n".join(str(part).strip() for part in parts if part and str(part).strip())
    return text or None
