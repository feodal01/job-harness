"""Schema.org JobPosting facts shared by ATS pages with verified structured markup."""

import json
from typing import Any

from bs4 import BeautifulSoup

from src.models.salary import legacy_salary  # Transitional until the ATS stages.
from src.models.vacancy import Vacancy
from src.parsers.ats.common import text
from src.parsers.ats.normalization import employment, locations
from src.parsers.interfaces import ParseError
from src.parsers.rich_text import parse_rich_text


def job_postings(body: str) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    soup = BeautifulSoup(body, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            value = json.loads(script.get_text())
        except ValueError as exc:
            raise ParseError("Invalid JSON-LD in ATS response") from exc
        postings.extend(_postings(value))
    return postings


def _postings(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [posting for item in value for posting in _postings(item)]
    if not isinstance(value, dict):
        return []
    kind = value.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    result = [value] if "JobPosting" in kinds else []
    if "@graph" in value:
        result.extend(_postings(value["@graph"]))
    return result


def enrich(vacancy: Vacancy, posting: dict[str, Any]) -> Vacancy:
    result = vacancy.model_copy(deep=True)
    description = text(posting.get("description"))
    if description:
        rich = parse_rich_text(description, base_url=vacancy.url)
        result.description, result.description_html = rich.text, rich.html
    date = text(posting.get("datePosted"))
    if date:
        result.published_at = date
    declared_employment = employment(posting.get("employmentType"))
    if declared_employment:
        result.employment = declared_employment
    remote = posting.get("jobLocationType") == "TELECOMMUTE"
    if remote and not any(fmt in result.work_formats for fmt in ("remote_country", "remote_worldwide")):
        result.work_formats.append("remote_country")
    places = posting.get("jobLocation", [])
    places = places if isinstance(places, list) else [places]
    normalized = []
    for place in places:
        if not isinstance(place, dict) or not isinstance(place.get("address"), dict):
            continue
        address = place["address"]
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        normalized.extend(locations(country=country, city=address.get("addressLocality")))
    if normalized:
        # Office addresses are not evidence of remote eligibility.
        result.locations.extend(normalized)
    eligibility = posting.get("applicantLocationRequirements", [])
    eligibility = eligibility if isinstance(eligibility, list) else [eligibility]
    if remote:
        for place in eligibility:
            if isinstance(place, dict) and place.get("@type") == "Country":
                result.locations.extend(locations(country=place.get("name"), format="remote_country"))
    result.locations = [
        place for i, place in enumerate(result.locations) if place not in result.locations[:i]
    ]
    salary = posting.get("baseSalary")
    if isinstance(salary, dict) and isinstance(salary.get("value"), dict):
        quantity = salary["value"]
        currency = text(salary.get("currency"))
        period = {"HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month", "YEAR": "year"}.get(
            str(quantity.get("unitText", "")).upper()
        )
        minimum = quantity.get("minValue", quantity.get("value"))
        maximum = quantity.get("maxValue", quantity.get("value"))
        if currency and any(type(v) in {int, float} for v in (minimum, maximum)):
            result.salary = legacy_salary(
                {
                    "from": minimum if type(minimum) in {int, float} else None,
                    "to": maximum if type(maximum) in {int, float} else None,
                    "currencyCode": currency,
                    "period": period,
                },
                hidden=False,
            )
    result.source_data = {**result.source_data, "jobposting": posting}
    return result
