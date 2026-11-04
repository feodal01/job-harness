"""Y Combinator company job boards rendered through an embedded Inertia page."""

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.filters import Employment, WorkLocation
from src.models.vacancy import Vacancy
from src.parsers.ats.common import (
    BoardSearch,
    adapter,
    array_value,
    host,
    json_object,
    object_value,
    required_text,
    text,
)
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter

_EMPLOYMENT: dict[str, Employment] = {
    "Full-time": "full_time",
    "Part-time": "part_time",
    "Contract": "contract",
    "Internship": "internship",
}


class YCombinatorSearch(BoardSearch):
    fields = frozenset(
        {
            "id",
            "title",
            "url",
            "company",
            "location",
            "locations",
            "work_formats",
            "employment",
            "experience",
            "min_experience",
            "skills",
            "source_data",
        }
    )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        page = next(
            (
                json_object(str(node["data-page"]))
                for node in soup.select("[data-page]")
                if "WaasShowJobsPage" in str(node["data-page"])
            ),
            None,
        )
        if page is None:
            raise ParseError("Y Combinator response is missing WaasShowJobsPage data-page")
        props = object_value(page.get("props"), "props")
        jobs = array_value(props.get("jobPostings"), "jobPostings")
        vacancies = [self._vacancy(object_value(job, "jobPosting")) for job in jobs]
        return SearchPage(vacancies, total=len(vacancies))

    def _vacancy(self, item: dict[str, Any]) -> Vacancy:
        uid = item.get("id")
        if not isinstance(uid, (str, int)) or isinstance(uid, bool) or not str(uid).strip():
            raise ParseError("Y Combinator posting is missing id")
        location = text(item.get("location"))
        remote = re.search(r"\bRemote\b(?:\s*\(([^)]+)\))?", location or "", re.I)
        countries = re.split(r"[;/]", remote[1]) if remote and remote[1] else []
        locations = [
            WorkLocation(country=country.strip(), format="remote_country")
            for country in countries
            if re.fullmatch(r"[A-Z]{2}", country.strip())
        ]
        if remote and not locations:
            locations = [WorkLocation(format="remote_country")]
        experience = text(item.get("minExperience"))
        years = re.fullmatch(r"(\d+)\+? years?", experience or "", re.I)
        employment = _EMPLOYMENT.get(text(item.get("type")) or "")
        return Vacancy(
            id=str(uid),
            title=required_text(item.get("title"), "title"),
            url=urljoin(self.board.url, required_text(item.get("url"), "url")),
            company=Company(name=self.board.company, profile_url=self.board.url),
            location=location,
            locations=locations,
            work_formats=["remote_country"] if remote else [],
            employment=[employment] if employment else [],
            experience=experience,
            min_experience=float(years[1]) if years else None,
            skills=[
                skill for value in array_value(item.get("skills") or [], "skills") if (skill := text(value))
            ],
            source_data=item,
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    match = re.fullmatch(r"/companies/([^/]+)/jobs/?", urlsplit(board.url).path)
    if host(board.url) not in {"www.ycombinator.com", "ycombinator.com"} or match is None:
        raise ValueError("Y Combinator requires https://www.ycombinator.com/companies/<slug>/jobs")
    return adapter(
        "ycombinator",
        board,
        YCombinatorSearch(board, board.url),
        key=match[1],
        resource="ycombinator.com",
        allowed_hosts=("ycombinator.com", "www.ycombinator.com"),
        settings=settings,
    )
