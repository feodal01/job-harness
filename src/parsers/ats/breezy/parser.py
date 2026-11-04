"""Server-rendered Breezy position cards on a native public board."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, required_text
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment, locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter


def _label(card: Tag, selector: str) -> str | None:
    element = card.select_one(selector)
    return element.get_text(" ", strip=True) or None if element else None


class BreezySearch(BoardSearch):
    filter_names = frozenset(["location"])
    fields = frozenset({"title", "company", "location", "locations", "work_formats", "employment"})

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        cards = soup.select("li.position")
        if not cards:
            if "%LABEL_NO_POSITIONS%" in body or "no open positions" in soup.get_text(" ").casefold():
                return SearchPage(total=0)
            raise ParseError("Breezy page contains no position cards or explicit empty state")
        vacancies = []
        for card in cards:
            link = card.select_one('a[href^="/p/"]')
            href = str(link.get("href", "")) if link else ""
            match = re.fullmatch(r"/p/([\w-]+)", href)
            if match is None:
                raise ParseError("Breezy position has no valid /p/ link")
            title = required_text(_label(card, "h2"), "position title")
            location = _label(card, ".location span")
            kind = _label(card, ".type span")
            if kind == "%LABEL_POSITION_TYPE_FULL_TIME%":
                kind = "Full-time"
            formats = []
            metadata = " ".join(str(part) for part in card.select(".meta")).casefold()
            if "hybrid" in metadata:
                formats = work_formats("hybrid")
            elif "remote" in metadata:
                formats = work_formats("remote")
            city = location.split(",", 1)[0].strip() if location and "," in location and not formats else None
            vacancies.append(
                Vacancy(
                    id=match[1],
                    title=title,
                    url=urljoin(self.listing_url, href),
                    company=Company(name=self.board.company),
                    location=location,
                    locations=locations(city=city, format=next(iter(formats), None)),
                    work_formats=formats,
                    employment=employment(kind),
                    source_data={
                        "location": location,
                        "employment_type": kind,
                        "salary": _label(card, ".salary-range span"),
                    },
                )
            )
        return SearchPage(vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    match = re.fullmatch(r"([a-z0-9-]+)\.breezy\.hr", url.hostname or "")
    if match is None or url.port or url.path not in {"", "/"}:
        raise ValueError("Expected a native Breezy board root URL")
    search = BreezySearch(board, f"https://{url.hostname}/")
    return adapter(
        "breezy",
        board,
        search,
        key=match[1],
        resource=url.hostname or "",
        allowed_hosts=(url.hostname or "",),
        settings=settings,
    )
