"""JOIN company pages expose listings and job details in Next.js initialState."""

import html
import re
from copy import deepcopy
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.brief import BriefInput
from src.models.company import Company
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
from src.parsers.ats.jsonld import enrich, job_postings
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment, locations, work_formats
from src.parsers.interfaces import (
    Fetch,
    ParseError,
    RequestSettings,
    SearchPage,
    SearchPlan,
    SearchRequest,
    SourceAdapter,
)
from src.parsers.models import Location

PUBLIC_JOBS_QUERY = """query PublicJobsList($input: PublicJobsQueryInput!) {
  publicJobs(input: $input) {
    items {
      id idParam title companyId createdAt workplaceType remoteType
      city { cityName countryCode countryName } country { iso3166 name }
      category { id name } employmentType { name }
    }
    pageInfo { page pageCount pageSize rowCount }
  }
}"""


def _state(body: str) -> dict[str, Any]:
    node = BeautifulSoup(body, "html.parser").select_one("script#__NEXT_DATA__")
    if node is None:
        raise ParseError("JOIN response is missing __NEXT_DATA__")
    props = object_value(json_object(node.get_text()).get("props"), "props")
    page = object_value(props.get("pageProps"), "pageProps")
    return object_value(page.get("initialState"), "initialState")


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(f"JOIN pagination {field} must be an integer")
    return value


def _optional_object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _listing(item: dict[str, Any], board: Board, listing_url: str) -> Vacancy:
    identifier = item.get("id")
    if type(identifier) not in {str, int} or not str(identifier).isdigit():
        raise ParseError("JOIN job is missing a numeric id")
    slug = required_text(item.get("idParam"), "idParam")
    match = re.fullmatch(r"([0-9]+)-[^/?#]+", slug)
    if match is None:
        raise ParseError("JOIN job slug must contain a public numeric id")
    public_id = match[1]
    city_data = _optional_object(item.get("city"))
    if not city_data:
        city_data = _optional_object(_optional_object(item.get("office")).get("city"))
    country_data = _optional_object(item.get("country"))
    city = text(city_data.get("cityName"))
    country = text(country_data.get("iso3166")) or text(city_data.get("countryCode"))
    country_name = text(country_data.get("name")) or text(city_data.get("countryName"))
    formats = work_formats(item.get("workplaceType"))
    remote_type = text(item.get("remoteType"))
    worldwide = bool(
        formats == ["remote_country"] and (remote_type or "").casefold() in {"worldwide", "global"}
    )
    if worldwide:
        formats = ["remote_worldwide"]
    work_format = formats[0] if formats else None
    location = ", ".join(v for v in (city, country_name or country) if v) or None
    if formats == ["remote_country"]:
        eligible_city = city if remote_type == "CITY" else None
        places = locations(country=country, city=eligible_city, format=work_format)
    else:
        places = locations(country=country, city=city, format=work_format, worldwide=worldwide)
    return Vacancy(
        id=public_id,
        title=required_text(item.get("title"), "title"),
        url=urljoin(listing_url + "/", slug),
        company=Company(name=board.company),
        location=location,
        locations=places,
        work_formats=formats,
        published_at=text(item.get("createdAt")),
        employment=employment(_optional_object(item.get("employmentType")).get("name")),
        source_data={
            "platform": "join",
            "id": public_id,
            "internal_id": str(identifier),
            "workplaceType": item.get("workplaceType"),
            "remoteType": remote_type,
            "category": item.get("category"),
            "employmentType": item.get("employmentType"),
            "salaryFrequency": item.get("salaryFrequency"),
        },
    )


class Details:
    fields = frozenset(
        {"description", "description_html", "published_at", "locations", "employment", "salary"}
    )

    def __init__(self, board: Board, listing_url: str) -> None:
        self.board, self.listing_url = board, listing_url

    def url(self, vacancy: Vacancy) -> str:
        return vacancy.url

    def parse(self, body: str, vacancy: Vacancy) -> Vacancy:
        job = object_value(_state(body).get("job"), "job")
        updated = _listing(job, self.board, self.listing_url)
        if updated.id != vacancy.id:
            raise ParseError("JOIN detail id does not match requested vacancy")
        postings = job_postings(body)
        if len(postings) > 1:
            raise ParseError("JOIN detail contains multiple JobPosting records")
        posting = dict(postings[0]) if postings else {}
        description = required_text(job.get("schemaDescription") or posting.get("description"), "description")
        posting["description"] = html.unescape(description)
        return enrich(updated, posting)


class JoinSearch(BoardSearch):
    filter_names = frozenset({"categoryIds"})
    multi_filter_names = filter_names
    query_notes = "JOIN categoryIds filter uses the company-scoped public GraphQL endpoint; text is local."
    fields = frozenset(
        {"title", "company", "location", "locations", "work_formats", "employment", "published_at"}
    )
    detail_fields = Details.fields

    def __init__(self, board: Board, listing_url: str) -> None:
        super().__init__(board, listing_url)
        self.company_id: int | None = None
        raw = board.native_filters.get("categoryIds", [])
        values = [raw] if isinstance(raw, str) else raw
        if any(not value.isdecimal() or int(value) <= 0 for value in values):
            raise ValueError("JOIN categoryIds must be positive numeric category IDs")
        self.category_ids = [int(value) for value in values]

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        self._company(_state(await fetch(self.listing_url)))
        return SearchPlan((self.build(""),), self.parameters)

    def _company(self, state: dict[str, Any]) -> None:
        company = object_value(state.get("company"), "company")
        if company.get("domain") != urlsplit(self.listing_url).path.rsplit("/", 1)[-1]:
            raise ParseError("JOIN preparation response changed company")
        self.company_id = _integer(company.get("id"), "company.id")
        if self.company_id <= 0:
            raise ParseError("JOIN company.id must be positive")

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None:
            raise ValueError("JOIN has no supported native geography filter")
        if not self.category_ids and self.company_id is None:
            return SearchRequest(self.listing_url, query)
        if self.company_id is None:
            raise ValueError("JOIN category filtering requires preparation to resolve companyId")
        return SearchRequest(
            "https://join.com/candidate-api/graphql",
            query,
            method="POST",
            json_body={
                "query": PUBLIC_JOBS_QUERY,
                "variables": {
                    "input": {
                        "companyId": self.company_id,
                        "paginationPage": {"page": 1, "pageSize": 50},
                        "filters": {"categoryIds": [value for value in self.category_ids]},
                    }
                },
            },
        )

    def _graphql(self, body: str, request: SearchRequest) -> SearchPage:
        payload = json_object(body)
        if payload.get("errors"):
            raise ParseError("JOIN publicJobs GraphQL returned errors")
        jobs = object_value(object_value(payload.get("data"), "data").get("publicJobs"), "publicJobs")
        items = [object_value(item, "job") for item in array_value(jobs.get("items"), "items")]
        for item in items:
            if "companyId" in item and item["companyId"] != self.company_id:
                raise ParseError("JOIN publicJobs response changed company")
        pagination = object_value(jobs.get("pageInfo"), "pageInfo")
        current = _integer(pagination.get("page"), "page")
        pages = _integer(pagination.get("pageCount"), "pageCount")
        total = _integer(pagination.get("rowCount"), "rowCount")
        size = _integer(pagination.get("pageSize"), "pageSize")
        if (
            current < 1
            or pages < 0
            or size < len(items)
            or total < len(items)
            or (pages == 0 and total != 0)
            or (pages > 0 and current > pages)
            or (total == 0 and (items or pages))
            or (current < pages and not items)
        ):
            raise ParseError("JOIN pagination counts contradict returned jobs")
        request_body = object_value(request.json_body, "request body")
        inputs = object_value(object_value(request_body.get("variables"), "variables").get("input"), "input")
        requested_page = object_value(inputs.get("paginationPage"), "paginationPage").get("page")
        if current != requested_page or inputs.get("companyId") != self.company_id:
            raise ParseError("JOIN response/request changed company or page")
        next_requests: tuple[SearchRequest, ...] = ()
        if current < pages:
            next_body = deepcopy(request_body)
            next_body["variables"]["input"]["paginationPage"]["page"] = current + 1
            next_requests = (SearchRequest(request.url, request.query, method="POST", json_body=next_body),)
        return SearchPage(
            [_listing(item, self.board, self.listing_url) for item in items],
            total=total,
            next_requests=next_requests,
        )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        if request.method == "POST":
            return self._graphql(body, request)
        state = _state(body)
        jobs = object_value(state.get("jobs"), "jobs")
        items = array_value(jobs.get("items"), "jobs.items")
        pagination = object_value(jobs.get("pagination"), "jobs.pagination")
        vacancies = [_listing(object_value(item, "job"), self.board, self.listing_url) for item in items]
        current = _integer(pagination.get("page"), "page")
        pages = _integer(pagination.get("pageCount"), "pageCount")
        total = _integer(pagination.get("total"), "total")
        if current < 1 or pages < 0 or total < len(vacancies) or (pages == 0 and total != 0):
            raise ParseError("JOIN pagination counts contradict returned jobs")
        if pages > 0 and current > pages:
            raise ParseError("JOIN current page exceeds pageCount")
        next_requests: tuple[SearchRequest, ...] = ()
        if current < pages:
            link = BeautifulSoup(body, "html.parser").select_one('a[rel~="next"][href]')
            if link is None:
                if not items:
                    raise ParseError("JOIN board has more results but returned no jobs")
                self._company(state)
                # The HTML preview and publicJobs have different page sizes. Start publicJobs
                # at page one; the engine deduplicates the preview's public vacancy IDs.
                next_requests = (self.build(request.query),)
            else:
                url = urljoin(request.url, str(link["href"]))
                if (
                    host(url) != host(self.listing_url)
                    or urlsplit(url).path != urlsplit(self.listing_url).path
                ):
                    raise ParseError("JOIN next-page link changed board")
                if url == request.url:
                    raise ParseError("JOIN next-page link repeats current page")
                next_requests = (SearchRequest(url, request.query),)
        return SearchPage(vacancies=vacancies, total=total, next_requests=next_requests)


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    if host(board.url) != "join.com" or not re.fullmatch(
        r"/(?:[a-z]{2}/)?companies/[a-zA-Z0-9_-]+/?", address.path
    ):
        raise ValueError("JOIN requires https://join.com/companies/<company>")
    listing = address._replace(path=address.path.rstrip("/"), query="", fragment="").geturl()
    return adapter(
        "join",
        board,
        JoinSearch(board, listing),
        key=address.path.strip("/"),
        resource=host(listing),
        allowed_hosts=(host(listing),),
        settings=settings,
        details=Details(board, listing),
    )
