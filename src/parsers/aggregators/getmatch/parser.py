"""Public Getmatch offer feed with pagination and optional native geography."""

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.company import Company
from src.models.filters import OFFICE_FORMATS, REMOTE_FORMATS, SearchPlace, WorkFormat, accepted_formats
from src.models.vacancy import Vacancy
from src.parsers.aggregators.getmatch.enrichment.formats import FORMATS
from src.parsers.aggregators.getmatch.geography import REMOTE, GetmatchGeography
from src.parsers.aggregators.getmatch.settings import GetmatchSettings
from src.parsers.blocking import classify_common
from src.parsers.interfaces import (
    Combined,
    Draft,
    Fetch,
    NativeParams,
    ParameterMode,
    ParseError,
    PreparationStatus,
    Prepared,
    SearchPage,
    SearchRequest,
    SourceAdapter,
    StagedSearch,
    VacancyDetails,
    filter_parameters,
)
from src.parsers.models import Location
from src.parsers.rich_text import parse_rich_text

BASE = "https://getmatch.ru"


class GetmatchSearch(StagedSearch):
    notes = (
        "Каждое место — отдельная лента Getmatch; удалёнка Getmatch не ограничена географией.",
        "Зоны «другие города России» и «другие страны» шире выбранного места; "
        "конкретные места и форматы проверяются локально.",
        "Помощь с релокацией проверяется по описанию и не включает автоматически l=relocate.",
    )
    query_notes = (
        "Обход общей ленты Getmatch без выбора специализации. "
        "Поисковые фразы не ограничивают ленту; общие фильтры проверяются по данным вакансий. "
        "Выбранная география и лимиты сбора действуют."
    )
    fields = frozenset(
        {
            "title",
            "company",
            "company_profile_url",
            "location",
            "locations",
            "salary",
            "published_at",
            "work_formats",
            "summary",
            "skills",
            # Structured relocation of each place (`relocation_company`), set by the Getmatch normalizer.
            "locations.relocation_support",
        }
    )

    def __init__(self, page_size: int, geography: GetmatchGeography | None = None) -> None:
        self.page_size = page_size
        self.geography = geography or GetmatchGeography()

    @property
    def parameters(self) -> dict[str, ParameterMode]:
        return filter_parameters(
            self.fields | GetmatchVacancyDetails.fields, {"query": "ignored", "locations": "native"}
        )

    def _application(self) -> dict[str, ParameterMode]:
        return filter_parameters(self.fields | GetmatchVacancyDetails.fields, {"query": "ignored"})

    def _prepare_query(self, query: str) -> Prepared:
        return Prepared("query", "ignored", reason="Getmatch обходится общей лентой без поиска по тексту")

    async def _prepare_geography(self, fetch: Fetch, place: SearchPlace) -> Prepared:
        return await self.geography.zone(fetch, place)

    def _prepare_work_format(self, minimum: WorkFormat | None) -> Prepared:
        accepted = accepted_formats(minimum)
        if not OFFICE_FORMATS & set(accepted):
            # Only the remote zone is sent, and it holds no office or hybrid jobs.
            status: PreparationStatus = "exact"
            reason: str | None = None
            if minimum == "remote_worldwide":
                status, reason = "broader", "Зона remote включает удалёнку с ограничением страны"
            return Prepared("minimum_work_format", "native", status, accepted=accepted, reason=reason)
        if minimum == "hybrid":
            reason = "Getmatch не отделяет офис от гибрида: одна зона"
            return Prepared("minimum_work_format", "local", "broader", accepted=accepted, reason=reason)
        return Prepared("minimum_work_format", "local", accepted=accepted)

    def _prepare_published(self, published_after: date) -> Prepared:
        # Cover the whole first calendar day; the exact date stays local.
        days = (datetime.now(UTC) - datetime.combine(published_after, time(), UTC)).total_seconds() / 86400
        period = next((f"{n}d" for n in (1, 3, 7, 30) if n >= days), "all")
        mode: ParameterMode = "native" if period != "all" else "local"
        return Prepared("published_after", mode, params={"pa": (period,)})

    def _combine(self, draft: Draft) -> Combined:
        """The only step that couples geography and format: Getmatch zones carry both."""
        work_format = draft.get("minimum_work_format")
        accepted = set(work_format.accepted) if work_format else set()
        geography = draft.get("locations")
        zones: list[str] = []
        notes: list[Prepared] = []
        if OFFICE_FORMATS & accepted and geography is not None:
            zones.extend(geography.params["l"])
        if REMOTE_FORMATS & accepted:
            zones.append(REMOTE)
            if geography is not None:
                reason = "Getmatch не ограничивает географию удалённой работы"
                notes.append(Prepared("locations", "local", "broader", {"l": (REMOTE,)}, reason=reason))
        if geography is None and OFFICE_FORMATS & accepted:
            zones = []  # Historical brief without places: the whole feed.
        published = draft.get("published_after")
        params: NativeParams = {"pa": published.params["pa"] if published else ("all",)}
        if zones:
            params["l"] = tuple(p.id for p in self.geography.options if p.id in zones)
        return Combined((params,), tuple(notes))

    def _request(self, query: str, params: NativeParams) -> SearchRequest:
        zones = params.get("l", ())
        options = [p for p in self.geography.options if p.id in zones]
        location = Location(source="getmatch", options=options) if options else None
        return self._offers(query, location, period=params["pa"][0])

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None and (location.country is not None or not location.options):
            raise ValueError("Getmatch requires native region options")
        return self._offers(query, location)

    def _offers(
        self, query: str, location: Location | None, offset: int = 0, period: str = "all"
    ) -> SearchRequest:
        params = {
            "sa": "any",
            "p": "1",
            "offset": str(offset),
            "limit": str(self.page_size),
            "pa": period,
        }
        pairs = list(params.items())
        if location:
            pairs.extend(("l", choice.id) for choice in location.options)
        return SearchRequest(BASE + "/api/offers?" + urlencode(pairs), query, location=location)

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        value = json.loads(body)
        if not isinstance(value, dict) or not isinstance(value.get("offers"), list):
            raise ParseError("Getmatch offers payload is missing")
        meta = value.get("meta")
        if not isinstance(meta, dict) or any(
            type(meta.get(k)) is not int or meta[k] < 0 for k in ("total", "offset", "limit")
        ):
            raise ParseError("Getmatch pagination metadata is invalid")
        params = parse_qs(urlsplit(request.url).query)
        offset = int(params["offset"][0])
        if meta["offset"] != offset or meta["limit"] != self.page_size:
            raise ParseError("Getmatch pagination does not match requested offset/limit")
        vacancies = [_vacancy(item) for item in value["offers"]]
        if not vacancies and offset < meta["total"]:
            raise ParseError("Getmatch reports results but returned an empty page")
        # The feed can prepend promoted offers that do not consume pagination slots.
        next_offset = offset + meta["limit"]
        following: tuple[SearchRequest, ...] = ()
        if vacancies and next_offset < meta["total"]:
            params["offset"] = [str(next_offset)]
            following = (replace(request, url=BASE + "/api/offers?" + urlencode(params, doseq=True)),)
        return SearchPage(vacancies, meta["total"], following)


def _vacancy(item: dict) -> Vacancy:
    if (
        not isinstance(item, dict)
        or item.get("id") is None
        or not item.get("position")
        or not item.get("url")
    ):
        raise ParseError("Getmatch offer is missing identity, title or URL")
    company = item.get("company") or {}
    profile = urljoin(BASE, company["url"]) if company.get("url") else None
    locations = item.get("location_items") or []
    rich = parse_rich_text(item.get("offer_description") or "", base_url=urljoin(BASE, item["url"]))
    vacancy = Vacancy(
        id=str(item["id"]),
        title=item["position"],
        url=urljoin(BASE, item["url"]),
        company=Company(
            id=str(company["id"]) if company.get("id") is not None else profile,
            name=company.get("name"),
            profile_url=profile,
        )
        if company
        else None,
        location=", ".join(v["label"] for v in locations if v.get("label")) or None,
        published_at=item.get("published_at"),
        work_formats=list(
            dict.fromkeys(
                FORMATS[v["format"]] for v in locations if v.get("format") in FORMATS and not v.get("exclude")
            )
        ),
        summary=rich.text,
        summary_html=rich.html,
        skills=[s["name"] for s in item.get("skills_objects") or [] if s.get("name")],
        source_data={
            k: item[k]
            for k in (
                "location_requirements",
                "location_items",
                # Salary is a fact of the Getmatch normalizer (getmatch/enrichment/salary.py).
                "salary_display_from",
                "salary_display_to",
                "salary_currency",
                "salary_taxes",
                "salary_hidden",
                "salary_is_total",
                "salary_by_our_version",
                "salary_description",
                "offer_type",
            )
            if k in item
        },
    )
    return vacancy


class GetmatchVacancyDetails(VacancyDetails):
    fields = frozenset({"description"})

    def url(self, vacancy: Vacancy) -> str:
        return vacancy.url

    def parse(self, body: str, vacancy: Vacancy) -> Vacancy:
        soup = BeautifulSoup(body, "html.parser")
        canonical = soup.select_one('link[rel="canonical"]')
        href = canonical.get("href") if canonical else None
        identity = (
            re.match(r"/vacancies/(\d+)(?:-|$)", urlsplit(href).path) if isinstance(href, str) else None
        )
        if identity is None or identity.group(1) != vacancy.id:
            raise ParseError("Getmatch detail identity does not match the requested vacancy")
        selector = (
            "section.one-day-offer-v3--container"
            if vacancy.source_data.get("offer_type") == "one_day_offer_v3"
            else "section.b-vacancy-description"
        )
        description = soup.select_one(selector)
        if description is None or not description.get_text(strip=True):
            raise ParseError("Getmatch vacancy description is missing")
        rich = parse_rich_text(description.decode_contents(), base_url=vacancy.url)
        if rich.text is None:
            raise ParseError("Getmatch vacancy description is missing")
        return vacancy.model_copy(update={"description": rich.text, "description_html": rich.html})


def create_adapter(settings: GetmatchSettings) -> SourceAdapter:
    geography = GetmatchGeography(settings.geography_cache_seconds)
    return SourceAdapter(
        id="getmatch",
        name="Getmatch",
        type="aggregator",
        resource="getmatch.ru",
        allowed_hosts=("getmatch.ru",),
        settings=settings,
        search=GetmatchSearch(settings.page_size, geography),
        vacancy_details=GetmatchVacancyDetails(),
        geography=geography,
        classify=classify_common,
    )
