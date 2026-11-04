"""Lever posting feeds, including the EU API, without employer-specific rules."""

import re
from datetime import UTC, datetime
from html import escape
from typing import Any
from urllib.parse import urlsplit

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import (
    BoardSearch,
    adapter,
    array_value,
    json_array,
    object_value,
    required_text,
    text,
)
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import country_code, employment, locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter
from src.parsers.rich_text import parse_rich_text


def _description(job: dict[str, Any]) -> str:
    parts = []
    description = text(job.get("description"))
    if description:
        parts.append(description)
    else:
        for key in ("opening", "descriptionBody"):
            if value := text(job.get(key)):
                parts.append(value)
        if not parts and (plain := text(job.get("descriptionPlain"))):
            parts.append("<p>" + escape(plain).replace("\n", "<br>") + "</p>")
    for value in array_value(job.get("lists", []), "lists"):
        section = object_value(value, "description section")
        if label := text(section.get("text")):
            parts.append(f"<h3>{escape(label)}</h3>")
        if content := text(section.get("content")):
            parts.append(f"<ul>{content}</ul>" if re.match(r"<li(?:\s|>)", content, re.I) else content)
    if additional := text(job.get("additional")):
        parts.append(additional)
    return "\n".join(parts)


class LeverSearch(BoardSearch):
    filter_names = frozenset(["location", "commitment", "team", "department", "level"])
    multi_filter_names = frozenset({"location", "commitment", "team", "department"})
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
        vacancies = []
        for value in json_array(body):
            job = object_value(value, "posting")
            identifier = required_text(job.get("id"), "id")
            categories = object_value(job.get("categories") or {}, "categories")
            url = required_text(job.get("hostedUrl"), "hostedUrl")
            native = urlsplit(url)
            endpoint = urlsplit(self.listing_url)
            board_key = endpoint.path.rsplit("/", 1)[-1]
            expected_host = (endpoint.hostname or "").replace("api.", "jobs.", 1)
            if (
                native.scheme != "https"
                or native.netloc != expected_host
                or native.path.rstrip("/") != f"/{board_key}/{identifier}"
            ):
                raise ParseError("Lever hostedUrl does not identify this board and job")
            rich = parse_rich_text(_description(job), base_url=url)
            formats = work_formats(job.get("workplaceType"))
            countries = [text(job.get("country"))]
            if "remote_country" in formats:
                # The posting's administrative country is not a remote eligibility restriction.
                labels = array_value(categories.get("allLocations", []), "allLocations")
                labels = labels or [categories.get("location")]
                countries = list(
                    dict.fromkeys(country_code(label) for label in labels if country_code(label))
                )
                if not countries:
                    countries = [None]
            places = []
            for country in countries:
                places.extend(locations(country=country, format=next(iter(formats), None)))
            commitment = text(categories.get("commitment")) or ""
            commitment = re.sub(r"\bremote\b[, ]*", "", commitment, flags=re.I).strip()
            published = None
            timestamp = job.get("createdAt")
            if timestamp is not None:
                if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                    raise ParseError("Lever createdAt must be a millisecond timestamp")
                try:
                    published = datetime.fromtimestamp(timestamp / 1000, UTC).isoformat()
                except (ValueError, OverflowError, OSError) as exc:
                    raise ParseError("Lever createdAt is outside the timestamp range") from exc
            vacancies.append(
                Vacancy(
                    id=identifier,
                    title=required_text(job.get("text"), "text"),
                    url=url,
                    company=Company(name=self.board.company),
                    location=text(categories.get("location")),
                    locations=places,
                    work_formats=formats,
                    employment=employment(commitment),
                    published_at=published,
                    description=rich.text,
                    description_html=rich.html,
                    source_data=job,
                )
            )
        return SearchPage(vacancies, total=len(vacancies))


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    url = urlsplit(board.url)
    hosts = {"jobs.lever.co", "api.lever.co", "jobs.eu.lever.co", "api.eu.lever.co"}
    pattern = r"/v0/postings/([\w.-]+)/?" if (url.hostname or "").startswith("api.") else r"/([\w.-]+)/?"
    match = re.fullmatch(pattern, url.path)
    if url.hostname not in hosts or url.port or match is None:
        raise ValueError("Expected a Lever board URL or public posting API URL")
    region = "eu." if ".eu." in url.hostname else ""
    api_host = f"api.{region}lever.co"
    key = match[1]
    search = LeverSearch(board, f"https://{api_host}/v0/postings/{key}?mode=json")
    return adapter(
        "lever",
        board,
        search,
        key=region + key,
        resource=api_host,
        allowed_hosts=(api_host, f"jobs.{region}lever.co"),
        settings=settings,
    )
