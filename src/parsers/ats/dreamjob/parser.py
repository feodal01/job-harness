"""DreamJob employer vacancy boards and their JobPosting detail records."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from pydantic import JsonValue

from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.ats.common import BoardSearch, adapter, filtered_next_url, host, required_text
from src.parsers.ats.jsonld import enrich, job_postings
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import locations
from src.parsers.interfaces import ParseError, RequestSettings, SearchPage, SearchRequest, SourceAdapter
from src.parsers.rich_text import parse_rich_text


def _text(node: Tag, selector: str) -> str | None:
    child = node.select_one(selector)
    return " ".join(child.stripped_strings) or None if child else None


def _salary(value: str | None) -> dict[str, JsonValue]:
    if not value:
        return {}
    numbers = [int("".join(token.split())) for token in re.findall(r"\d[\d\s]*", value)]
    if not numbers:
        return {}
    minimum = None if value.casefold().startswith("до ") else numbers[0]
    maximum = None if value.casefold().startswith("от ") else numbers[-1]
    currency = "RUB" if any(marker in value.casefold() for marker in ("₽", "руб", "rub")) else None
    return {"from": minimum, "to": maximum, "currencyCode": currency, "period": None}


class Details:
    fields = frozenset(
        {"description", "description_html", "locations", "employment", "published_at", "salary"}
    )

    def url(self, vacancy: Vacancy) -> str:
        return vacancy.url

    def parse(self, body: str, vacancy: Vacancy) -> Vacancy:
        postings = job_postings(body)
        if len(postings) != 1:
            raise ParseError("DreamJob detail requires one JobPosting")
        required_text(postings[0].get("description"), "description")
        return enrich(vacancy, postings[0])


class DreamjobSearch(BoardSearch):
    query_parameter = "jbfrp[text]"
    fields = frozenset(
        {
            "title",
            "company",
            "location",
            "work_formats",
            "salary",
            "summary",
            "summary_html",
            "locations",
            "min_experience",
        }
    )
    detail_fields = Details.fields

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        soup = BeautifulSoup(body, "html.parser")
        vacancies = []
        key = urlsplit(self.listing_url).path.split("/")[2]
        for card in soup.select("div.vacancy:not(.vacancy__not-found)"):
            heading = card.select_one(".vacancy__head[data-link]")
            if heading is None:
                raise ParseError("DreamJob vacancy has no detail link")
            url = urljoin(self.listing_url, str(heading["data-link"]))
            match = re.fullmatch(rf"/employers/{re.escape(key)}/vakansii/(\d+)", urlsplit(url).path)
            if match is None or host(url) != host(self.listing_url):
                raise ParseError("DreamJob vacancy URL does not belong to this employer")
            location = _text(card, ".vacancy__city")
            location = location.rstrip(",") if location else None
            tags = [node.get_text(" ", strip=True) for node in card.select(".tags__item")]
            remote = any(t.casefold() in {"можно удаленно", "можно удалённо"} for t in tags)
            remote = remote or (location or "").casefold() in {"удаленно", "удалённо", "remote"}
            salary_text = _text(card, ".vacancy__salary")
            experience = next(
                (
                    tag
                    for tag in tags
                    if tag
                    in {
                        "Нет опыта",
                        "От 1 года до 3 лет",
                        "От 3 до 6 лет",
                        "Более 6 лет",
                    }
                ),
                None,
            )
            snippet = card.select_one("p.line-clamp-3")
            rich = parse_rich_text(str(snippet) if snippet else "", base_url=url)
            vacancies.append(
                Vacancy(
                    id=match[1],
                    title=required_text(heading.get_text(" ", strip=True), "title"),
                    url=urlsplit(url)._replace(query="", fragment="").geturl(),
                    company=Company(name=self.board.company),
                    location=location,
                    locations=locations(city=location)
                    if location
                    and location.casefold()
                    not in {
                        "удаленно",
                        "удалённо",
                        "remote",
                    }
                    else [],
                    experience=experience,
                    min_experience={
                        "Нет опыта": 0,
                        "От 1 года до 3 лет": 1,
                        "От 3 до 6 лет": 3,
                        "Более 6 лет": 6,
                    }.get(experience or ""),
                    min_experience_exclusive=experience == "Более 6 лет",
                    work_formats=["remote_country"] if remote else [],
                    salary=_salary(salary_text),
                    summary=rich.text,
                    summary_html=rich.html,
                    source_data={
                        "platform": "dreamjob",
                        "id": match[1],
                        "tags": list(tags),
                        "published_text": _text(card, ".vacancy__date"),
                        "salary_text": salary_text,
                    },
                )
            )
        if not vacancies:
            if soup.select_one(".vacancy__not-found .vacancy__title-not-found"):
                return SearchPage(total=0)
            raise ParseError("DreamJob response contains no recognized vacancy cards")
        current = parse_qs(urlsplit(request.url).query).get("page", ["1"])[0]
        candidates = {}
        for link in soup.select('a[href*="/vakansii?"]'):
            url = urljoin(request.url, str(link["href"]))
            address = urlsplit(url)
            page = parse_qs(address.query).get("page", [""])[0]
            if address.path != urlsplit(self.listing_url).path:
                continue
            if host(url) != host(self.listing_url):
                raise ParseError("DreamJob pagination changed board host")
            if page.isdigit() and current.isdigit() and int(page) > int(current):
                candidates[int(page)] = filtered_next_url(url, request, frozenset({"jbfrp[text]"}))
        next_requests = (SearchRequest(candidates[min(candidates)], request.query),) if candidates else ()
        return SearchPage(vacancies=vacancies, next_requests=next_requests)


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    match = re.fullmatch(r"/employers/(\d+)(?:/vakansii)?/?", address.path)
    if host(board.url) != "dreamjob.ru" or match is None:
        raise ValueError("DreamJob requires https://dreamjob.ru/employers/<id>[/vakansii]")
    listing = f"https://dreamjob.ru/employers/{match[1]}/vakansii"
    return adapter(
        "dreamjob",
        board,
        DreamjobSearch(board, listing),
        key=match[1],
        resource=host(listing),
        allowed_hosts=(host(listing),),
        settings=settings,
        details=Details(),
    )
