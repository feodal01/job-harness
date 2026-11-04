"""Teamtailor job cards, including native domains and explicitly selected custom boards."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, filtered_next_url, host, required_text
from src.parsers.ats.models import Board
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter


def _format(value: str) -> str | None:
    value = value.casefold()
    if value in {"hybrid", "hybrid remote"}:
        return "hybrid"
    if value in {"remote", "fully remote"}:
        return "remote_country"
    if value in {"on-site", "onsite"}:
        return "office"
    return None


class TeamtailorSearch(BoardSearch):
    query_parameter = "query"
    fields = frozenset({"title", "company", "location", "work_formats"})

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        vacancies = []
        seen = set()
        for link in soup.select('li a[href*="/jobs/"]'):
            url = urljoin(self.listing_url, str(link.get("href", "")))
            match = re.fullmatch(r"/jobs/(\d+)-[^/]+/?", urlsplit(url).path)
            if match is None:
                continue
            if host(url) != host(self.listing_url):
                raise ParseError("Teamtailor job URL changed board host")
            if match[1] in seen:
                continue
            seen.add(match[1])
            title_node = link.select_one("[title]")
            title = str(title_node["title"]) if title_node else link.get_text(" ", strip=True)
            card = link.find_parent("li")
            metadata = card.select_one("div.mt-1") if card else None
            values = []
            titled = []
            if metadata:
                for span in metadata.select("span"):
                    value = span.get_text(" ", strip=True)
                    if value and value != "·":
                        values.append(value)
                        if span.get("title"):
                            titled.append(str(span["title"]))
            workplace = next((v for v in values if _format(v)), None)
            facts = [v for v in values if v != workplace]
            location = titled[0] if titled else facts[-1] if facts else None
            department = facts[0] if len(facts) > 1 else None
            work_format = _format(workplace) if workplace else None
            vacancies.append(
                Vacancy(
                    id=match[1],
                    title=required_text(title, "title"),
                    url=urlsplit(url)._replace(query="", fragment="").geturl(),
                    company=Company(name=self.board.company),
                    location=location,
                    work_formats=[work_format] if work_format else [],
                    source_data={
                        "platform": "teamtailor",
                        "id": match[1],
                        "department": department,
                        "workplace": workplace,
                    },
                )
            )
        if not vacancies and not any(
            t in soup.get_text(" ", strip=True).casefold()
            for t in ("no jobs found", "no matching jobs", "no open positions", "aucune offre correspondante")
        ):
            raise ParseError("Teamtailor response contains no recognized job cards")
        urls = []
        current_page = parse_qs(urlsplit(request.url).query).get("page", ["1"])[-1]
        for link in soup.select('a[href*="/jobs/show_more?"]'):
            url = urljoin(request.url, str(link["href"]))
            address = urlsplit(url)
            page = parse_qs(address.query).get("page", [""])[-1]
            if address.path != "/jobs/show_more" or host(url) != host(self.listing_url):
                raise ParseError("Teamtailor pagination changed board")
            if (
                page.isdigit()
                and current_page.isdigit()
                and int(page) > int(current_page)
                and url not in urls
            ):
                urls.append(filtered_next_url(url, request, frozenset({"query"})))
        return SearchPage(
            vacancies=vacancies, next_requests=tuple(SearchRequest(u, request.query) for u in urls)
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    if address.path.rstrip("/") not in {"", "/jobs"} or address.query:
        raise ValueError("Teamtailor requires a board root or /jobs URL (native or custom domain)")
    listing = address._replace(path="/jobs", fragment="").geturl()
    return adapter(
        "teamtailor",
        board,
        TeamtailorSearch(board, listing),
        key=host(listing),
        resource=host(listing),
        allowed_hosts=(host(listing),),
        settings=settings,
    )
