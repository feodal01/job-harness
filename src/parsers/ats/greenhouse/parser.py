"""Greenhouse job-board JSON with decoded and sanitized content HTML."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit

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
from src.parsers.ats.normalization import locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter
from src.parsers.models import Location
from src.parsers.rich_text import parse_rich_text


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError("Greenhouse pagination counts must be integers")
    return value


class GreenhouseSearch(BoardSearch):
    query_parameter = "keyword"
    query_notes = "Hosted Greenhouse keyword search; empty queries use the complete public board API."
    detail_fields = frozenset({"description", "description_html", "published_at"})
    fields = frozenset(
        {
            "title",
            "company",
            "location",
            "locations",
            "work_formats",
            "description",
            "description_html",
            "published_at",
        }
    )

    def __init__(self, board: Board, listing_url: str, hosted_url: str) -> None:
        super().__init__(board, listing_url)
        self.hosted_url = hosted_url
        self.board_key = urlsplit(listing_url).path.split("/")[3]

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None:
            raise ValueError("Greenhouse has no supported native geography filter")
        if not query:
            return SearchRequest(self.listing_url, query)
        return SearchRequest(
            self.hosted_url + "?" + urlencode({"keyword": query, "_data": "routes/$url_token"}), query
        )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        payload = json_object(body)
        next_requests: tuple[SearchRequest, ...] = ()
        total: int | None = None
        if "jobPosts" in payload:
            if payload.get("urlToken") != self.board_key:
                raise ParseError("Greenhouse response changed board")
            if payload.get("keyword") != request.query:
                raise ParseError("Greenhouse response changed keyword")
            posts = object_value(payload["jobPosts"], "jobPosts")
            values = array_value(posts.get("data"), "jobPosts.data")
            counts = [_integer(posts.get(field)) for field in ("count", "page", "total", "total_pages")]
            count, current, total, pages = counts
            if (
                count != len(values)
                or current < 1
                or total < count
                or pages < 0
                or (pages == 0 and total != 0)
                or (pages > 0 and current > pages)
                or (total == 0 and (values or pages))
                or (current < pages and not values)
            ):
                raise ParseError("Greenhouse pagination counts contradict returned jobs")
            parameters = parse_qs(urlsplit(request.url).query)
            if parameters.get("page", ["1"]) != [str(current)]:
                raise ParseError("Greenhouse response changed requested page")
            if current < pages:
                parameters["page"] = [str(current + 1)]
                next_url = urlsplit(request.url)._replace(query=urlencode(parameters, doseq=True)).geturl()
                next_requests = (SearchRequest(next_url, request.query),)
        else:
            values = array_value(payload.get("jobs"), "jobs")
        vacancies = []
        for value in values:
            job = object_value(value, "job")
            identifier = job.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier):
                raise ParseError("Greenhouse job is missing id")
            url = required_text(job.get("absolute_url"), "absolute_url")
            address = urlsplit(url)
            if (
                address.scheme != "https"
                or not address.hostname
                or address.username is not None
                or address.password is not None
            ):
                raise ParseError("Greenhouse absolute_url must be a public HTTPS URL without credentials")
            board_key = self.board_key
            native_hosts = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
            if address.hostname in native_hosts:
                if (
                    address.netloc != address.hostname
                    or address.path.rstrip("/") != f"/{board_key}/jobs/{identifier}"
                ):
                    raise ParseError("Greenhouse absolute_url does not identify this board and job")
            else:
                custom_id = parse_qs(address.query, keep_blank_values=True).get("gh_jid")
                if custom_id is not None and custom_id != [str(identifier)]:
                    raise ParseError("Greenhouse custom absolute_url has no matching gh_jid")
            location_value = job.get("location")
            location = (
                text(location_value)
                if isinstance(location_value, str)
                else text(object_value(location_value or {}, "location").get("name"))
            )
            formats = []
            if location:
                if re.search(r"\bhybrid\b", location, re.I):
                    formats = work_formats("hybrid")
                elif re.search(r"\bremote\b", location, re.I):
                    formats = work_formats("remote")
            rich = parse_rich_text(unescape(text(job.get("content")) or ""), base_url=url)
            vacancies.append(
                Vacancy(
                    id=str(identifier),
                    title=required_text(job.get("title"), "title"),
                    url=url,
                    company=Company(name=text(job.get("company_name")) or self.board.company),
                    location=location,
                    locations=locations(format=next(iter(formats), None)),
                    work_formats=formats,
                    # updated_at is an edit timestamp, not publication evidence.
                    published_at=text(job.get("first_published")) or text(job.get("published_at")),
                    description=rich.text,
                    description_html=rich.html,
                    source_data=job,
                )
            )
        return SearchPage(
            vacancies, total=len(vacancies) if total is None else total, next_requests=next_requests
        )


class Details:
    fields = GreenhouseSearch.detail_fields

    def __init__(self, search: GreenhouseSearch) -> None:
        self.search = search

    def url(self, vacancy: Vacancy) -> str:
        return self.search.listing_url.split("?", 1)[0] + f"/{vacancy.id}?content=true"

    def parse(self, body: str, vacancy: Vacancy) -> Vacancy:
        job = json_object(body)
        if str(job.get("id")) != vacancy.id:
            raise ParseError("Greenhouse detail id does not match requested vacancy")
        updated = self.search.parse(json.dumps({"jobs": [job]}), self.search.build("")).vacancies[0]
        if not updated.description:
            raise ParseError("Greenhouse detail is missing content")
        return vacancy.model_copy(
            update={
                "description": updated.description,
                "description_html": updated.description_html,
                "published_at": updated.published_at or vacancy.published_at,
            }
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    api_hosts = {"boards-api.greenhouse.io"}
    board_hosts = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
    pattern = r"/v1/boards/([\w-]+)/jobs/?" if url.hostname in api_hosts else r"/([\w-]+)/?"
    match = re.fullmatch(pattern, url.path)
    if url.hostname not in api_hosts | board_hosts or url.port or match is None:
        raise ValueError("Expected a Greenhouse board URL or public board API URL")
    key = match[1]
    api_host = url.hostname if url.hostname in api_hosts else "boards-api.greenhouse.io"
    hosted_host = (
        "job-boards.eu.greenhouse.io"
        if url.hostname == "job-boards.eu.greenhouse.io"
        else "job-boards.greenhouse.io"
    )
    search = GreenhouseSearch(
        board, f"https://{api_host}/v1/boards/{key}/jobs?content=true", f"https://{hosted_host}/{key}"
    )
    return adapter(
        "greenhouse",
        board,
        search,
        key=key,
        resource=api_host,
        allowed_hosts=tuple(sorted(api_hosts | board_hosts)),
        settings=settings,
        details=Details(search),
    )
