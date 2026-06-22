"""Contract-first hh.ru source backed by HH search SSR state."""

from __future__ import annotations

import html
import json
from typing import Any, cast
from urllib.parse import urlencode

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
from job_harness.v2.runtime.sources._url import absolute_url, strip_query, update_query
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_BASE_URL = "https://hh.ru/search/vacancy"
_DETAIL_BASE_URL = "https://hh.ru"
_STATE_TEMPLATE_MARKER = 'id="HH-Lux-InitialState"'
_DEFAULT_RUSSIA_AREA_ID = "113"

_EXPERIENCE_GRADE_MAP = {
    "noExperience": "junior",
    "between1And3": "middle",
    "between3And6": "senior",
    "moreThan6": "lead",
}
_CURRENCY_MAP = {
    "RUR": "RUB",
}
_REMOTE_WORK_FORMAT = "REMOTE"


class HhRuSource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("hh_ru")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("hh_ru")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=f"{_BASE_URL}?{urlencode(_search_params(query_variant, request))}",
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        search_result = _extract_search_result(response.body)
        vacancies = _vacancies(search_result)
        total_results = _int_or_none(search_result.get("totalResults"))
        if total_results == 0 and not vacancies:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        listings = tuple(_hh_listing(vacancy) for vacancy in vacancies)
        next_request = _next_page_request(search_result=search_result, request=request)
        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=listings,
            next_request=next_request,
        )


def _search_params(query_variant: str, request: SearchRequest) -> dict[str, str]:
    params = {
        "text": query_variant,
        "area": _DEFAULT_RUSSIA_AREA_ID,
        "search_field": "name",
    }
    if request.salary_from is not None:
        params["salary"] = str(request.salary_from)
        params["only_with_salary"] = "true"
    return params


def _extract_search_result(body: str) -> dict[str, Any]:
    state = _extract_initial_state(body)
    value = state.get("vacancySearchResult")
    if not isinstance(value, dict):
        raise ValueError("HH initial state does not contain vacancySearchResult object")
    return cast(dict[str, Any], value)


def _extract_initial_state(body: str) -> dict[str, Any]:
    marker_index = body.find(_STATE_TEMPLATE_MARKER)
    if marker_index == -1:
        raise ValueError("HH response does not contain HH-Lux-InitialState template")
    content_start = body.find(">", marker_index)
    if content_start == -1:
        raise ValueError("HH initial state template start is malformed")
    content_end = body.find("</template>", content_start)
    if content_end == -1:
        raise ValueError("HH initial state template end is missing")
    value = json.loads(html.unescape(body[content_start + 1 : content_end]))
    if not isinstance(value, dict):
        raise ValueError("HH initial state is not a JSON object")
    return cast(dict[str, Any], value)


def _vacancies(search_result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    value = search_result.get("vacancies")
    if not isinstance(value, list):
        raise ValueError("HH vacancySearchResult.vacancies is not a list")
    vacancies: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("HH vacancySearchResult.vacancies contains a non-object item")
        vacancies.append(cast(dict[str, Any], item))
    return tuple(vacancies)


def _hh_listing(vacancy: dict[str, Any]) -> RawListing:
    source_listing_id = str(vacancy.get("vacancyId") or "")
    title = _text(vacancy.get("name")).strip()
    company = _nested_text(vacancy, "company", "visibleName") or _nested_text(vacancy, "company", "name")
    location = _location(vacancy)
    compensation = _compensation(vacancy.get("compensation"))
    raw_work_formats = _work_formats(vacancy.get("workFormats"))
    work_experience = _text_or_none(vacancy.get("workExperience"))

    return RawListing(
        source_listing_id=source_listing_id or None,
        title=title,
        url=_vacancy_url(vacancy, source_listing_id),
        source="hh_ru",
        company=company,
        country="RU",
        city=location.city,
        location_text=location.text,
        salary_text=compensation.text,
        salary_min=compensation.minimum,
        salary_max=compensation.maximum,
        salary_currency=compensation.currency,
        posted_at=_publication_time(vacancy.get("publicationTime")),
        remote_in_country=_REMOTE_WORK_FORMAT in raw_work_formats or _text(vacancy.get("@workSchedule")) == "remote",
        remote_global=None,
        relocation=None,
        native_grade=_native_grade(work_experience),
        description=None,
        requirements=None,
        skills=(),
        raw_text=_join_text(
            title,
            company,
            location.text,
            compensation.text,
            work_experience,
            " ".join(raw_work_formats),
        ),
        raw={
            "vacancyId": vacancy.get("vacancyId"),
            "area": vacancy.get("area"),
            "address": vacancy.get("address"),
            "workExperience": vacancy.get("workExperience"),
            "workFormats": raw_work_formats,
            "employment": vacancy.get("employment"),
            "publicationTime": vacancy.get("publicationTime"),
            "compensation": vacancy.get("compensation"),
            "creationSite": vacancy.get("creationSite"),
            "searchRid": vacancy.get("searchRid"),
        },
    )


class _Location:
    def __init__(self, *, city: str | None, text: str | None) -> None:
        self.city = city
        self.text = text


class _Compensation:
    def __init__(
        self,
        *,
        minimum: int | None,
        maximum: int | None,
        currency: str | None,
        text: str | None,
    ) -> None:
        self.minimum = minimum
        self.maximum = maximum
        self.currency = currency
        self.text = text


def _vacancy_url(vacancy: dict[str, Any], source_listing_id: str) -> str:
    desktop_url = _nested_text(vacancy, "links", "desktop")
    if desktop_url:
        return strip_query(desktop_url)
    return absolute_url(_DETAIL_BASE_URL, f"/vacancy/{source_listing_id}")


def _location(vacancy: dict[str, Any]) -> _Location:
    city = _nested_text(vacancy, "area", "name")
    address = vacancy.get("address")
    display_name = _text(address.get("displayName")).strip() if isinstance(address, dict) else ""
    return _Location(city=city or None, text=display_name or city or None)


def _compensation(value: object) -> _Compensation:
    if not isinstance(value, dict) or "noCompensation" in value:
        return _Compensation(minimum=None, maximum=None, currency=None, text=None)

    minimum = _int_or_none(value.get("from"))
    maximum = _int_or_none(value.get("to"))
    currency = _currency(_text(value.get("currencyCode")))
    gross = value.get("gross") if isinstance(value.get("gross"), bool) else None
    return _Compensation(
        minimum=minimum,
        maximum=maximum,
        currency=currency,
        text=_salary_text(minimum=minimum, maximum=maximum, currency=currency, gross=gross),
    )


def _salary_text(
    *,
    minimum: int | None,
    maximum: int | None,
    currency: str | None,
    gross: bool | None,
) -> str | None:
    if minimum is None and maximum is None:
        return None
    if minimum is not None and maximum is not None:
        amount = f"{minimum}-{maximum}"
    elif minimum is not None:
        amount = f"от {minimum}"
    else:
        amount = f"до {maximum}"
    tax_suffix = ""
    if gross is True:
        tax_suffix = ", до вычета налогов"
    if gross is False:
        tax_suffix = ", на руки"
    return f"{amount} {currency or ''}{tax_suffix}".strip()


def _work_formats(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    formats: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        elements = item.get("workFormatsElement")
        if not isinstance(elements, list):
            continue
        formats.extend(_text(element).strip() for element in elements if _text(element).strip())
    return tuple(formats)


def _publication_time(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return _text_or_none(value.get("$"))


def _native_grade(value: str | None) -> str | None:
    if value is None:
        return None
    return _EXPERIENCE_GRADE_MAP.get(value)


def _next_page_request(
    *,
    search_result: dict[str, Any],
    request: SourceFetchRequest,
) -> SourceFetchRequest | None:
    paging = search_result.get("paging")
    if not isinstance(paging, dict):
        return None
    next_page = paging.get("next")
    if not isinstance(next_page, dict) or next_page.get("disabled") is True:
        return None
    page_number = _int_or_none(next_page.get("page"))
    if page_number is None:
        return None
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=update_query(request.url, {"page": str(page_number)}),
        method=request.method,
        headers=dict(request.headers),
        body=request.body,
    )


def _nested_text(value: dict[str, Any], key: str, nested_key: str) -> str | None:
    nested = value.get(key)
    if not isinstance(nested, dict):
        return None
    return _text_or_none(nested.get(nested_key))


def _currency(value: str) -> str | None:
    normalized = value.strip().upper()
    if not normalized:
        return None
    return _CURRENCY_MAP.get(normalized, normalized)


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
