"""iCIMS public iframe job cards and page-number links."""

import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, host, required_text
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment, locations, work_formats
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter
from src.parsers.rich_text import parse_rich_text


class IcimsSearch(BoardSearch):
    query_parameter = "searchKeyword"
    fields = frozenset(
        {"title", "company", "location", "locations", "work_formats", "employment", "summary", "summary_html"}
    )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        vacancies = []
        for card in soup.select("ul.iCIMS_JobsTable li.iCIMS_JobCardItem"):
            link = card.select_one("a.iCIMS_Anchor[href]:has(h3)")
            if link is None:
                raise ParseError("iCIMS job card is missing its title link")
            url = urljoin(self.listing_url, str(link["href"]))
            match = re.fullmatch(r"/jobs/(\d+)/[^/]+/job/?", urlsplit(url).path)
            if match is None or host(url) != host(self.listing_url):
                raise ParseError("iCIMS job URL changed board")
            title = link.select_one("h3")
            metadata = {}
            for label in card.select(".header .field-label"):
                value = label.find_next_sibling("span")
                if value:
                    metadata[label.get_text(" ", strip=True).rstrip(":").casefold()] = value.get_text(
                        " ", strip=True
                    )
            for label in card.select("dt.iCIMS_JobHeaderField"):
                value = label.find_next_sibling("dd")
                if value:
                    metadata[label.get_text(" ", strip=True).rstrip(":").casefold()] = value.get_text(
                        " ", strip=True
                    )
            location = metadata.get("job locations") or metadata.get("location")
            # iCIMS explicitly encodes country-region-city. Preserve its capitalization.
            location_match = re.fullmatch(r"([A-Z]{2})-([^-]*)-(.+)", location or "")
            formats = work_formats(metadata.get("workplace"))
            places = (
                locations(
                    country=location_match[1],
                    city=location_match[3],
                    format=formats[0] if formats and formats[0] in {"office", "hybrid"} else None,
                )
                if location_match
                else []
            )
            employment_value = metadata.get("employment type") or metadata.get("position type")
            employment_value = {
                "Regular Full-Time": "full-time",
                "Regular Part-Time": "part-time",
                "Fixed Term Contract": "contract",
            }.get(employment_value or "", employment_value)
            snippet = card.select_one(".description")
            rich = parse_rich_text(str(snippet) if snippet else "", base_url=url)
            vacancies.append(
                Vacancy(
                    id=match[1],
                    title=required_text(title.get_text(" ", strip=True) if title else None, "title"),
                    url=urlsplit(url)._replace(query="", fragment="").geturl(),
                    company=Company(name=self.board.company),
                    location=location,
                    locations=places,
                    work_formats=formats,
                    employment=employment(employment_value),
                    summary=rich.text,
                    summary_html=rich.html,
                    source_data={"platform": "icims", "id": match[1], "metadata": dict(metadata)},
                )
            )
        if not vacancies and not any(
            t in soup.get_text(" ", strip=True).casefold()
            for t in ("no jobs found", "no matching jobs", "no jobs were found")
        ):
            raise ParseError("iCIMS response contains no recognized job cards")
        current = parse_qs(urlsplit(request.url).query).get("pr", ["0"])[0]
        pages = {}
        for link in soup.select('a[href*="pr="], link[rel~="next"][href]'):
            url = urljoin(request.url, str(link["href"]))
            address = urlsplit(url)
            number = parse_qs(address.query).get("pr", [""])[0]
            if not number.isdigit() or not current.isdigit() or int(number) <= int(current):
                continue
            if host(url) != host(self.listing_url) or address.path != "/jobs/search":
                raise ParseError("iCIMS pagination changed board")
            # Pagination UI uses empty search fields; retain active request filters.
            query = dict(parse_qsl(urlsplit(request.url).query, keep_blank_values=True))
            query.update(
                (k, v) for k, v in parse_qsl(address.query) if v and k not in {"ss", "searchKeyword"}
            )
            query.pop("ss", None)
            query["pr"] = number
            pages[int(number)] = address._replace(query=urlencode(query), fragment="").geturl()
        return SearchPage(
            vacancies=vacancies,
            next_requests=(SearchRequest(pages[min(pages)], request.query),) if pages else (),
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    if not re.fullmatch(r"[a-z0-9-]+\.icims\.com", host(board.url)) or address.path.rstrip("/") not in {
        "",
        "/jobs",
        "/jobs/search",
    }:
        raise ValueError("iCIMS requires https://<tenant>.icims.com/jobs/search")
    query = dict(parse_qsl(address.query, keep_blank_values=True))
    query.setdefault("in_iframe", "1")
    query.setdefault("ss", "1")
    listing = address._replace(path="/jobs/search", query=urlencode(query), fragment="").geturl()
    return adapter(
        "icims",
        board,
        IcimsSearch(board, listing),
        key=host(listing),
        resource=host(listing),
        allowed_hosts=(host(listing),),
        settings=settings,
    )
