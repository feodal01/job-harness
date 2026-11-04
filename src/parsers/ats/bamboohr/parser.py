"""BambooHR public careers/list JSON; numeric locationType is kept as source evidence."""

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


class BambooHRSearch(BoardSearch):
    fields = frozenset({"title", "company", "location", "locations", "work_formats", "employment"})

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        payload = json_object(body)
        rows = array_value(payload.get("result"), "result")
        total = object_value(payload.get("meta") or {}, "meta").get("totalCount")
        if not rows and total != 0:
            raise ParseError("BambooHR empty result is missing an explicit zero totalCount")
        vacancies = []
        for value in rows:
            job = object_value(value, "job")
            identifier = job.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, (int, str)) or not str(identifier):
                raise ParseError("BambooHR job is missing id")
            primary = object_value(job.get("location") or {}, "location")
            ats_location = object_value(job.get("atsLocation") or {}, "atsLocation")
            city = text(primary.get("city")) or text(ats_location.get("city"))
            country = text(ats_location.get("country"))
            label = text(job.get("employmentStatusLabel")) or ""
            formats = work_formats("remote") if job.get("isRemote") is True else []
            if not formats and re.search(r"\bremote\b", label, re.I):
                formats = work_formats("remote")
            kind = re.sub(r"\bremote\b", "", label, flags=re.I).strip()
            label_parts = [city, text(primary.get("state")) or text(ats_location.get("state")), country]
            vacancies.append(
                Vacancy(
                    id=str(identifier),
                    title=required_text(job.get("jobOpeningName"), "jobOpeningName"),
                    url=f"https://{urlsplit(self.listing_url).netloc}/careers/{identifier}",
                    company=Company(name=self.board.company),
                    location=", ".join(p for p in label_parts if p) or None,
                    locations=locations(country=country, city=city, format=next(iter(formats), None)),
                    work_formats=formats,
                    employment=employment(kind),
                    source_data=job,
                )
            )
        return SearchPage(vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    match = re.fullmatch(r"([a-z0-9-]+)\.bamboohr\.com", url.hostname or "")
    if match is None or url.port or url.path.rstrip("/") not in {"", "/careers", "/careers/list", "/jobs"}:
        raise ValueError("Expected a BambooHR public careers board URL")
    search = BambooHRSearch(board, f"https://{url.hostname}/careers/list")
    return adapter(
        "bamboohr",
        board,
        search,
        key=match[1],
        resource=url.hostname or "",
        allowed_hosts=(url.hostname or "",),
        settings=settings,
    )
