"""Personio public SSR boards and JobPosting detail documents."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, host, required_text
from src.parsers.ats.jsonld import enrich, job_postings
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter


class Details:
    fields = frozenset(
        {"description", "description_html", "locations", "employment", "published_at", "salary"}
    )

    def url(self, vacancy: Vacancy) -> str:
        return vacancy.url

    def parse(self, body: str, vacancy: Vacancy) -> Vacancy:
        postings = job_postings(body)
        if len(postings) != 1:
            raise ParseError("Personio detail requires one JobPosting")
        identifier = postings[0].get("identifier")
        if isinstance(identifier, dict):
            value = str(identifier.get("value", ""))
            if value and value.split("-", 1)[0] != vacancy.source_data.get("id"):
                raise ParseError("Personio detail id does not match requested vacancy")
        required_text(postings[0].get("description"), "description")
        return enrich(vacancy, postings[0])


class PersonioSearch(BoardSearch):
    fields = frozenset({"title", "company", "location", "work_formats", "employment"})
    detail_fields = Details.fields

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        vacancies = []
        for link in soup.select('li a[href*="/job/"]'):
            url = urljoin(self.listing_url, str(link.get("href", "")))
            match = re.fullmatch(r"/job/(\d+)/?", urlsplit(url).path)
            if match is None:
                raise ParseError("Personio job URL is missing a numeric id")
            if host(url) != host(self.listing_url):
                raise ParseError("Personio job URL does not belong to this board")
            title = link.select_one(".jb-title")
            if title is None:
                raise ParseError("Personio job is missing its title")
            metadata = [" ".join(node.stripped_strings) for node in link.select('[class*="jobMetaText"]')]
            location = metadata[0] if metadata else None
            # Only the location/workplace metadata is evidence of a work arrangement.
            evidence = (location or "").casefold()
            formats = (
                ["hybrid"] if "hybrid" in evidence else ["remote_country"] if "remote" in evidence else []
            )
            vacancies.append(
                Vacancy(
                    id=match[1],
                    title=required_text(title.get_text(" ", strip=True), "title"),
                    url=urljoin(self.listing_url, match[0]),
                    company=Company(name=self.board.company),
                    location=location,
                    work_formats=formats,
                    employment=employment(metadata[1] if len(metadata) > 1 else None),
                    source_data={"platform": "personio", "id": match[1], "metadata": list(metadata)},
                )
            )
        if not vacancies and "no positions at the moment" not in soup.get_text(" ", strip=True).casefold():
            raise ParseError("Personio response contains no recognized job list")
        return SearchPage(vacancies=vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    if not re.fullmatch(r"[a-z0-9-]+\.jobs\.personio\.(?:de|com)", host(board.url)) or address.path not in {
        "",
        "/",
    }:
        raise ValueError("Personio requires https://<tenant>.jobs.personio.de/ or .com/")
    listing = address._replace(path="/", fragment="").geturl()
    return adapter(
        "personio",
        board,
        PersonioSearch(board, listing),
        key=host(listing),
        resource=host(listing),
        allowed_hosts=(host(listing),),
        settings=settings,
        details=Details(),
    )
