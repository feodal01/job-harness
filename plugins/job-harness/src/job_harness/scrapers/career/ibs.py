"""IBS career site scraper — ibs.ru/career/vacancies/.

Bitrix-powered site. Uses SEF (Search Engine Friendly) filter URLs for
server-side filtering: /career/vacancies/filter/napravlenie-is-testirovanie/apply/.

Async-only: dispatched by SearchEngine through BrowserPool.
"""

from __future__ import annotations

from typing import ClassVar

from job_harness.base import BaseBrowserScraper
from job_harness.models import RawListing, SearchParams
from job_harness.registry import register_scraper
from job_harness.types import FilterSupport, ScraperCapabilities, SearchCriterion, SourceGroup

# Query keyword → Bitrix SEF segment mapping (property 210 — primary direction).
_DIRECTION_MAP = {
    "тестиров": "testirovanie",
    "qa": "testirovanie",
    "test": "testirovanie",
    "разработ": "razrabotka",
    "dev": "razrabotka",
    "аналит": "analitika-i-konsalting",
    "архитект": "arkhitektura",
    "проект": "upravlenie-proektami",
    "бек": "bek-ofis",
    "продаж": "razvitie-biznesa-prodazhi",
    "инженер": "inzhenery",
}

# Sub-category filters (property 523 — specialisation within direction).
_SUBCATEGORY_MAP = {
    "ручн": "ruchnoe-testirovanie",
    "авто": "avtomatizirovannoe-testirovanie",
    "нагруз": "nagruzochnoe-testirovanie",
    "1c": "1c",
    "би": "biznes-analiz",
    "внедрен": "vnedrenie",
    "системн": "sistemnyj-analiz",
    "data": "upravlenie-dannymi",
}

# Work format filters (property 209).
_FORMAT_MAP = {
    "удален": "online",
    "офис": "office",
    "гибрид": "flexible",
}

# City filters (property 217).
_CITY_MAP = {
    "москва": "moscow",
    "мск": "moscow",
    "питер": "spb",
    "спб": "spb",
    "казань": "kzn",
    "пермь": "perm",
    "тюмень": "tyumen",
}

_BASE_URL = "https://ibs.ru/career/vacancies/"
_BASE_FILTER_URL = "https://ibs.ru/career/vacancies/filter"


@register_scraper("career:ibs")
class IBSCareerScraper(BaseBrowserScraper):
    display_name = "IBS (career)"
    countries = ("RU",)
    source_group = SourceGroup.COMPANY_CAREER
    source_limit = 100
    server_criteria = frozenset(
        {
            SearchCriterion.QUERY,
            SearchCriterion.COUNTRY,
            SearchCriterion.REMOTE_ONLY,
            SearchCriterion.LOCATION,
        }
    )
    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.SERVER,        # format-is-online segment
        "country": FilterSupport.CLIENT,            # RU-only
        "experience": FilterSupport.UNSUPPORTED,
        "location": FilterSupport.SERVER,           # gorod-is-* segment
        "has_salary": FilterSupport.UNSUPPORTED,
        "query_match": FilterSupport.SERVER,        # napravlenie-is-* segment
    }

    async def search_with_page(self, page, params: SearchParams) -> list[RawListing]:
        url = self._build_filter_url(params)
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        listings: list[RawListing] = []
        items = page.locator("a.jobs-item")
        count = await items.count()
        for i in range(min(count, self.max_results)):
            try:
                item = items.nth(i)
                href = await item.get_attribute("href") or ""
                if not href:
                    continue

                title_loc = item.locator(".jobs-item-title")
                title = (await title_loc.inner_text()).strip() if await title_loc.count() > 0 else ""

                tags_loc = item.locator(".jobs-item-tags")
                tags_text = (await tags_loc.inner_text()).strip() if await tags_loc.count() > 0 else ""

                desc_loc = item.locator(".jobs-item-desc")
                description = (await desc_loc.inner_text()).strip() if await desc_loc.count() > 0 else ""

                if href.startswith("/"):
                    href = "https://ibs.ru" + href

                tags = [t.strip().lstrip("#").strip() for t in tags_text.split("\n") if t.strip()]
                remote = "УДАЛЕННО" in tags_text.upper()

                listings.append(
                    RawListing(
                        title=title or href,
                        url=href,
                        company="IBS",
                        country="RU",
                        remote=remote,
                        skills=tuple(tags),
                        description=description or None,
                        source=self.name,
                    )
                )
            except Exception:
                continue
        return listings

    # ----- URL builder ---------------------------------------------------

    def _build_filter_url(self, params: SearchParams) -> str:
        segments: list[str] = []
        q = params.query.lower()

        direction = self._match_keyword(q, _DIRECTION_MAP)
        if direction:
            segments.append(f"napravlenie-is-{direction}")

        subcat = self._match_keyword(q, _SUBCATEGORY_MAP)
        if subcat:
            segments.append(subcat)

        if params.remote_only:
            segments.append("format-is-online")
        else:
            fmt = self._match_keyword(q, _FORMAT_MAP)
            if fmt:
                segments.append(f"format-is-{fmt}")

        city = _CITY_MAP.get((params.location or "").lower()) or self._match_keyword(q, _CITY_MAP)
        if city:
            segments.append(f"gorod-is-{city}")

        if segments:
            return f"{_BASE_FILTER_URL}/{'/'.join(segments)}/apply/"
        return _BASE_URL

    @staticmethod
    def _match_keyword(query: str, mapping: dict[str, str]) -> str | None:
        for keyword, value in mapping.items():
            if keyword in query:
                return value
        return None
