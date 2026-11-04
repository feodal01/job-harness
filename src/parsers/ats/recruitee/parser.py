"""Recruitee's public offers JSON, including explicit per-location facts."""

import re
from urllib.parse import urlsplit

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import (
    BoardSearch,
    adapter,
    array_value,
    json_object,
    object_value,
    required_text,
    text,
)
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment, locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter
from src.parsers.rich_text import parse_rich_text


class RecruiteeSearch(BoardSearch):
    fields = frozenset(
        {
            "title",
            "company",
            "location",
            "locations",
            "work_formats",
            "employment",
            "published_at",
            "description",
            "description_html",
            "salary",
        }
    )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        vacancies = []
        for value in array_value(json_object(body).get("offers"), "offers"):
            job = object_value(value, "offer")
            identifier = job.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier):
                raise ParseError("Recruitee offer is missing id")
            url = required_text(job.get("careers_url"), "careers_url")
            formats = []
            for key in ("remote", "hybrid", "on_site"):
                if job.get(key) is True:
                    formats.extend(work_formats(key))
            raw_places = array_value(job.get("locations", []), "locations") or [job]
            places = []
            location_formats: list[str | None] = list(formats) or [None]
            for raw in raw_places:
                place = object_value(raw, "location")
                for format in location_formats:
                    places.extend(
                        locations(
                            country=place.get("country_code") or place.get("country"),
                            city=place.get("city"),
                            format=format,
                        )
                    )
            markup = text(job.get("description")) or ""
            if requirements := text(job.get("requirements")):
                markup += "<h2>Requirements</h2>" + requirements
            rich = parse_rich_text(markup, base_url=url)
            published = text(job.get("published_at"))
            if published and published.endswith(" UTC"):
                published = published[:-4].replace(" ", "T") + "Z"
            kind = text(job.get("employment_type_code"))
            if kind and kind.startswith(("fulltime_", "parttime_")):
                kind = kind.split("_", 1)[0]
            raw_salary = object_value(job.get("salary") or {}, "salary")
            salary = {}
            if raw_salary.get("min") is not None or raw_salary.get("max") is not None:
                salary = {
                    "from": raw_salary.get("min"),
                    "to": raw_salary.get("max"),
                    "currencyCode": raw_salary.get("currency"),
                    "period": {"yearly": "year", "monthly": "month", "hourly": "hour"}.get(
                        text(raw_salary.get("period")) or "", raw_salary.get("period")
                    ),
                }
            vacancies.append(
                Vacancy(
                    id=str(identifier),
                    title=required_text(job.get("title"), "title"),
                    url=url,
                    company=Company(name=self.board.company),
                    location=text(job.get("location")),
                    locations=places,
                    work_formats=formats,
                    employment=employment(kind),
                    published_at=published,
                    description=rich.text,
                    description_html=rich.html,
                    salary=salary,
                    source_data=job,
                )
            )
        return SearchPage(vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    match = re.fullmatch(r"([a-z0-9-]+)\.recruitee\.com", url.hostname or "")
    if match is None or url.port or url.path.rstrip("/") not in {"", "/api/offers"}:
        raise ValueError("Expected a Recruitee subdomain board URL or offers API URL")
    search = RecruiteeSearch(board, f"https://{url.hostname}/api/offers")
    return adapter(
        "recruitee",
        board,
        search,
        key=match[1],
        resource=url.hostname or "",
        allowed_hosts=(url.hostname or "",),
        settings=settings,
    )
