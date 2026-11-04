"""Public Comeet boards with embedded COMPANY_POSITIONS_DATA."""

import json
import re
from html import escape
from typing import Any
from urllib.parse import urlsplit

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, array_value, host, object_value, required_text, text
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment, locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter
from src.parsers.rich_text import parse_rich_text


def _assignment(body: str, name: str) -> object:
    match = re.search(r"\b" + re.escape(name) + r"\s*=\s*", body)
    if match is None:
        raise ParseError(f"Comeet response is missing {name}")
    try:
        return json.JSONDecoder().raw_decode(body[match.end() :])[0]
    except ValueError as exc:
        raise ParseError(f"Comeet {name} is not valid JSON") from exc


class ComeetSearch(BoardSearch):
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
            "description",
            "description_html",
            "source_data",
        }
    )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        company = object_value(_assignment(body, "COMPANY_DATA"), "COMPANY_DATA")
        positions = array_value(_assignment(body, "COMPANY_POSITIONS_DATA"), "COMPANY_POSITIONS_DATA")
        vacancies = [self._vacancy(object_value(item, "position"), company) for item in positions]
        return SearchPage(vacancies, total=len(vacancies))

    def _vacancy(self, item: dict[str, Any], company: dict[str, Any]) -> Vacancy:
        uid = required_text(item.get("uid"), "uid")
        url = required_text(item.get("url_comeet_hosted_page"), "url_comeet_hosted_page")
        location = object_value(item.get("location") or {}, "location")
        country = text(location.get("country"))
        city = text(location.get("city"))
        if city and city.casefold() == "remote":
            city = None
        formats = work_formats(item.get("workplace_type"))
        if not formats and location.get("is_remote") is True:
            formats = ["remote_country"]
        raw_sections = object_value(item.get("custom_fields") or {}, "custom_fields").get("details") or []
        parts = []
        for section in array_value(raw_sections, "custom_fields.details"):
            section = object_value(section, "custom field")
            content = text(section.get("value"))
            if content:
                label = text(section.get("name"))
                parts.append((f"<h2>{escape(label)}</h2>" if label else "") + content)
        rich = parse_rich_text("\n".join(parts), base_url=url)
        return Vacancy(
            id=uid,
            title=required_text(item.get("name"), "name"),
            url=url,
            company=Company(name=self.board.company, profile_url=self.board.url),
            location=text(location.get("name")) or ", ".join(v for v in (city, country) if v) or None,
            locations=locations(country=country, city=city, format=formats[0] if formats else None),
            work_formats=formats,
            employment=employment(item.get("employment_type")),
            experience=text(item.get("experience_level")),
            description=rich.text,
            description_html=rich.html,
            source_data={"position": item, "company_uid": company.get("company_uid")},
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    match = re.fullmatch(r"/jobs/([^/]+)/([^/]+)/?", address.path)
    if host(board.url) not in {"comeet.com", "www.comeet.com"} or match is None:
        raise ValueError("Comeet requires https://www.comeet.com/jobs/<slug>/<company-uid>")
    return adapter(
        "comeet",
        board,
        ComeetSearch(board, board.url),
        key=match[2],
        resource="comeet.com",
        allowed_hosts=("comeet.com", "www.comeet.com"),
        settings=settings,
    )
