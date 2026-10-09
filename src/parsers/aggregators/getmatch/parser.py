"""Public Getmatch offer feed with pagination and optional native geography."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from src.models.brief import BriefInput
from src.models.company import Company
from src.models.vacancy import Vacancy
from src.parsers.aggregators.getmatch.geography import GetmatchGeography
from src.parsers.aggregators.getmatch.settings import GetmatchSettings
from src.parsers.blocking import classify_common
from src.parsers.interfaces import (
    Fetch,
    ParameterMode,
    ParseError,
    Search,
    SearchPage,
    SearchPlan,
    SearchRequest,
    SourceAdapter,
    VacancyDetails,
    filter_parameters,
)
from src.parsers.models import Location
from src.parsers.normalization import FORMATS, getmatch_locations, normalize_stored_vacancy
from src.parsers.rich_text import parse_rich_text

BASE = "https://getmatch.ru"


class GetmatchSearch(Search):
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
        }
    )

    def __init__(self, page_size: int) -> None:
        self.page_size = page_size

    @property
    def parameters(self) -> dict[str, ParameterMode]:
        return filter_parameters(
            self.fields | GetmatchVacancyDetails.fields, {"query": "ignored", "locations": "native"}
        )

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        filters = brief.filters
        minimum = None if second_chance else filters.minimum_work_format
        physical = minimum not in {"remote_country", "remote_worldwide"}
        regions: set[str] = set()
        if physical:
            if not filters.locations:
                regions.update(["moscow", "saints_p", "regions_ru", "relocate"])
            for place in filters.locations:
                city = (place.city or "").casefold()
                if place.country != "RU":
                    regions.add("relocate")
                elif not city:
                    regions.update(["moscow", "saints_p", "regions_ru"])
                elif city in {"москва", "moscow"}:
                    regions.add("moscow")
                elif city in {"санкт-петербург", "saint petersburg", "saint-petersburg"}:
                    regions.add("saints_p")
                else:
                    regions.add("regions_ru")
        regions.add("remote")
        choices = GetmatchGeography().options
        location = (
            None
            if len(regions) == len(choices)
            else Location(source="getmatch", options=[p for p in choices if p.id in regions])
        )
        application = filter_parameters(self.fields | GetmatchVacancyDetails.fields, {"query": "ignored"})
        if filters.locations and location:
            application["locations"] = "native"
        if minimum in {"remote_country", "remote_worldwide"}:
            application["minimum_work_format"] = "native"
        request = self.build("", location)
        if filters.published_after and not second_chance:
            # Cover the whole first calendar day; exact date stays local.
            elapsed = (
                datetime.now(UTC) - datetime.combine(filters.published_after, time(), UTC)
            ).total_seconds()
            days = elapsed / 86400
            period = next((f"{n}d" for n in (1, 3, 7, 30) if n >= days), "all")
            params = parse_qs(urlsplit(request.url).query)
            params["pa"] = [period]
            request = replace(request, url=BASE + "/api/offers?" + urlencode(params, doseq=True))
            if period != "all":
                application["published_after"] = "native"
        return SearchPlan(
            (request,),
            application,
            (
                "Категории Getmatch дают широкую выдачу; конкретные места и форматы проверяются локально.",
                "Помощь с релокацией проверяется по описанию и не включает автоматически l=relocate.",
            ),
        )

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None and (location.country is not None or not location.options):
            raise ValueError("Getmatch requires native region options")
        return self._offers(query, location)

    def _offers(self, query: str, location: Location | None, offset: int = 0) -> SearchRequest:
        params = {
            "sa": "any",
            "p": "1",
            "offset": str(offset),
            "limit": str(self.page_size),
            "pa": "all",
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
    taxes = item.get("salary_taxes")
    salary_text = item.get("salary_description") or ""
    period = next(
        (
            value
            for pattern, value in [
                (r"/[^а-яa-z]*мес|per month|/month", "month"),
                (r"/[^а-яa-z]*год|per year|/year", "year"),
                (r"/[^а-яa-z]*час|per hour|/hour", "hour"),
            ]
            if re.search(pattern, salary_text, re.I)
        ),
        None,
    )
    places = getmatch_locations(item)
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
        salary={
            "from": item.get("salary_display_from"),
            "to": item.get("salary_display_to"),
            "currencyCode": item.get("salary_currency"),
            "period": period,
            "gross": taxes == "gross" if taxes in {"net", "gross"} else None,
        },
        salary_hidden=bool(item.get("salary_hidden")),
        locations=places,
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
                "salary_description",
                "salary_taxes",
                "offer_type",
            )
            if k in item
        },
    )
    return normalize_stored_vacancy(vacancy, "getmatch")


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
    return SourceAdapter(
        id="getmatch",
        name="Getmatch",
        type="aggregator",
        resource="getmatch.ru",
        allowed_hosts=("getmatch.ru",),
        settings=settings,
        search=GetmatchSearch(settings.page_size),
        vacancy_details=GetmatchVacancyDetails(),
        geography=GetmatchGeography(),
        classify=classify_common,
    )
