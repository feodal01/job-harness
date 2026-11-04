"""Ashby's public posting feed; all locations and descriptions come from each job."""

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


class AshbySearch(BoardSearch):
    fields = frozenset(
        {
            "title",
            "company",
            "location",
            "locations",
            "work_formats",
            "employment",
            "description",
            "description_html",
            "published_at",
        }
    )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        jobs = array_value(json_object(body).get("jobs"), "jobs")
        vacancies = []
        for value in jobs:
            job = object_value(value, "job")
            if job.get("isListed") is False:
                continue
            identifier = required_text(job.get("id"), "id")
            url = required_text(job.get("jobUrl"), "jobUrl")
            native = urlsplit(url)
            board_key = urlsplit(self.listing_url).path.rsplit("/", 1)[-1]
            if (
                native.scheme != "https"
                or native.netloc != "jobs.ashbyhq.com"
                or native.path.rstrip("/") != f"/{board_key}/{identifier}"
            ):
                raise ParseError("Ashby jobUrl does not identify this board and job")
            rich = parse_rich_text(text(job.get("descriptionHtml")) or "", base_url=url)
            formats = work_formats(job.get("workplaceType"))
            if not formats and job.get("isRemote") is True:
                formats = work_formats("remote")
            raw_places = [job, *array_value(job.get("secondaryLocations", []), "secondaryLocations")]
            names, places = [], []
            for raw in raw_places:
                place = object_value(raw, "location")
                name = text(place.get("location"))
                if name:
                    names.append(name)
                address = object_value(place.get("address") or {}, "address")
                postal = object_value(address.get("postalAddress") or {}, "postalAddress")
                places.extend(
                    locations(
                        country=postal.get("addressCountry"),
                        city=postal.get("addressLocality"),
                        format=next(iter(formats), None),
                    )
                )
            vacancies.append(
                Vacancy(
                    id=identifier,
                    title=required_text(job.get("title"), "title"),
                    url=url,
                    company=Company(name=self.board.company),
                    location="; ".join(names) or None,
                    locations=places,
                    work_formats=formats,
                    employment=employment(job.get("employmentType")),
                    published_at=text(job.get("publishedAt")),
                    description=rich.text,
                    description_html=rich.html,
                    source_data=job,
                )
            )
        return SearchPage(vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    pattern = r"/([\w-]+)/?" if url.hostname == "jobs.ashbyhq.com" else r"/posting-api/job-board/([\w-]+)/?"
    match = re.fullmatch(pattern, url.path)
    if url.hostname not in {"jobs.ashbyhq.com", "api.ashbyhq.com"} or url.port or match is None:
        raise ValueError("Expected an Ashby board URL or public posting API URL")
    key = match[1]
    search = AshbySearch(board, f"https://api.ashbyhq.com/posting-api/job-board/{key}")
    return adapter(
        "ashby",
        board,
        search,
        key=key,
        resource="api.ashbyhq.com",
        allowed_hosts=("api.ashbyhq.com", "jobs.ashbyhq.com"),
        settings=settings,
    )
