"""SuccessFactors legacy XML feeds and public Recruiting Marketing search."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, cast
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from pydantic import JsonValue

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
    with_query,
)
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
from src.parsers.rich_text import parse_rich_text


class SuccessFactorsSearch(BoardSearch):
    query_notes = (
        "Старый XML-фид SuccessFactors не применяет поисковые фразы и native_filters. "
        "Для серверного поиска RMK задайте публичный URL /search/ компании."
    )
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
            "published_at",
            "description",
            "description_html",
            "source_data",
        }
    )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ParseError("SuccessFactors response is not valid XML") from exc
        if root.tag != "Job-Listing":
            raise ParseError("SuccessFactors response is missing Job-Listing root")
        vacancies = [self._vacancy(element) for element in root.findall("Job")]
        return SearchPage(vacancies, total=len(vacancies))

    def _vacancy(self, element: ET.Element) -> Vacancy:
        uid = required_text(element.findtext("ReqId"), "ReqId")
        metadata: dict[str, str] = {}
        for child in element:
            label, value = text(child.findtext("label")), text(child.findtext("value"))
            if label and value:
                metadata[label.rstrip(":").casefold()] = value
        address = urlsplit(self.board.url)
        query = {
            "career_ns": "job_listing",
            "company": parse_qs(address.query)["company"][0],
            "career_job_req_id": uid,
        }
        url = urlunsplit(address._replace(query=urlencode(query), fragment=""))
        rich = parse_rich_text(element.findtext("Job-Description") or "", base_url=url)
        country = metadata.get("country") or metadata.get("country/region")
        city = metadata.get("city")
        location = ", ".join(v for v in (city, country) if v) or None
        # The source sometimes supplies a labelled description location without a city metadata field.
        # Keep it verbatim; commas alone cannot prove country/city boundaries.
        for line in (rich.text or "").splitlines():
            match = re.match(r"^Location:\s*(.+)$", line.strip(), re.I)
            if match:
                location = match[1].strip()
                break
        formats = work_formats(metadata.get("workplace") or metadata.get("work arrangement"))
        date = text(element.findtext("Posted-Date"))
        if date:
            try:
                date = datetime.strptime(date, "%m/%d/%Y").date().isoformat()
            except ValueError:
                pass
        return Vacancy(
            id=uid,
            title=required_text(element.findtext("JobTitle"), "JobTitle"),
            url=url,
            company=Company(name=self.board.company, profile_url=self.board.url),
            location=location,
            locations=locations(country=country, city=city, format=formats[0] if formats else None),
            work_formats=formats,
            employment=employment(metadata.get("employment type")),
            published_at=date,
            description=rich.text,
            description_html=rich.html,
            source_data={"metadata": {key: value for key, value in metadata.items()}},
        )


class RecruitingMarketingSearch(BoardSearch):
    fields = frozenset({"id", "title", "url", "company", "location", "published_at", "source_data"})
    query_parameter = "keywords"
    # Field IDs come from the public RMK facetFields configuration, not employer labels.
    facet_names = frozenset(
        {
            "filter1",
            "filter2",
            "filter3",
            "jobLocationCountry",
            "jobLocationState",
            "jobLocationCity",
            "cust_WorkLocationType",
            "rmeducationLevel",
        }
    )
    filter_names = facet_names | {"locale", "location"}
    multi_filter_names = facet_names
    page_size = 10  # The public RMK jobs endpoint returns ten cards per zero-based page.

    def __init__(self, board: Board) -> None:
        address = urlsplit(board.url)
        self.base = urlunsplit(address._replace(path="", query="", fragment=""))
        self.bootstrap_url = urlunsplit(address._replace(path="/search/", fragment=""))
        super().__init__(board, f"{self.base}/services/recruiting/v1/jobs")
        for name in ("locale", "location"):
            if name in board.native_filters and not isinstance(board.native_filters[name], str):
                raise ValueError(f"SuccessFactors {name} requires one string")
        locale = board.native_filters.get("locale") or parse_qs(address.query).get("locale", [None])[0]
        self.locale = locale if isinstance(locale, str) else None
        if self.locale is not None and not re.fullmatch(r"[a-z]{2}_[A-Z]{2}", self.locale):
            raise ValueError("SuccessFactors locale must have the form en_GB")

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        locale = self.locale or self._locale(await fetch(self.bootstrap_url))
        requests = tuple(self._request(query, locale) for query in brief.queries or [""])
        return SearchPlan(requests, self.parameters)

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None:
            raise ValueError("SuccessFactors requires explicit RMK location or facet filter values")
        if self.locale is not None:
            return self._request(query, self.locale)
        return SearchRequest(with_query(self.bootstrap_url, {"q": query}), query, stage="preparation")

    @staticmethod
    def _locale(body: str) -> str:
        config = re.search(r"\bvar\s+appParams\s*=\s*\{(.*?)\};", body, re.S)
        if config is None or "facetFilters" not in config[1]:
            raise ParseError("SuccessFactors page is missing RMK appParams and locale")
        locale = re.search(r"\blocale\s*:\s*[\"\']([a-z]{2}_[A-Z]{2})[\"\']", config[1])
        if locale is None:
            raise ParseError("SuccessFactors page is missing RMK appParams and locale")
        return locale[1]

    def _request(self, query: str, locale: str) -> SearchRequest:
        return SearchRequest(
            self.listing_url,
            query,
            method="POST",
            json_body={
                "locale": locale,
                "pageNumber": 0,
                "sortBy": "",
                "keywords": query,
                "location": cast(str, self.board.native_filters.get("location", "")),
                "facetFilters": {
                    name: cast(JsonValue, value if isinstance(value, list) else [value])
                    for name, value in self.board.native_filters.items()
                    if name in self.facet_names
                },
                "brand": "",
                "skills": [],
                "categoryId": 0,
                "alertId": "",
                "rcmCandidateId": "",
            },
        )

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        if request.stage == "preparation":
            return SearchPage(next_requests=(self._request(request.query, self._locale(body)),))
        payload = json_object(body)
        total = payload.get("totalJobs")
        if type(total) is not int or total < 0:
            raise ParseError("SuccessFactors RMK response is missing a nonnegative totalJobs")
        # RMK deliberately omits jobSearchResult on a successful zero-result search.
        jobs = array_value(payload.get("jobSearchResult", [] if total == 0 else None), "jobSearchResult")
        params = request.json_body or {}
        number = params.get("pageNumber")
        if type(number) is not int or number < 0:
            raise ParseError("SuccessFactors RMK request is missing a nonnegative pageNumber")
        offset = number * self.page_size
        if (
            len(jobs) > self.page_size
            or (jobs and offset + len(jobs) > total)
            or (not jobs and offset < total)
        ):
            raise ParseError("SuccessFactors RMK result count is inconsistent with totalJobs")
        locale = required_text(params.get("locale"), "locale")
        vacancies = [self._vacancy(object_value(job, "jobSearchResult row"), locale) for job in jobs]
        if len({v.id for v in vacancies}) != len(vacancies):
            raise ParseError("SuccessFactors RMK returned duplicate job IDs in one page")
        next_requests: tuple[SearchRequest, ...] = ()
        if offset + len(jobs) < total:
            if len(jobs) != self.page_size:
                raise ParseError("SuccessFactors RMK returned an incomplete page before totalJobs")
            next_requests = (
                SearchRequest(
                    request.url,
                    request.query,
                    method="POST",
                    json_body={**params, "pageNumber": number + 1},
                ),
            )
        return SearchPage(vacancies, total=total, next_requests=next_requests)

    def _vacancy(self, row: dict[str, Any], locale: str) -> Vacancy:
        job = object_value(row.get("response"), "response")
        uid = required_text(job.get("id"), "id")
        if not uid.isdigit():
            raise ParseError("SuccessFactors RMK id must be a numeric requisition ID")
        slug = required_text(job.get("unifiedUrlTitle"), "unifiedUrlTitle")
        url = f"{self.base}/job/{quote(slug, safe='%-_.~')}/{uid}-{locale}"
        names = [
            required_text(value, "jobLocationShort")
            for value in array_value(job.get("jobLocationShort", []), "jobLocationShort")
        ]
        date = text(job.get("unifiedStandardStart"))
        date_format = {"en_GB": "%d/%m/%Y", "en_US": "%m/%d/%Y", "nl_NL": "%d-%m-%Y"}.get(locale)
        if date and date_format:
            try:
                date = datetime.strptime(date, date_format).date().isoformat()
            except ValueError:
                pass
        return Vacancy(
            id=uid,
            title=required_text(job.get("unifiedStandardTitle"), "unifiedStandardTitle"),
            url=url,
            company=Company(name=self.board.company, profile_url=self.board.url),
            location="; ".join(dict.fromkeys(names)) or None,
            published_at=date,
            source_data=job,
        )


def create_adapter(board: Board, settings: RequestSettings | None = None) -> SourceAdapter:
    address = urlsplit(board.url)
    if address.path.rstrip("/") == "/search" and not address.port:
        return adapter(
            "successfactors",
            board,
            RecruitingMarketingSearch(board),
            key=host(board.url),
            resource=host(board.url),
            allowed_hosts=(host(board.url),),
            settings=settings,
        )
    query = parse_qs(address.query, keep_blank_values=True)
    if (
        not host(board.url).endswith((".successfactors.com", ".successfactors.eu"))
        or address.path.rstrip("/") != "/career"
        or not query.get("company", [""])[0]
    ):
        raise ValueError("SuccessFactors requires a legacy /career?company= URL or a public RMK /search/ URL")
    query.update({"career_ns": ["job_listing_summary"], "resultType": ["XML"]})
    url = urlunsplit(address._replace(query=urlencode(query, doseq=True), fragment=""))
    return adapter(
        "successfactors",
        board,
        SuccessFactorsSearch(board, url),
        key=f"{host(board.url)}:{query['company'][0]}",
        resource=host(board.url),
        allowed_hosts=(host(board.url),),
        settings=settings,
    )
