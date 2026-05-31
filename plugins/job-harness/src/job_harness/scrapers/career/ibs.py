"""IBS career site scraper — ibs.ru/career/vacancies/.

Bitrix-powered site. Uses SEF (Search Engine Friendly) filter URLs for
server-side filtering, e.g. /career/vacancies/filter/napravlenie-is-testirovanie/apply/.
Vacancies are in a.jobs-item cards with .jobs-item-title, .jobs-item-tags,
.jobs-item-desc.
"""

from __future__ import annotations

from job_harness.models import JobListing, SearchParams
from job_harness.scrapers.career.base import BaseCareerScraper, register_career_scraper

# Query keyword → Bitrix SEF URL_ID mapping
# Property 210 (Направление) is the primary category filter
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

# Sub-category filters (property 523 — specialization within direction)
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

# Work format filters (property 209)
_FORMAT_MAP = {
    "удален": "online",
    "офис": "office",
    "гибрид": "flexible",
}

# City filters (property 217)
_CITY_MAP = {
    "москва": "moscow",
    "мск": "moscow",
    "питер": "spb",
    "спб": "spb",
    "казань": "kzn",
    "пермь": "perm",
    "тюмень": "tyumen",
}

_BASE_FILTER_URL = "https://ibs.ru/career/vacancies/filter"


@register_career_scraper("ibs")
class IBSCareerScraper(BaseCareerScraper):
    company = "IBS"
    careers_url = "https://ibs.ru/career/vacancies/"

    def search(self, params: SearchParams) -> list[JobListing]:
        url = self._build_filter_url(params)
        page = self.context.new_page()
        results = []
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

            items = page.locator("a.jobs-item")
            for i in range(min(items.count(), params.max_results)):
                try:
                    item = items.nth(i)
                    href = item.get_attribute("href") or ""
                    if not href:
                        continue

                    title_el = item.locator(".jobs-item-title")
                    title = title_el.inner_text().strip() if title_el.count() > 0 else ""

                    tags_el = item.locator(".jobs-item-tags")
                    tags_text = tags_el.inner_text().strip() if tags_el.count() > 0 else ""

                    desc_el = item.locator(".jobs-item-desc")
                    description = desc_el.inner_text().strip() if desc_el.count() > 0 else ""

                    if href.startswith("/"):
                        href = "https://ibs.ru" + href

                    tags = [t.strip().lstrip("#").strip() for t in tags_text.split("\n") if t.strip()]
                    remote = "УДАЛЕННО" in tags_text.upper()

                    listing = self._make_listing(
                        title=title or href,
                        url=href,
                        remote=remote,
                        skills=tags,
                        description=description or None,
                    )
                    results.append(listing)
                except Exception:
                    continue
        finally:
            page.close()
        return results

    def _build_filter_url(self, params: SearchParams) -> str:
        """Build a Bitrix SEF filter URL from search parameters.

        Filter segments are slash-separated:
        /filter/napravlenie-is-testirovanie/format-is-online/apply/
        """
        segments = []
        q = params.query.lower()

        # Primary direction filter (property 210)
        direction = self._match_keyword(q, _DIRECTION_MAP)
        if direction:
            segments.append(f"napravlenie-is-{direction}")

        # Sub-category filter (property 523)
        subcat = self._match_keyword(q, _SUBCATEGORY_MAP)
        if subcat:
            segments.append(subcat)

        # Remote only or work format from query (property 209)
        if params.remote_only:
            segments.append("format-is-online")
        else:
            fmt = self._match_keyword(q, _FORMAT_MAP)
            if fmt:
                segments.append(f"format-is-{fmt}")

        # City from query or location param (property 217)
        city = _CITY_MAP.get((params.location or "").lower()) or self._match_keyword(q, _CITY_MAP)
        if city:
            segments.append(f"gorod-is-{city}")

        if segments:
            return f"{_BASE_FILTER_URL}/{'/'.join(segments)}/apply/"
        return self.careers_url

    @staticmethod
    def _match_keyword(query: str, mapping: dict[str, str]) -> str | None:
        for keyword, value in mapping.items():
            if keyword in query:
                return value
        return None
