"""SmartRecruiters public postings, returning the next GET request to the engine."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

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
from src.parsers.ats.normalization import country_code, employment, locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(f"SmartRecruiters {field} must be an integer")
    return value


class SmartRecruitersSearch(BoardSearch):
    query_parameter = "q"
    filter_names = frozenset(["country", "region", "city", "department"])
    fields = frozenset(
        {"title", "company", "location", "locations", "work_formats", "employment", "published_at"}
    )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        payload = json_object(body)
        rows = array_value(payload.get("content"), "content")
        total, limit, offset = (_integer(payload.get(key), key) for key in ("totalFound", "limit", "offset"))
        if total < 0 or offset < 0 or limit < 1 or (not rows and offset < total):
            raise ParseError("SmartRecruiters response has inconsistent pagination")
        vacancies = []
        for value in rows:
            job = object_value(value, "posting")
            identifier = required_text(job.get("id"), "id")
            title = required_text(job.get("name"), "name")
            company = object_value(job.get("company") or {}, "company")
            location = object_value(job.get("location") or {}, "location")
            formats = []
            for key in ("remote", "hybrid"):
                if location.get(key) is True:
                    formats.extend(work_formats(key))
            city = text(location.get("city"))
            if country_code(city) == country_code(location.get("country")) and country_code(city):
                city = None
            url = text(job.get("postingUrl"))
            if not url:
                company_key = text(company.get("identifier")) or urlsplit(self.listing_url).path.split("/")[3]
                slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
                url = f"https://jobs.smartrecruiters.com/{company_key}/{identifier}-{slug}"
            vacancies.append(
                Vacancy(
                    id=identifier,
                    title=title,
                    url=url,
                    company=Company(name=text(company.get("name")) or self.board.company),
                    location=text(location.get("fullLocation")),
                    locations=locations(
                        country=location.get("country"), city=city, format=next(iter(formats), None)
                    ),
                    work_formats=formats,
                    employment=employment(
                        object_value(job.get("typeOfEmployment") or {}, "typeOfEmployment").get("label")
                    ),
                    published_at=text(job.get("releasedDate")),
                    source_data=job,
                )
            )
        next_requests: tuple[SearchRequest, ...] = ()
        next_offset = offset + len(rows)
        if next_offset < total:
            address = urlsplit(request.url)
            query = parse_qs(address.query)
            query.update({"offset": [str(next_offset)], "limit": [str(limit)]})
            next_url = urlunsplit(address._replace(query=urlencode(query, doseq=True)))
            if next_url == request.url:
                raise ParseError("SmartRecruiters pagination did not advance")
            next_requests = (SearchRequest(next_url, request.query),)
        return SearchPage(vacancies, total=total, next_requests=next_requests)


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    hosts = {"api.smartrecruiters.com", "careers.smartrecruiters.com", "jobs.smartrecruiters.com"}
    pattern = (
        r"/v1/companies/([\w-]+)/postings/?" if url.hostname == "api.smartrecruiters.com" else r"/([\w-]+)/?"
    )
    match = re.fullmatch(pattern, url.path)
    if url.hostname not in hosts or url.port or match is None:
        raise ValueError("Expected a SmartRecruiters company board or public postings API URL")
    key = match[1]
    search = SmartRecruitersSearch(
        board, f"https://api.smartrecruiters.com/v1/companies/{key}/postings?limit=100"
    )
    return adapter(
        "smartrecruiters",
        board,
        search,
        key=key,
        resource="api.smartrecruiters.com",
        allowed_hosts=tuple(sorted(hosts)),
        settings=settings,
    )
