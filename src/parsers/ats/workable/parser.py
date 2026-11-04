"""Workable native JSON search and the legacy public jobs.md board table."""

import re
from urllib.parse import urlsplit

from src.models.company import Company
from src.models.filters import WorkLocation
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
from src.parsers.models import Location


def _cell(value: str) -> str | None:
    return None if value.strip() in {"", "—", "-"} else value.strip()


class WorkableSearch(BoardSearch):
    query_parameter = "query"
    query_notes = "Workable public JSON keyword search with cursor pagination; empty queries use jobs.md."
    fields = frozenset(
        {"title", "company", "location", "locations", "work_formats", "employment", "published_at"}
    )

    def __init__(self, board: Board, listing_url: str) -> None:
        super().__init__(board, listing_url)
        self.slug = urlsplit(listing_url).path.split("/")[1]
        self.api_url = f"https://apply.workable.com/api/v3/accounts/{self.slug}/jobs"

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None:
            raise ValueError("Workable has no supported native geography filter")
        if not query:
            return SearchRequest(self.listing_url, query)
        return SearchRequest(self.api_url, query, method="POST", json_body={"query": query})

    def _api(self, body: str, request: SearchRequest) -> SearchPage:
        inputs = object_value(request.json_body, "Workable request body")
        if request.url != self.api_url or inputs.get("query") != request.query:
            raise ParseError("Workable request changed board or query")
        payload = json_object(body)
        values = array_value(payload.get("results"), "Workable results")
        total = payload.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < len(values):
            raise ParseError("Workable total contradicts returned jobs")
        token = payload.get("nextPage")
        if token is not None and (not isinstance(token, str) or not token.strip()):
            raise ParseError("Workable nextPage must be a nonempty cursor")
        if total > 0 and not values:
            raise ParseError("Workable returned no jobs for a positive total")
        if token is not None and total == 0:
            raise ParseError("Workable empty response has a pagination cursor")
        if not inputs.get("token") and total > len(values) and token is None:
            raise ParseError("Workable response is missing pagination cursor")
        if token is not None and token == inputs.get("token"):
            raise ParseError("Workable nextPage repeats the current cursor")
        vacancies = []
        for value in values:
            job = object_value(value, "Workable job")
            identifier = required_text(job.get("shortcode"), "shortcode")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                raise ParseError("Workable job has an invalid shortcode")
            formats = work_formats(job.get("workplace"))
            if not formats and job.get("remote") is True:
                formats = work_formats("remote")
            work_format = next(iter(formats), None)
            primary = object_value(job.get("location") or {}, "location")
            display = ", ".join(v for v in (text(primary.get("city")), text(primary.get("country"))) if v)
            places: list[WorkLocation] = []
            raw_places = job.get("locations")
            source_places = array_value(raw_places, "locations") if raw_places is not None else [primary]
            for raw_place in source_places:
                place = object_value(raw_place, "location")
                if place.get("hidden") is True:
                    continue
                normalized = locations(
                    country=place.get("countryCode") or place.get("country"),
                    city=None if work_format == "remote_country" else place.get("city"),
                    format=work_format,
                )
                places.extend(place for place in normalized if place not in places)
            kind = text(job.get("type"))
            vacancies.append(
                Vacancy(
                    id=identifier,
                    title=required_text(job.get("title"), "title"),
                    url=f"https://apply.workable.com/{self.slug}/j/{identifier}",
                    company=Company(name=self.board.company),
                    location=display or None,
                    locations=places,
                    work_formats=formats,
                    employment=employment({"full": "full_time", "part": "part_time"}.get(kind or "", kind)),
                    published_at=text(job.get("published")),
                    source_data=job,
                )
            )
        next_requests: tuple[SearchRequest, ...] = ()
        if token is not None:
            next_requests = (
                SearchRequest(
                    request.url, request.query, method="POST", json_body={**inputs, "token": token}
                ),
            )
        return SearchPage(vacancies, total=total, next_requests=next_requests)

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        if request.method == "POST":
            return self._api(body, request)
        headers: list[str] = []
        vacancies = []
        slug = urlsplit(self.listing_url).path.split("/")[1]
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line.startswith("|") or not line.endswith("|"):
                continue
            cells = [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line[1:-1])]
            if not headers:
                headers = cells
                if not {"Title", "Location", "Details"}.issubset(headers):
                    raise ParseError("Workable table is missing Title, Location or Details columns")
                continue
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if len(cells) != len(headers):
                raise ParseError("Workable table row has the wrong number of columns")
            row = dict(zip(headers, cells, strict=True))
            detail = re.fullmatch(
                r"\[View\]\(https://apply\.workable\.com/" + re.escape(slug) + r"/jobs/view/([\w-]+)\.md\)",
                row["Details"],
            )
            if detail is None:
                raise ParseError("Workable row has an invalid details link")
            location = _cell(row["Location"])
            formats = []
            clean_location = location or ""
            marker = re.search(r"\((Remote|Hybrid|On[- ]site)\)", clean_location, re.I)
            if marker:
                formats = work_formats(marker[1])
                clean_location = clean_location[: marker.start()].strip()
            city, country = None, None
            if "," in clean_location:
                city, country = (part.strip() for part in clean_location.rsplit(",", 1))
            elif country_code(clean_location):
                country = clean_location
            vacancies.append(
                Vacancy(
                    id=detail[1],
                    title=required_text(_cell(row["Title"]), "Title"),
                    url=f"https://apply.workable.com/{slug}/j/{detail[1]}",
                    company=Company(name=self.board.company),
                    location=location,
                    locations=locations(country=country, city=city, format=next(iter(formats), None)),
                    work_formats=formats,
                    employment=employment(_cell(row.get("Type", ""))),
                    published_at=_cell(row.get("Posted", "")),
                    source_data=row,
                )
            )
        if not headers:
            if re.search(
                r"This company has \d+ open positions\. Use the search endpoint to filter results\.", body
            ):
                raise ParseError(
                    "Workable returned a search directory instead of the supported jobs table; "
                    "complete board enumeration is unavailable for this response format"
                )
            if re.search(r"\b(?:no open positions|no jobs found)\b", body, re.I):
                return SearchPage(total=0)
            raise ParseError("Workable response contains no jobs table")
        return SearchPage(vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    match = re.fullmatch(r"/([\w-]+)(?:/jobs\.md)?/?", url.path)
    if url.hostname != "apply.workable.com" or url.port or match is None:
        raise ValueError("Expected a Workable board URL or jobs.md URL")
    key = match[1]
    search = WorkableSearch(board, f"https://apply.workable.com/{key}/jobs.md")
    return adapter(
        "workable",
        board,
        search,
        key=key,
        resource="apply.workable.com",
        allowed_hosts=("apply.workable.com",),
        settings=settings,
    )
