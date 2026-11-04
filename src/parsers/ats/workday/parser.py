"""Public Workday CXS boards: JSON POST search and JSON GET job details."""

import re
from typing import Any
from urllib.parse import urlsplit

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
from src.parsers.ats.models import Board
from src.parsers.ats.normalization import employment, locations, work_formats
from src.parsers.interfaces import (
    Fetch,
    ParameterMode,
    ParseError,
    RequestSettings,
    SearchPage,
    SearchPlan,
    SearchRequest,
    SourceAdapter,
    filter_parameters,
)
from src.parsers.models import Location
from src.parsers.rich_text import parse_rich_text


class WorkdaySearch(BoardSearch):
    fields = frozenset({"id", "title", "url", "company", "location", "employment", "source_data"})
    query_notes = "Workday searchText applies each brief query; remaining conditions use common filters."

    def __init__(self, board: Board, base: str, tenant: str, site: str) -> None:
        self.base, self.tenant, self.site = base, tenant, site
        super().__init__(board, f"{base}/wday/cxs/{tenant}/{site}/jobs")
        self.detail_fields = WorkdayDetails.fields

    @property
    def parameters(self) -> dict[str, ParameterMode]:
        return filter_parameters(self.fields | self.detail_fields, {"query": "native"})

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        return SearchPlan(tuple(self.build(query) for query in brief.queries or [""]), self.parameters)

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None:
            raise ValueError("Workday adapter has no native geography filter")
        return self._request(query, 0)

    def _request(self, query: str, offset: int) -> SearchRequest:
        return SearchRequest(
            self.listing_url,
            query,
            method="POST",
            json_body={
                "appliedFacets": {},
                "limit": 20,
                "offset": offset,
                "searchText": query,
            },
        )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        payload = json_object(body)
        postings = array_value(payload.get("jobPostings"), "jobPostings")
        total = payload.get("total")
        if type(total) is not int or total < 0:
            raise ParseError("Workday response is missing a nonnegative total")
        offset = (request.json_body or {}).get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ParseError("Workday request offset must be a nonnegative integer")
        if not postings and offset < total:
            raise ParseError("Workday returned an empty page before total was reached")
        vacancies = [self._vacancy(object_value(item, "jobPosting")) for item in postings]
        next_offset = offset + len(postings)
        next_requests = (self._request(request.query, next_offset),) if next_offset < total else ()
        return SearchPage(vacancies, total=total, next_requests=next_requests)

    def _vacancy(self, item: dict[str, Any]) -> Vacancy:
        path = required_text(item.get("externalPath"), "externalPath")
        if not path.startswith("/job/") or urlsplit(path).netloc:
            raise ParseError("Workday externalPath must be a relative /job/ path")
        # Requisition identity belongs to the URL, while bulletFields are configurable labels.
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        uid = slug.rsplit("_", 1)[-1]
        return Vacancy(
            id=uid,
            title=required_text(item.get("title"), "title"),
            url=f"{self.base}/{self.site}{path}",
            company=Company(name=self.board.company, profile_url=self.board.url),
            location=text(item.get("locationsText")),
            employment=employment(item.get("timeType")),
            source_data=item,
        )


class WorkdayDetails:
    fields = frozenset(
        {
            "title",
            "description",
            "description_html",
            "location",
            "locations",
            "work_formats",
            "employment",
            "published_at",
            "source_data",
        }
    )

    def __init__(self, base: str, tenant: str, site: str) -> None:
        self.base, self.tenant, self.site = base, tenant, site

    def url(self, vacancy: Vacancy) -> str:
        address = urlsplit(vacancy.url)
        prefix = f"/{self.site}"
        if (
            address.scheme != "https"
            or address.netloc != urlsplit(self.base).netloc
            or not address.path.startswith(f"{prefix}/job/")
        ):
            raise ValueError("Workday vacancy URL does not belong to this board")
        return f"{self.base}/wday/cxs/{self.tenant}/{self.site}{address.path.removeprefix(prefix)}"

    def parse(self, body: str, vacancy: Vacancy) -> Vacancy:
        info = object_value(json_object(body).get("jobPostingInfo"), "jobPostingInfo")
        rich = parse_rich_text(
            required_text(info.get("jobDescription"), "jobDescription"), base_url=vacancy.url
        )
        if not rich.text:
            raise ParseError("Workday jobDescription is empty")
        posting_slug = urlsplit(vacancy.url).path.rstrip("/").rsplit("/", 1)[-1]
        if text(info.get("jobPostingId")) not in {None, posting_slug}:
            raise ParseError("Workday detail belongs to a different posting")
        values = [
            text(info.get("location")),
            *[
                text(value)
                for value in array_value(info.get("additionalLocations") or [], "additionalLocations")
            ],
        ]
        labels = list(dict.fromkeys(value for value in values if value))
        requisition_location = object_value(
            info.get("jobRequisitionLocation") or {}, "jobRequisitionLocation"
        )
        country = object_value(requisition_location.get("country") or {}, "jobRequisitionLocation.country")
        raw_country = object_value(info.get("country") or {}, "country")
        country_value = (
            country.get("alpha2Code") or country.get("descriptor") or raw_country.get("descriptor")
        )
        formats = work_formats(info.get("remoteType"))
        result = vacancy.model_copy(deep=True)
        result.title = text(info.get("title")) or result.title
        result.description, result.description_html = rich.text, rich.html
        result.location = "; ".join(labels) or result.location
        # Workday's free-form location labels may be states, offices, or cities; preserve them verbatim.
        result.locations = locations(country=country_value, format=formats[0] if formats else None)
        result.work_formats = formats
        result.employment = employment(info.get("timeType")) or result.employment
        result.published_at = text(info.get("startDate")) or result.published_at
        result.source_data = {**result.source_data, "detail": info}
        return result


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    match = re.fullmatch(r"([^.]+)\.wd\d+\.myworkdayjobs\.com", host(board.url))
    if match is None:
        raise ValueError("Workday requires a tenant.wd<number>.myworkdayjobs.com board URL")
    parts = address.path.strip("/").split("/")
    if len(parts) == 5 and parts[:2] == ["wday", "cxs"] and parts[-1] == "jobs":
        tenant, site = parts[2:4]
    elif len(parts) == 1 and parts[0]:
        tenant, site = match[1], parts[0]
    elif len(parts) == 2 and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        tenant, site = match[1], parts[1]
    else:
        raise ValueError("Workday requires /<site>, /<locale>/<site>, or /wday/cxs/<tenant>/<site>/jobs")
    if not re.fullmatch(r"[\w-]+", tenant) or not re.fullmatch(r"[\w-]+", site):
        raise ValueError("Invalid Workday tenant or site")
    base = f"https://{address.netloc}"
    search = WorkdaySearch(board, base, tenant, site)
    return adapter(
        "workday",
        board,
        search,
        key=f"{host(board.url)}:{tenant}:{site}",
        resource=host(board.url),
        allowed_hosts=(host(board.url),),
        settings=settings,
        details=WorkdayDetails(base, tenant, site),
    )
