"""JazzHR's public applytojob job index."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, host, required_text
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter


class JazzhrSearch(BoardSearch):
    query_parameter = "q"
    fields = frozenset({"title", "company", "location", "work_formats"})

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        vacancies = []
        for row in soup.select("#jobs_column .jobs_row"):
            link = row.select_one("a.job_title_link[href]")
            if link is None:
                raise ParseError("JazzHR row is missing its job link")
            url = urljoin(self.listing_url, str(link["href"]))
            match = re.fullmatch(r"/apply/jobs/details/([^/]+)/?", urlsplit(url).path)
            if match is None or host(url) != host(self.listing_url):
                raise ParseError("JazzHR job URL does not belong to this board")
            metadata = {}
            for node in row.select(".resumator_description"):
                label, separator, value = node.get_text(" ", strip=True).partition(":")
                if separator:
                    metadata[label.strip().casefold()] = value.strip()
            heading = row.find_previous("h3")
            if "department" not in metadata and heading and heading.find_parent(id="jobs_column"):
                metadata["department"] = heading.get_text(" ", strip=True)
            location = metadata.get("location")
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
                    source_data={"platform": "jazzhr", "id": match[1], "metadata": dict(metadata)},
                )
            )
        if not vacancies and not any(
            t in soup.get_text(" ", strip=True).casefold()
            for t in ("no jobs found", "no positions", "no open positions")
        ):
            raise ParseError("JazzHR response contains no recognized job rows")
        return SearchPage(vacancies=vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    if not re.fullmatch(r"[a-z0-9-]+\.applytojob\.com", host(board.url)) or address.path.rstrip("/") not in {
        "",
        "/apply",
        "/apply/jobs",
    }:
        raise ValueError("JazzHR requires https://<tenant>.applytojob.com/apply/jobs")
    listing = address._replace(path="/apply/jobs", query="", fragment="").geturl()
    return adapter(
        "jazzhr",
        board,
        JazzhrSearch(board, listing),
        key=host(listing),
        resource=host(listing),
        allowed_hosts=(host(listing),),
        settings=settings,
    )
