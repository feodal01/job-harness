"""Additional CIS-focused and RU-speaking IT job source scrapers."""

from __future__ import annotations

import json
import re
from typing import ClassVar
from urllib.parse import urlencode

from job_harness.base import BaseScraper
from job_harness.countries import CIS_COUNTRY_CODES
from job_harness.models import JobListing, SearchParams
from job_harness.registry import register_scraper
from job_harness.scrapers.http_common import (
    Anchor,
    absolute_url,
    extract_anchors,
    extract_next_data,
    fetch_json,
    fetch_text,
    normalize_text,
)
from job_harness.types import FilterSupport, ScraperCapabilities

_SALARY_RE = re.compile(r"(?:от\s*)?(?:~\s*)?\d[\d\s.,]*(?:K|к)?(?:\s*(?:₽|\$|€|руб))?", re.I)
_DATE_RE = re.compile(r"\b\d{1,2}\s+[а-яё]+\b", re.I)
_CIS_COUNTRY_BY_TEXT = {
    "арм": "AM",
    "armen": "AM",
    "азер": "AZ",
    "azer": "AZ",
    "беларус": "BY",
    "belarus": "BY",
    "казахстан": "KZ",
    "kazakh": "KZ",
    "киргиз": "KG",
    "кыргыз": "KG",
    "kyrgyz": "KG",
    "молдов": "MD",
    "moldov": "MD",
    "росси": "RU",
    "russia": "RU",
    "таджик": "TJ",
    "tajik": "TJ",
    "узбек": "UZ",
    "uzbek": "UZ",
    "туркмен": "TM",
    "turkmen": "TM",
    "грузи": "GE",
    "georgia": "GE",
    "украин": "UA",
    "ukrain": "UA",
}


def _country_from_text(text: str) -> str | None:
    lower = text.casefold()
    for marker, code in _CIS_COUNTRY_BY_TEXT.items():
        if marker in lower:
            return code
    return None


def _is_remote(text: str) -> bool:
    lower = text.casefold()
    return "remote" in lower or "удал" in lower


def _salary_from_text(text: str) -> str | None:
    match = _SALARY_RE.search(text)
    if match is None:
        return None
    return normalize_text(match.group(0)).removeprefix("~").strip()


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9+#.]+", query.casefold())
        if len(token) > 1
    }


def _listing_matches_query(listing: JobListing, query: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True

    searchable = " ".join(
        str(value or "")
        for value in (
            listing.title,
            listing.company,
            listing.url,
            listing.location,
            listing.description,
            listing.requirements,
            " ".join(listing.skills),
            " ".join(str(value) for value in listing.raw.values()),
        )
    ).casefold()
    return any(token in searchable for token in tokens)


class _HtmlAnchorScraper(BaseScraper):
    requires_browser = False
    detail_requires_browser = False
    base_url: str = ""
    search_path: str = ""
    query_param: str = "q"
    link_pattern: re.Pattern[str]
    default_country: str | None = None
    verify_ssl = True

    def search(self, params: SearchParams) -> list[JobListing]:
        html = fetch_text(
            self._build_search_url(params),
            verify_ssl=self.verify_ssl,
            timeout_seconds=self.fetch_timeout_seconds,
        )
        listings = self._parse_search_results(html, params)
        return [listing for listing in listings if _listing_matches_query(listing, params.query)][: self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    def _build_search_url(self, params: SearchParams) -> str:
        query = urlencode({self.query_param: params.query})
        return f"{self.base_url}{self.search_path}?{query}"

    def _parse_search_results(self, html: str, params: SearchParams) -> list[JobListing]:
        listings = []
        seen: set[str] = set()
        for anchor in extract_anchors(html):
            if not self.link_pattern.match(anchor.href):
                continue
            url = absolute_url(self.base_url, anchor.href)
            if url in seen:
                continue
            seen.add(url)
            listing = self._listing_from_anchor(anchor, url, params)
            if listing:
                listings.append(listing)
        return listings

    def _listing_from_anchor(self, anchor: Anchor, url: str, params: SearchParams) -> JobListing | None:
        title = anchor.text or anchor.attrs.get("aria-label", "")
        title = normalize_text(title)
        if not title:
            return None
        return JobListing(
            title=title,
            url=url,
            company="",
            country=params.country or self.default_country,
            source=self.name,
        )


@register_scraper("hirehi")
class HireHiScraper(_HtmlAnchorScraper):
    display_name = "HireHi"
    base_url = "https://hirehi.ru"
    search_path = "/jobs_new"
    query_param = "query"
    countries = ("RU",)
    default_country = "RU"
    link_pattern = re.compile(
        r"^/(?:qa|marketing|devops|analytics|development|design|management|backend|frontend|fullstack|python|java|go|mobile|ml-ai)/[^/]+-\d+$"
    )
    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.BEST_EFFORT,   # parsed from anchor text via _is_remote
        "country": FilterSupport.CLIENT,            # RU-only by design
        "experience": FilterSupport.BEST_EFFORT,
        "location": FilterSupport.BEST_EFFORT,
        "has_salary": FilterSupport.BEST_EFFORT,
        "query_match": FilterSupport.SERVER,        # query= URL param
    }

    def _listing_from_anchor(self, anchor: Anchor, url: str, params: SearchParams) -> JobListing | None:
        text = normalize_text(anchor.text)
        if not text:
            return None

        title = text
        company = ""
        if " в " in text:
            title, rest = text.split(" в ", 1)
            company = rest.split(",", 1)[0].strip()

        return JobListing(
            title=title.strip(),
            url=url,
            company=company,
            country="RU",
            salary=_salary_from_text(text),
            remote=_is_remote(text),
            source=self.name,
        )


@register_scraper("hirify")
class HirifyScraper(BaseScraper):
    display_name = "Hirify"
    API_URL = "https://api.hirify.me/api/vacancies"
    countries = CIS_COUNTRY_CODES
    requires_browser = False
    detail_requires_browser = False

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.CLIENT,        # work_format field
        "country": FilterSupport.CLIENT,            # country/location field
        "experience": FilterSupport.BEST_EFFORT,
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.CLIENT,         # salary_from/_to
        "query_match": FilterSupport.SERVER,        # search= API param
    }

    def search(self, params: SearchParams) -> list[JobListing]:
        data = fetch_json(self._build_search_url(params), timeout_seconds=self.fetch_timeout_seconds)
        listings = [self._listing_from_item(item, params) for item in data.get("data", [])]
        return [listing for listing in listings if listing is not None][: self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    def _build_search_url(self, params: SearchParams) -> str:
        return f"{self.API_URL}?{urlencode({'search': params.query})}"

    def _listing_from_item(self, item: dict, params: SearchParams) -> JobListing | None:
        title = normalize_text(str(item.get("title") or ""))
        slug = normalize_text(str(item.get("slug") or ""))
        if not title or not slug:
            return None

        salary = self._format_salary(item)
        company = self._company_from_item(item)

        country = _country_from_text(str(item.get("country") or item.get("location") or ""))
        work_format = " ".join(item.get("work_format") or [])
        raw = {"id": item.get("id")}
        if not company:
            raw["company_missing"] = True

        return JobListing(
            title=title,
            url=f"https://hirify.me/jobs/{slug}",
            company=company,
            country=country,
            salary=salary,
            remote=_is_remote(work_format),
            location=item.get("location"),
            posted_date=item.get("published_at") or item.get("updated_at"),
            source=self.name,
            raw=raw,
        )

    def _company_from_item(self, item: dict) -> str:
        candidates: list[object] = [
            item.get("company_title"),
            item.get("companyName"),
            item.get("company_name"),
            item.get("employer_title"),
            item.get("employer_name"),
        ]
        for key in ("company", "employer", "organization", "recruiter"):
            nested = item.get(key)
            if isinstance(nested, dict):
                candidates.extend([
                    nested.get("title"),
                    nested.get("name"),
                    nested.get("company_title"),
                    nested.get("display_name"),
                ])
        for candidate in candidates:
            company = normalize_text(str(candidate or ""))
            if company and company != "%hirify_global%":
                return company
        return ""

    def _format_salary(self, item: dict) -> str | None:
        salary_from = item.get("salary_from")
        salary_to = item.get("salary_to")
        currency = item.get("currency")
        if salary_from and salary_to:
            return f"{salary_from} - {salary_to} {currency or ''}".strip()
        if salary_from:
            return f"from {salary_from} {currency or ''}".strip()
        if salary_to:
            return f"to {salary_to} {currency or ''}".strip()
        return None


@register_scraper("staff_am")
class StaffAmScraper(BaseScraper):
    display_name = "Staff.am"
    BASE_URL = "https://staff.am"
    countries = ("AM",)
    requires_browser = False
    detail_requires_browser = False

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.CLIENT,        # is_remote in __NEXT_DATA__
        "country": FilterSupport.CLIENT,            # AM-only by design
        "experience": FilterSupport.BEST_EFFORT,
        "location": FilterSupport.CLIENT,           # job_city
        "has_salary": FilterSupport.UNSUPPORTED,
        "query_match": FilterSupport.BEST_EFFORT,   # URL-category map + post-filter
    }

    def search(self, params: SearchParams) -> list[JobListing]:
        html = fetch_text(self._build_search_url(params), timeout_seconds=self.fetch_timeout_seconds)
        data = extract_next_data(html)
        jobs = data["props"]["pageProps"].get("jobs", [])
        listings = []
        for job in jobs:
            listing = self._listing_from_job(job)
            if listing:
                listings.append(listing)
        filtered = [
            listing for listing in listings
            if _listing_matches_query(listing, params.query)
        ]
        return filtered[: self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    def _build_search_url(self, params: SearchParams) -> str:
        query = params.query.casefold()
        if any(keyword in query for keyword in ("qa", "test", "тест", "quality")):
            return f"{self.BASE_URL}/en/jobs/quality-assurance"
        if any(
            keyword in query
            for keyword in (
                "developer",
                "software",
                "backend",
                "frontend",
                "fullstack",
                "python",
                "java",
                "golang",
                "go ",
                "разработ",
            )
        ):
            return f"{self.BASE_URL}/en/jobs/software-development"
        return f"{self.BASE_URL}/en/jobs"

    def _localized(self, value: dict | str | None) -> str:
        if isinstance(value, dict):
            return str(value.get("en") or value.get("ru") or value.get("am") or "")
        return str(value or "")

    def _listing_from_job(self, job: dict) -> JobListing | None:
        title = normalize_text(self._localized(job.get("title")))
        company_data = job.get("companiesStruct") or {}
        company = normalize_text(self._localized(company_data.get("title")))
        category = job.get("category") or {}
        slug = job.get("slug") or {}
        category_code = category.get("code")
        job_slug = slug.get("en") or slug.get("ru") or slug.get("am")
        if not category_code or not job_slug:
            return None

        city = self._localized((job.get("job_city") or {}).get("title"))
        activated_at = job.get("activated_at") or {}

        return JobListing(
            title=title,
            url=f"{self.BASE_URL}/en/jobs/{category_code}/{job_slug}",
            company=company,
            country="AM",
            remote=bool(job.get("is_remote")),
            location=city or None,
            posted_date=activated_at.get("staffam"),
            source=self.name,
            raw={"id": job.get("id")},
        )


@register_scraper("geekjob")
class GeekJobScraper(_HtmlAnchorScraper):
    display_name = "GeekJob"
    base_url = "https://geekjob.ru"
    search_path = "/vacancies"
    query_param = "qs"
    countries = CIS_COUNTRY_CODES
    link_pattern = re.compile(r"^/vacancy/[a-f0-9]+$")
    verify_ssl = False
    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.BEST_EFFORT,
        "country": FilterSupport.BEST_EFFORT,
        "experience": FilterSupport.BEST_EFFORT,
        "location": FilterSupport.BEST_EFFORT,
        "has_salary": FilterSupport.BEST_EFFORT,
        "query_match": FilterSupport.CLIENT,        # _listing_matches_query post-filter
    }

    def search(self, params: SearchParams) -> list[JobListing]:
        html = fetch_text(
            f"{self.base_url}{self.search_path}",
            verify_ssl=self.verify_ssl,
            timeout_seconds=self.fetch_timeout_seconds,
        )
        listings = self._parse_search_results(html, params)
        filtered = [listing for listing in listings if _listing_matches_query(listing, params.query)]
        return filtered[: self.max_results]

    def _parse_search_results(self, html: str, params: SearchParams) -> list[JobListing]:
        grouped: dict[str, list[str]] = {}
        for anchor in extract_anchors(html):
            if self.link_pattern.match(anchor.href):
                grouped.setdefault(anchor.href, []).append(anchor.text)

        listings = []
        for href, texts in grouped.items():
            listing = self._listing_from_texts(href, texts)
            if listing:
                listings.append(listing)
        return listings

    def _listing_from_texts(self, href: str, texts: list[str]) -> JobListing | None:
        cleaned = [normalize_text(text) for text in texts if normalize_text(text)]
        meaningful = [
            text for text in cleaned
            if not _DATE_RE.fullmatch(text)
            and text != "chevron_right"
            and not (len(text) <= 3 and text.isupper())
        ]
        if not meaningful:
            return None

        title = ""
        company = ""
        location = None
        salary = None
        for text in meaningful:
            if salary is None:
                salary = _salary_from_text(text)
            if location is None and _country_from_text(text):
                location = text
                continue
            if not title and salary != text:
                title = text
                continue
            if title and not company and text != salary:
                company = text
                break

        if not title:
            return None

        return JobListing(
            title=title,
            url=absolute_url(self.base_url, href),
            company=company,
            country=_country_from_text(" ".join(meaningful)),
            salary=salary,
            remote=_is_remote(" ".join(meaningful)),
            location=location,
            source=self.name,
        )


@register_scraper("talento")
class TalentoScraper(_HtmlAnchorScraper):
    display_name = "Talento"
    base_url = "https://talento.works"
    search_path = "/"
    query_param = "q"
    countries = CIS_COUNTRY_CODES
    link_pattern = re.compile(r"^/jobs/[a-f0-9-]+$")
    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.BEST_EFFORT,
        "country": FilterSupport.BEST_EFFORT,
        "experience": FilterSupport.BEST_EFFORT,
        "location": FilterSupport.BEST_EFFORT,
        "has_salary": FilterSupport.BEST_EFFORT,
        "query_match": FilterSupport.BEST_EFFORT,
    }

    def _listing_from_anchor(self, anchor: Anchor, url: str, params: SearchParams) -> JobListing | None:
        label = normalize_text(anchor.attrs.get("aria-label", "") or anchor.text)
        if not label:
            return None
        company = ""
        title = label
        if ": " in label:
            company, title = label.split(": ", 1)
        return JobListing(
            title=title,
            url=url,
            company=company,
            country=params.country,
            source=self.name,
        )


@register_scraper("finder_work")
class FinderWorkScraper(BaseScraper):
    display_name = "Finder.work"
    API_URL = "https://api.finder.work/api/v1/vacancies"
    countries = CIS_COUNTRY_CODES
    requires_browser = False
    detail_requires_browser = False

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.CLIENT,        # distant_work field
        "country": FilterSupport.CLIENT,            # locations
        "experience": FilterSupport.CLIENT,         # experience enum field
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.CLIENT,         # salary_from/_to
        "query_match": FilterSupport.SERVER,
    }

    def search(self, params: SearchParams) -> list[JobListing]:
        data = fetch_json(
            f"{self.API_URL}?{urlencode({'search': params.query})}",
            timeout_seconds=self.fetch_timeout_seconds,
        )
        listings = [self._listing_from_item(item) for item in data.get("items", [])]
        return [listing for listing in listings if listing is not None][: self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    def _listing_from_item(self, item: dict) -> JobListing | None:
        title = normalize_text(str(item.get("title") or ""))
        item_id = item.get("id")
        if not title or item_id is None:
            return None

        company = item.get("company") or {}
        locations = item.get("locations") or []
        location = ", ".join(location.get("name", "") for location in locations).strip()

        external_url = item.get("external_url") or {}
        raw = {"id": item_id}
        if external_url.get("value"):
            raw["external_url"] = external_url["value"]
            raw["external_source"] = external_url.get("label")

        return JobListing(
            title=title,
            url=f"https://finder.work/vacancies/{item_id}",
            company=normalize_text(str(company.get("title") or "")),
            country=_country_from_text(location) or "RU",
            salary=self._format_salary(item),
            experience=self._normalize_finder_experience(item.get("experience")),
            remote=bool(item.get("distant_work")),
            location=location or None,
            description=normalize_text(re.sub(r"<[^>]+>", " ", item.get("short_description") or "")) or None,
            posted_date=item.get("publication_at"),
            source=self.name,
            raw=raw,
        )

    def _format_salary(self, item: dict) -> str | None:
        salary_from = item.get("salary_from") or 0
        salary_to = item.get("salary_to") or 0
        currency = item.get("currency_symbol") or ""
        if salary_from and salary_to:
            return f"{salary_from} - {salary_to} {currency}".strip()
        if salary_from:
            return f"from {salary_from} {currency}".strip()
        if salary_to:
            return f"to {salary_to} {currency}".strip()
        return None

    def _normalize_finder_experience(self, value: str | None) -> str | None:
        mapping = {
            "no_experience": "junior",
            "one_year_more": "junior",
            "one_three_years": "middle",
            "three_years_more": "senior",
        }
        return mapping.get(value or "")


@register_scraper("it_jobs_uz")
class ItJobsUzScraper(BaseScraper):
    display_name = "IT-Jobs.uz"
    API_URL = "https://www.it-jobs.uz/api/jobs"
    BASE_URL = "https://www.it-jobs.uz"
    countries = ("UZ",)
    requires_browser = False
    detail_requires_browser = False

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.CLIENT,        # workType enum
        "country": FilterSupport.CLIENT,            # UZ-only by design
        "experience": FilterSupport.CLIENT,         # experienceLevel enum
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.CLIENT,         # salaryMin/Max
        "query_match": FilterSupport.SERVER,
    }

    def search(self, params: SearchParams) -> list[JobListing]:
        data = fetch_json(self._build_search_url(params), timeout_seconds=self.fetch_timeout_seconds)
        listings = [
            listing for listing in (self._listing_from_item(item) for item in data.get("data", []))
            if listing is not None
        ]
        filtered = [listing for listing in listings if _listing_matches_query(listing, params.query)]
        return filtered[: self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    def _build_search_url(self, params: SearchParams) -> str:
        params_dict = {"search": params.query}
        category_slug = self._category_slug_for_query(params.query)
        if category_slug:
            params_dict["category"] = category_slug
        return f"{self.API_URL}?{urlencode(params_dict)}"

    def _category_slug_for_query(self, query: str) -> str | None:
        tokens = _query_tokens(query)
        categories = {
            "backend": {"backend", "бэкенд", "server", "node", "nodejs", "php", "golang", "go", "java", "python", "fastapi", "django"},
            "frontend": {"frontend", "фронтенд", "react", "angular", "vue", "javascript", "typescript", "next"},
            "mobile": {"mobile", "мобиль", "android", "ios", "flutter", "reactnative", "kotlin", "swift"},
            "qa": {"qa", "test", "testing", "тест", "quality", "aqa", "sdet"},
            "data": {"data", "ml", "machine", "learning", "ai", "аналитик", "analyst", "bi"},
            "design": {"design", "designer", "ux", "ui", "дизайн"},
            "pm": {"pm", "product", "project", "manager", "продакт", "проект"},
            "devops": {"devops", "sre", "infrastructure", "infra", "cloud", "kubernetes", "docker"},
            "security": {"security", "cyber", "pentest", "infosec", "безопас"},
        }
        for slug, markers in categories.items():
            if tokens & markers:
                return slug
        return None

    def _listing_from_item(self, item: dict) -> JobListing | None:
        title = normalize_text(str(item.get("title") or ""))
        slug = normalize_text(str(item.get("slug") or ""))
        if not title or not slug:
            return None

        apply_url = item.get("applyUrl") or item.get("sourceUrl")

        return JobListing(
            title=title,
            url=f"{self.BASE_URL}/en/jobs/{slug}",
            company=normalize_text(str(item.get("companyName") or "")),
            country="UZ",
            salary=self._format_salary(item),
            experience=self._normalize_experience(item.get("experienceLevel")),
            remote=str(item.get("workType") or "").upper() == "REMOTE",
            location=item.get("location"),
            description=item.get("description"),
            requirements=item.get("requirements"),
            skills=[str(tag) for tag in item.get("tags") or []],
            posted_date=item.get("publishedAt") or item.get("createdAt"),
            source=self.name,
            raw={
                "id": item.get("id"),
                "apply_url": apply_url,
                "category": ((item.get("category") or {}).get("slug") or ""),
            },
        )

    def _format_salary(self, item: dict) -> str | None:
        salary_min = item.get("salaryMin")
        salary_max = item.get("salaryMax")
        currency = item.get("salaryCurrency") or ""
        period = item.get("salaryPeriod") or ""
        suffix = f"{currency}/{period}".strip("/")
        if salary_min and salary_max:
            return f"{salary_min} - {salary_max} {suffix}".strip()
        if salary_min:
            return f"from {salary_min} {suffix}".strip()
        if salary_max:
            return f"to {salary_max} {suffix}".strip()
        return None

    def _normalize_experience(self, value: str | None) -> str | None:
        mapping = {
            "JUNIOR": "junior",
            "MIDDLE": "middle",
            "SENIOR": "senior",
        }
        return mapping.get(str(value or "").upper())


@register_scraper("jobturbo")
class JobTurboScraper(BaseScraper):
    display_name = "JobTurbo"
    BASE_URL = "https://jobturbo.ru"
    SEARCH_URL = "https://jobturbo.ru/vakansii/remote"
    countries = CIS_COUNTRY_CODES
    requires_browser = False
    detail_requires_browser = False

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.BEST_EFFORT,
        "country": FilterSupport.BEST_EFFORT,
        "experience": FilterSupport.UNSUPPORTED,
        "location": FilterSupport.BEST_EFFORT,
        "has_salary": FilterSupport.BEST_EFFORT,
        "query_match": FilterSupport.SERVER,
    }

    def search(self, params: SearchParams) -> list[JobListing]:
        html = fetch_text(self.SEARCH_URL, timeout_seconds=self.fetch_timeout_seconds)
        listings = self._parse_search_results(html, params)
        filtered = [listing for listing in listings if _listing_matches_query(listing, params.query)]
        return filtered[: self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    def _parse_search_results(self, html: str, params: SearchParams) -> list[JobListing]:
        listings = []
        for item_list in self._extract_item_lists(html):
            for entry in item_list.get("itemListElement", []):
                listing = self._listing_from_item(entry, params)
                if listing:
                    listings.append(listing)
        return listings

    def _extract_item_lists(self, html: str) -> list[dict]:
        payloads = re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.S,
        )
        item_lists: list[dict] = []
        for payload in payloads:
            data = json.loads(payload)
            entries = data if isinstance(data, list) else [data]
            item_lists.extend(entry for entry in entries if entry.get("@type") == "ItemList")
        return item_lists

    def _listing_from_item(self, item: dict, params: SearchParams) -> JobListing | None:
        name = normalize_text(str(item.get("name") or ""))
        url = normalize_text(str(item.get("url") or ""))
        if not name or "/vakansiya/" not in url:
            return None
        return JobListing(
            title=name,
            url=url,
            company="",
            country=params.country,
            remote=True,
            source=self.name,
        )


@register_scraper("getmatch")
class GetmatchScraper(BaseScraper):
    display_name = "getmatch"
    OFFERS_URL = "https://getmatch.ru/api/offers"
    SPECIALIZATIONS_URL = "https://getmatch.ru/api/specializations"
    BASE_URL = "https://getmatch.ru"
    countries = CIS_COUNTRY_CODES
    requires_browser = False
    detail_requires_browser = False

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.CLIENT,        # location_requirements.format
        "country": FilterSupport.CLIENT,
        "experience": FilterSupport.BEST_EFFORT,
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.CLIENT,         # salary_description
        "query_match": FilterSupport.SERVER,        # specialization
    }

    def search(self, params: SearchParams) -> list[JobListing]:
        slugs: list[str | None] = list(self._matching_specialization_slugs(params.query))
        if not slugs:
            slugs = [None]

        listings = []
        seen: set[str] = set()
        for slug in slugs:
            data = fetch_json(self._build_offers_url(params, slug), timeout_seconds=self.fetch_timeout_seconds)
            for item in data.get("offers", []):
                listing = self._listing_from_offer(item)
                if listing and listing.url not in seen:
                    seen.add(listing.url)
                    listings.append(listing)
                    if len(listings) >= self.max_results:
                        return listings
        return listings

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    def _matching_specialization_slugs(self, query: str) -> list[str]:
        query_tokens = _query_tokens(query)
        if not query_tokens:
            return []

        data = fetch_json(self.SPECIALIZATIONS_URL, timeout_seconds=self.fetch_timeout_seconds)
        slugs = []
        for item in data:
            category = item.get("category") or {}
            haystack = " ".join(
                str(value or "")
                for value in (
                    item.get("name"),
                    item.get("slug"),
                    category.get("name"),
                    category.get("slug"),
                )
            ).casefold()
            haystack_tokens = set(re.findall(r"[a-zа-яё0-9]+", haystack))
            if (
                query_tokens <= haystack_tokens
                if len(query_tokens) > 1
                else bool(query_tokens & haystack_tokens)
            ):
                slugs.append(str(item["slug"]))
        return slugs

    def _build_offers_url(self, params: SearchParams, specialization_slug: str | None) -> str:
        query_params = {
            "sa": "any",
            "p": "1",
            "offset": "0",
            "limit": str(max(self.max_results, 20)),
            "pa": "all",
        }
        if specialization_slug:
            query_params["sp"] = specialization_slug
        return f"{self.OFFERS_URL}?{urlencode(query_params)}"

    def _listing_from_offer(self, offer: dict) -> JobListing | None:
        title = normalize_text(str(offer.get("position") or ""))
        url = normalize_text(str(offer.get("url") or ""))
        if not title or not url:
            return None

        company = offer.get("company") or {}
        location_requirements = offer.get("location_requirements") or []
        location_items = offer.get("location_items") or []
        location = ", ".join(item.get("label", "") for item in location_items).strip()
        country_text = " ".join(item.get("country", "") for item in location_requirements)

        return JobListing(
            title=title,
            url=absolute_url(self.BASE_URL, url),
            company=normalize_text(str(company.get("name") or "")),
            country=_country_from_text(country_text),
            salary=offer.get("salary_description") or self._format_salary(offer),
            remote=any(item.get("format") == "remote" for item in location_requirements),
            location=location or None,
            description=normalize_text(re.sub(r"<[^>]+>", " ", offer.get("offer_description") or "")) or None,
            skills=[skill.get("name", "") for skill in offer.get("skills_objects") or [] if skill.get("name")],
            posted_date=offer.get("published_at"),
            source=self.name,
            raw={"id": offer.get("id"), "analytics_id": offer.get("analytics_id")},
        )

    def _format_salary(self, offer: dict) -> str | None:
        salary_from = offer.get("salary_display_from")
        salary_to = offer.get("salary_display_to")
        currency = offer.get("salary_currency") or ""
        if salary_from and salary_to:
            return f"{salary_from} - {salary_to} {currency}".strip()
        if salary_from:
            return f"from {salary_from} {currency}".strip()
        if salary_to:
            return f"to {salary_to} {currency}".strip()
        return None
