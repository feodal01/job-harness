"""Jobvite public HTML boards; each show-more category is a separate request."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, filtered_next_url, host, required_text
from src.parsers.ats.models import Board
from src.parsers.blocking import classify_common
from src.parsers.interfaces import (
    ParseError,
    RequestSettings,
    ResponseClass,
    ResponseInfo,
    SearchPage,
    SearchRequest,
    SourceAdapter,
)
from src.parsers.models import Location


def classify(response: ResponseInfo) -> ResponseClass | None:
    location = next((v for k, v in response.headers.items() if k.casefold() == "location"), "")
    if any(
        urlsplit(urljoin(response.url, value)).path == "/careers/info/unavailable.html"
        for value in (response.url, location)
        if value
    ):
        return "blocked"
    return classify_common(response)


class JobviteSearch(BoardSearch):
    query_parameter = "q"
    fields = frozenset({"title", "company", "location", "work_formats"})

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        request = super().build(query, location)
        if query:
            address = urlsplit(request.url)
            return SearchRequest(
                address._replace(path=address.path.removesuffix("/jobs") + "/search").geturl(), query
            )
        return request

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        vacancies = []
        key = urlsplit(self.listing_url).path.split("/")[1]
        for table in soup.select("table.jv-job-list"):
            heading = table.find_previous("h3", class_="h2")
            department = heading.get_text(" ", strip=True) if heading else None
            for row in table.select("tr:has(td.jv-job-list-name)"):
                link = row.select_one("td.jv-job-list-name a[href]")
                if link is None:
                    raise ParseError("Jobvite row is missing a job link")
                url = urljoin(self.listing_url, str(link["href"]))
                match = re.fullmatch(rf"/{re.escape(key)}/job/([^/]+)/?", urlsplit(url).path)
                if match is None or host(url) != host(self.listing_url):
                    raise ParseError("Jobvite job URL does not belong to this board")
                node = row.select_one("td.jv-job-list-location")
                location = (
                    " ".join(node.get_text(" ", strip=True).split()).replace(" ,", ",") if node else None
                )
                evidence = (location or "").casefold()
                formats = (
                    ["hybrid"] if "hybrid" in evidence else ["remote_country"] if "remote" in evidence else []
                )
                vacancies.append(
                    Vacancy(
                        id=match[1],
                        title=required_text(link.get_text(" ", strip=True), "title"),
                        url=urlsplit(url)._replace(query="", fragment="").geturl(),
                        company=Company(name=self.board.company),
                        location=location,
                        work_formats=formats,
                        source_data={"platform": "jobvite", "id": match[1], "department": department},
                    )
                )
        if not vacancies and not any(
            t in soup.get_text(" ", strip=True).casefold()
            for t in ("no open positions", "no jobs found", "no results found")
        ):
            raise ParseError("Jobvite response contains no recognized job rows")
        urls = []
        for link in soup.select("a[href]"):
            url = urljoin(request.url, str(link["href"]))
            address = urlsplit(url)
            query = parse_qs(address.query)
            if address.path == f"/{key}/search" and query.get("c") and query.get("p"):
                if host(url) != host(self.listing_url):
                    raise ParseError("Jobvite pagination changed board host")
                url = filtered_next_url(url, request, frozenset({"q"}))
                if url != request.url and url not in urls:
                    urls.append(url)
        return SearchPage(
            vacancies=vacancies, next_requests=tuple(SearchRequest(u, request.query) for u in urls)
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    match = re.fullmatch(r"/([a-zA-Z0-9_-]+)(?:/jobs)?/?", address.path)
    if host(board.url) != "jobs.jobvite.com" or match is None:
        raise ValueError("Jobvite requires https://jobs.jobvite.com/<tenant>[/jobs]")
    listing = f"https://jobs.jobvite.com/{match[1]}/jobs"
    return adapter(
        "jobvite",
        board,
        JobviteSearch(board, listing),
        key=match[1],
        resource=host(listing),
        allowed_hosts=(host(listing),),
        settings=settings,
        classify=classify,
    )
