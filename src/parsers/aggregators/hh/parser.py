"""Pure HH parser interface. No network, storage, scheduling or filtering."""

import html
import json
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup
from pydantic import JsonValue

from src.geography.catalog import GeographyCatalog, default_catalog
from src.models.brief import BriefInput
from src.models.company import Company
from src.models.filters import REMOTE_FORMATS, Employment, WorkFormat, accepted_formats
from src.models.vacancy import Vacancy
from src.parsers.aggregators.hh.geography import HHGeography, resolve_city
from src.parsers.aggregators.hh.settings import HHSettings
from src.parsers.blocking import classify_common
from src.parsers.interfaces import (
    CompanyProfile,
    Fetch,
    ParameterMode,
    ParseError,
    ResponseClass,
    ResponseInfo,
    Search,
    SearchPage,
    SearchPlan,
    SearchRequest,
    SourceAdapter,
    VacancyDetails,
    filter_parameters,
)
from src.parsers.models import Location
from src.parsers.rich_text import parse_rich_text

# A minimum that excludes office-only jobs: HH can then pass it as work_format.
NOT_OFFICE_ONLY = frozenset(accepted_formats("hybrid"))


def _is_captcha(body: str) -> bool:
    return 'luxPageName: "AccountCaptcha"' in body or 'data-qa="account-captcha-picture"' in body


def classify(response: ResponseInfo) -> ResponseClass | None:
    """HH answers a captcha or a VPN check with 200 or a redirect; neither is a page to parse or retry."""
    return "captcha" if _is_captcha(response.text) else classify_common(response)


def _state(body: str) -> dict:
    # Extraction adapted from job-harness v2 hh_ru._extract_initial_state.
    if _is_captcha(body):  # Also guarded here: a parser must never treat the captcha page as data.
        raise ParseError("HH returned a captcha page", "blocked")
    marker = body.find('id="HH-Lux-InitialState"')
    start = body.find(">", marker)
    end = body.find("</template>", start)
    if marker < 0 or start < 0 or end < 0:
        raise ParseError("HH-Lux-InitialState is missing")
    try:
        value = json.loads(html.unescape(body[start + 1 : end]))
    except ValueError as exc:
        raise ParseError("HH-Lux-InitialState is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ParseError("HH-Lux-InitialState is not an object")
    return value


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _website(value: object) -> str | None:
    if isinstance(value, str) and urlsplit(value).scheme in {"http", "https"} and urlsplit(value).hostname:
        return value
    return None


def _normalize(vacancy: Vacancy, data: dict) -> None:
    # Places and work formats are facts of the HH normalizer (hh/enrichment/), read from `area`
    # and `workFormats` in source_data; the parser keeps HH's own values there.
    raw = vacancy.salary
    salary = {**raw}
    if salary.get("currencyCode") == "RUR":
        salary["currencyCode"] = "RUB"
    mode = raw.get("mode")
    salary["period"] = (
        {"MONTH": "month", "YEAR": "year", "HOUR": "hour"}.get(mode) if isinstance(mode, str) else None
    )
    # Retain an already normalized period when details contain no replacement salary.
    if mode is None and raw.get("period") in {"month", "year", "hour"}:
        salary["period"] = raw["period"]
    vacancy.salary = salary
    vacancy.salary_hidden = "noCompensation" in raw
    minimums = {"noExperience": 0, "between1And3": 1, "between3And6": 3, "moreThan6": 6}
    vacancy.min_experience = minimums.get(vacancy.experience or "")
    vacancy.min_experience_exclusive = vacancy.experience == "moreThan6"
    employments: dict[str, Employment] = {
        "FULL": "full_time",
        "PART": "part_time",
        "PROJECT": "contract",
        "INTERNSHIP": "internship",
        "VOLUNTEER": "volunteer",
    }
    employment = data.get("employmentForm")
    if isinstance(employment, str) and employment in employments:
        vacancy.employment = [employments[employment]]


class HHSearch(Search):
    query_notes = "Текстовый поиск; страна и город передаются в HH."
    fields = frozenset(
        {
            "title",
            "company",
            "location",
            "locations",
            "employment",
            "min_experience",
            "salary",
            "published_at",
            "experience",
            "work_formats",
            "company_profile_url",
            "company_website",
        }
    )

    def __init__(self, geography: HHGeography | None = None, catalog: GeographyCatalog | None = None) -> None:
        self.geography = geography or HHGeography(300)
        self.catalog = catalog or default_catalog()

    @property
    def parameters(self) -> dict[str, ParameterMode]:
        return filter_parameters(
            self.fields | HHVacancyDetails.fields, {"query": "native", "locations": "native"}
        )

    async def prepare(self, brief: BriefInput, fetch: Fetch, *, second_chance: bool = False) -> SearchPlan:
        filters = brief.filters
        application = filter_parameters(self.fields | HHVacancyDetails.fields, {"query": "native"})
        soft: dict[str, list[str]] = {}
        if not second_chance:
            employment = {"full_time": "FULL", "part_time": "PART"}
            # Do not narrow an OR condition when any selected kind has no safe mapping.
            if filters.employment and all(e in employment for e in filters.employment):
                soft["employment_form"] = [employment[e] for e in filters.employment]
                application["employment"] = "native"
            if filters.experience_years is not None:
                soft["experience"] = [
                    key
                    for key, years in (("noExperience", 0), ("between1And3", 1), ("between3And6", 3))
                    if filters.experience_years >= years
                ]
                if filters.experience_years > 6:
                    soft["experience"].append("moreThan6")
                application["experience_years"] = "native"
            if filters.published_after:
                # Earlier midnight covers timezones and HH five-minute rounding.
                soft["date_from"] = [(filters.published_after - timedelta(days=1)).isoformat()]
                application["published_after"] = "native"
        branches: list[tuple[Location | None, bool, WorkFormat | None]] = []
        if filters.locations:
            application["locations"] = "native"
            countries = await self.geography.countries(fetch)
            for place in filters.locations:
                minimum = None if second_chance else filters.location_conditions(place)[0]
                if minimum in NOT_OFFICE_ONLY:
                    application["minimum_work_format"] = "native"
                # HH's own country list, each name resolved to ISO through CLDR.
                country = next(
                    (c for c in countries if self.catalog.country(c.name).value == place.country), None
                )
                if country is None:
                    raise ValueError(f"HH cannot resolve country {place.country}")
                city = None
                if place.city and minimum not in REMOTE_FORMATS:
                    city_text = self.catalog.label(place.geoname_id, "ru") if place.geoname_id else place.city
                    choices = await self.geography.cities(fetch, country.id, city_text)
                    city = resolve_city(choices, place, self.catalog)
                if minimum not in REMOTE_FORMATS:
                    branches.append((Location(source="hh_ru", country=country, city=city), False, minimum))
                if city or minimum in REMOTE_FORMATS:
                    branches.append((Location(source="hh_ru", country=country), True, minimum))
            # Worldwide jobs may be published abroad; HH has no worldwide flag.
            branches.append((None, True, None))
        else:
            minimum = None if second_chance else filters.minimum_work_format
            if minimum in NOT_OFFICE_ONLY:
                application["minimum_work_format"] = "native"
            branches.append((None, False, minimum))
        requests: dict[str, SearchRequest] = {}
        for query in brief.queries:
            for location, remote, minimum in branches:
                request = self.build(query, location)
                params = {**parse_qs(urlsplit(request.url).query), **soft}
                if remote or minimum in REMOTE_FORMATS:
                    params["work_format"] = ["REMOTE"]
                elif minimum == "hybrid":
                    params["work_format"] = ["HYBRID", "REMOTE"]
                request = replace(
                    request, url="https://hh.ru/search/vacancy?" + urlencode(params, doseq=True)
                )
                requests[request.url] = request
        return SearchPlan(
            tuple(requests.values()),
            application,
            (
                "География объединяет местные вакансии и удалёнку; мировая удалёнка уточняется по тексту.",
                "Зарплата проверяется локально: безопасность нативного порога "
                "для скрытой зарплаты не подтверждена.",
            ),
        )

    def build(self, query: str, location: Location | None = None) -> SearchRequest:
        if location is not None and (location.country is None or location.options):
            raise ValueError("HH requires country/city geography")
        place = (location.city or location.country) if location else None
        location_id = place.id if place else None
        params = {"text": query, "page": "0"}
        if location_id is not None:
            if not location_id.isdigit():
                raise ValueError("HH location ID must contain digits")
            params["area"] = location_id
        return SearchRequest("https://hh.ru/search/vacancy?" + urlencode(params), query, location=location)

    def parse(self, body: str, request: SearchRequest) -> SearchPage:
        result = _state(body).get("vacancySearchResult")
        if not isinstance(result, dict) or not isinstance(result.get("vacancies"), list):
            raise ParseError("HH vacancySearchResult.vacancies is missing")
        if not isinstance(result.get("totalResults"), int):
            raise ParseError("HH totalResults is missing")
        vacancies = []
        for item in result["vacancies"]:
            if not isinstance(item, dict) or not item.get("vacancyId") or not item.get("name"):
                raise ParseError("HH listing is missing its identity/title")
            employer = _object(item.get("company"))
            company_id = employer.get("id")
            company = (
                Company(
                    id=str(company_id),
                    name=employer.get("visibleName") or employer.get("name"),
                    profile_url=f"https://hh.ru/employer/{company_id}",
                    site_url=_website(employer.get("companySiteUrl")),
                )
                if company_id
                else None
            )
            formats = item.get("workFormats", [])
            work_formats = [
                v for group in formats for v in _strings(_object(group).get("workFormatsElement"))
            ]
            vacancies.append(
                Vacancy(
                    id=str(item["vacancyId"]),
                    title=item["name"],
                    url=f"https://hh.ru/vacancy/{item['vacancyId']}",
                    company=company,
                    location=_object(item.get("area")).get("name"),
                    salary=_object(item.get("compensation")),
                    published_at=_object(item.get("publicationTime")).get("$"),
                    experience=item.get("workExperience"),
                    work_formats=work_formats,
                    source_data={
                        **{
                            k: item[k]
                            for k in ("area", "address", "compensation", "workExperience", "employmentForm")
                            if k in item
                        },
                        # The card's shape: a list of HH work format codes.
                        "workFormats": list[JsonValue](work_formats),
                    },
                )
            )
        if not vacancies and result["totalResults"] != 0:
            raise ParseError("HH reports results but returned an empty listing page")
        next_info = _object(_object(result.get("paging")).get("next"))
        next_page = next_info.get("page") if next_info.get("disabled") is not True else None
        if next_page is not None and (not isinstance(next_page, int) or next_page < 0):
            raise ParseError("HH next page number is invalid")
        following: tuple[SearchRequest, ...] = ()
        if next_page is not None:
            params = parse_qs(urlsplit(request.url).query)
            if next_page <= int(params.get("page", ["0"])[0]):
                raise ParseError("Pagination did not advance")
            params["page"] = [str(next_page)]
            url = "https://hh.ru/search/vacancy?" + urlencode(params, doseq=True)
            following = (SearchRequest(url, request.query, location=request.location),)
        for vacancy in vacancies:
            _normalize(vacancy, vacancy.source_data)
        return SearchPage(vacancies, result["totalResults"], following)


class HHVacancyDetails(VacancyDetails):
    # The HH normalizer reads relocation of the places from the card description.
    fields = HHSearch.fields | {"description", "skills", "locations.relocation_support"}

    def url(self, vacancy: Vacancy) -> str:
        return vacancy.url

    def parse(self, body: str, vacancy: Vacancy) -> Vacancy:
        # HH sometimes serves a standalone description without the SSR template.
        if 'id="HH-Lux-InitialState"' in body or "AccountCaptcha" in body:
            view = _object(_state(body).get("vacancyView"))
        else:
            view = {}
        full = _object(view.get("vacancyFull")).get("vacancy")
        if isinstance(full, dict):
            view = full
        if view.get("vacancyId") is not None and str(view["vacancyId"]) != vacancy.id:
            raise ParseError("HH detail identity does not match the requested vacancy")
        soup = BeautifulSoup(body, "html.parser")
        description = soup.select_one('[data-qa="vacancy-description"]')
        if description is None and isinstance(view.get("description"), str):
            description = BeautifulSoup(view["description"], "html.parser")
        if description is None or not description.get_text(strip=True):
            raise ParseError("HH vacancy description is missing")
        detail = vacancy.model_copy(deep=True)
        detail.title = view.get("name") or detail.title
        rich = parse_rich_text(description.decode_contents(), base_url=vacancy.url)
        if rich.text is None:
            raise ParseError("HH vacancy description is missing")
        detail.description = rich.text
        detail.description_html = rich.html
        skill_data = view.get("keySkills")
        detail.skills = _strings(
            skill_data if isinstance(skill_data, list) else _object(skill_data).get("keySkill")
        )
        detail.experience = view.get("workExperience") or detail.experience
        detail.work_formats = _strings(view.get("workFormats")) or detail.work_formats
        detail.salary = _object(view.get("compensation")) or detail.salary
        detail.location = _object(view.get("area")).get("name") or detail.location
        detail.published_at = view.get("publicationTimeIso") or detail.published_at
        employer = _object(view.get("company"))
        if employer.get("id"):
            detail.company = Company(
                id=str(employer["id"]),
                name=employer.get("visibleName") or employer.get("name"),
                profile_url=f"https://hh.ru/employer/{employer['id']}",
                site_url=_website(employer.get("companySiteUrl")),
            )
        detail.source_data.update(
            {
                k: view[k]
                for k in (
                    "area",
                    "compensation",
                    "workExperience",
                    "workFormats",
                    "keySkills",
                    "employmentForm",
                    "workScheduleByDays",
                    "workingHours",
                    "acceptLaborContract",
                    "civilLawContracts",
                )
                if k in view
            }
        )
        _normalize(detail, detail.source_data)
        return detail


class HHCompanyProfile(CompanyProfile):
    fields = frozenset({"company", "company_profile_url", "company_website", "company_location"})

    def url(self, company: Company) -> str | None:
        return company.profile_url

    def parse(self, body: str, company: Company) -> Company:
        company_id = company.id
        info = _state(body).get("employerInfo")
        if not isinstance(info, dict):
            raise ParseError("HH employerInfo is missing")
        site = _website(info.get("companySiteUrl") or info.get("siteUrl"))
        if not site:
            soup = BeautifulSoup(body, "html.parser")
            for link in soup.find_all("a", href=True):
                if "официальный сайт" in link.get_text(" ", strip=True).casefold():
                    site = _website(link.get("href"))
                    if site:
                        break
        return Company(
            id=company_id,
            name=info.get("name"),
            profile_url=f"https://hh.ru/employer/{company_id}",
            site_url=site,
            location=info.get("address") or info.get("employerCountryCode"),
        )


def create_adapter(settings: HHSettings, catalog: GeographyCatalog | None = None) -> SourceAdapter:
    geography = HHGeography(settings.geography_cache_seconds)
    return SourceAdapter(
        id="hh_ru",
        name="HH",
        type="aggregator",
        resource="hh.ru",
        allowed_hosts=("hh.ru",),
        settings=settings,
        search=HHSearch(geography, catalog),
        vacancy_details=HHVacancyDetails(),
        company_profile=HHCompanyProfile(),
        geography=geography,
        classify=classify,
    )
