"""Huntflow's rendered public vacancy cards; no API or employer detection."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, required_text
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import country_code, locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter


class HuntflowSearch(BoardSearch):
    fields = frozenset({"title", "company", "location", "locations", "work_formats"})

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        links = soup.select('article h3 a[href^="/vacancy/"]')
        if not links:
            if "current openings" in soup.get_text(" ").casefold():
                return SearchPage(total=0)
            raise ParseError("Huntflow page contains no vacancy cards or explicit empty state")
        vacancies = []
        for link in links:
            href = str(link.get("href", ""))
            match = re.fullmatch(r"/vacancy/([\w-]+)", href)
            if match is None:
                raise ParseError("Huntflow vacancy has an invalid URL")
            article = link.find_parent("article")
            info = article.find("div", class_=re.compile(r"(?:^|\s)_info_")) if article else None
            location = info.get_text(" ", strip=True) or None if info else None
            formats = work_formats("remote") if location and re.search(r"\bremote\b", location, re.I) else []
            countries = [country_code(part.strip()) for part in re.split(r"[/,]", location or "")]
            places = []
            country_codes: list[str | None] = [code for code in countries if code] or [None]
            for country in country_codes:
                places.extend(locations(country=country, format=next(iter(formats), None)))
            vacancies.append(
                Vacancy(
                    id=match[1],
                    title=required_text(link.get_text(" ", strip=True), "vacancy title"),
                    url=urljoin(self.listing_url, href),
                    company=Company(name=self.board.company),
                    location=location,
                    locations=places,
                    work_formats=formats,
                    source_data={"location": location},
                )
            )
        return SearchPage(vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    match = re.fullmatch(r"([a-z0-9-]+)\.huntflow\.(io|ru)", url.hostname or "")
    if match is None or url.port or url.path not in {"", "/"}:
        raise ValueError("Expected a native Huntflow board root URL")
    search = HuntflowSearch(board, f"https://{url.hostname}/")
    return adapter(
        "huntflow",
        board,
        search,
        key=match[1] + "." + match[2],
        resource=url.hostname or "",
        allowed_hosts=(url.hostname or "",),
        settings=settings,
    )
