"""hh.ru and family (hh.kz, hh.uz, rabota.by, headhunter.kg) async scrapers.

Dispatched by the SearchEngine through the async BrowserPool. The pool
hands `search_with_page(page, params)` an async Playwright Page with
the per-call deadline already wrapped in `asyncio.wait_for`. Anti-bot
detection runs after the callable returns.

The five subclasses share everything except BASE_URL + countries —
selectors and parse logic are inherited verbatim.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlencode

from job_harness.base import BaseBrowserScraper
from job_harness.models import JobListing, SearchParams
from job_harness.registry import register_scraper
from job_harness.types import FilterSupport, ScraperCapabilities

# hh.ru's data-qa attributes used by both layouts. The "serp-item__..."
# selectors are the current layout; the older "vacancy-serp__..." names
# are kept as a fallback because hh.ru ships A/B variants.
_TITLE_PRIMARY = '[data-qa="serp-item__title-text"]'
_TITLE_FALLBACK = '[data-qa="vacancy-serp__vacancy-title"]'
_LINK_PRIMARY = '[data-qa="serp-item__title"]'
_LINK_FALLBACK = '[data-qa="vacancy-serp__vacancy-title"]'
_COMPANY_PRIMARY = '[data-qa="vacancy-serp__vacancy-employer-text"]'
_COMPANY_FALLBACK = '[data-qa="vacancy-serp__vacancy-employer"]'
_SALARY_SELECTOR = '[data-qa="vacancy-serp__vacancy-compensation"]'
_EXPERIENCE_SELECTOR = '[data-qa^="vacancy-serp__vacancy-work-experience"]'
_REMOTE_LABEL = '[data-qa="vacancy-label-work-schedule-remote"]'
_CARD_SELECTOR = '[data-qa="vacancy-serp__vacancy"]'
_PAGER_NEXT = '[data-qa="pager-next"]'

_DESCRIPTION_SELECTOR = '[data-qa="vacancy-description"]'
_SKILLS_SELECTOR = '[data-qa="skills-element"]'

_EXPERIENCE_URL_MAP = {
    "junior": "noExperience",
    "middle": "between1And3",
    "senior": "between3And6",
}


@register_scraper("hh_ru")
class HHRuScraper(BaseBrowserScraper):
    display_name = "hh.ru"
    BASE_URL = "https://hh.ru/search/vacancy"
    countries = ("RU",)

    # remote_only via schedule=remote URL param; country via subdomain;
    # experience via mapping in _build_search_url; query via text= param.
    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.SERVER,
        "country": FilterSupport.SERVER,
        "experience": FilterSupport.SERVER,
        "location": FilterSupport.BEST_EFFORT,
        "has_salary": FilterSupport.UNSUPPORTED,
        "query_match": FilterSupport.SERVER,
    }

    # ----- async dispatch path -------------------------------------------

    async def search_with_page(self, page, params: SearchParams) -> list[JobListing]:
        """Open the search URL, parse the first page, paginate until
        `max_results` is reached. The pool's `asyncio.wait_for` enforces
        the wall-clock deadline; we do not poll a cooperative one here.
        """
        url = self._build_search_url(params)
        await page.goto(url, wait_until="domcontentloaded")
        # Brief settle for late-rendered cards. The pool's deadline
        # bounds this; if the page is mid-anti-bot it is detected by
        # the pool's `is_blocked` probe after this call returns.
        await page.wait_for_timeout(800)

        listings: list[JobListing] = []
        seen_urls: set[str] = set()

        while len(listings) < self.max_results:
            batch = await self._parse_cards_async(page)
            new_items = [item for item in batch if item.url not in seen_urls]
            if not new_items:
                break
            for listing in new_items:
                seen_urls.add(listing.url)
                listings.append(listing)
                if len(listings) >= self.max_results:
                    break
            if len(listings) >= self.max_results:
                break

            next_btn = page.locator(_PAGER_NEXT)
            try:
                if not await next_btn.is_visible():
                    break
                await next_btn.click()
                await page.wait_for_timeout(800)
            except Exception:
                break

        return listings[: self.max_results]

    async def fetch_detail_with_page(self, listing: JobListing, page) -> JobListing:
        await page.goto(listing.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(500)

        description = None
        desc_el = page.locator(_DESCRIPTION_SELECTOR)
        if await desc_el.count() > 0:
            description = (await desc_el.inner_text())[:3000]

        skills = list(listing.skills)
        skill_els = page.locator(_SKILLS_SELECTOR)
        count = await skill_els.count()
        for i in range(count):
            try:
                text = (await skill_els.nth(i).inner_text()).strip()
                if text:
                    skills.append(text)
            except Exception:
                continue

        return JobListing(
            title=listing.title,
            url=listing.url,
            company=listing.company,
            country=listing.country,
            salary=listing.salary,
            experience=listing.experience,
            remote=listing.remote,
            location=listing.location,
            description=description,
            skills=skills if skills else listing.skills,
            posted_date=listing.posted_date,
            source=listing.source,
            raw=listing.raw,
        )

    # ----- URL builder (shared) -------------------------------------------

    def _build_search_url(self, params: SearchParams) -> str:
        query_params: dict[str, str] = {
            "text": params.query,
            "area": "0",
            "search_field": "name",
        }
        if params.remote_only:
            query_params["schedule"] = "remote"
        if len(params.experience_levels) == 1:
            level = params.experience_levels[0]
            if level in _EXPERIENCE_URL_MAP:
                query_params["experience"] = _EXPERIENCE_URL_MAP[level]
        query_params.update(params.extra)
        return self.BASE_URL + "?" + urlencode(query_params)

    # ----- async parsing helpers -----------------------------------------

    async def _parse_cards_async(self, page) -> list[JobListing]:
        """Read every visible vacancy card on the current page."""
        for attempt in range(3):
            try:
                return await self._parse_cards_once(page)
            except Exception as exc:
                if not _is_navigation_context_error(exc) or attempt == 2:
                    raise
                await _settle_after_navigation(page)
        return []

    async def _parse_cards_once(self, page) -> list[JobListing]:
        listings: list[JobListing] = []
        cards = page.locator(_CARD_SELECTOR)
        count = await cards.count()
        for i in range(count):
            try:
                listing = await self._parse_one_card(cards.nth(i))
            except Exception:
                continue
            if listing is not None:
                listings.append(listing)
        return listings

    async def _parse_one_card(self, card) -> JobListing | None:
        title = await _first_text(card, (_TITLE_PRIMARY, _TITLE_FALLBACK))
        if not title:
            return None

        href = await _first_attribute(card, (_LINK_PRIMARY, _LINK_FALLBACK), "href")
        url = (href or "").split("?")[0]

        company = await _first_text(card, (_COMPANY_PRIMARY, _COMPANY_FALLBACK)) or ""

        salary = await _first_text(card, (_SALARY_SELECTOR,))
        raw_exp = await _first_text(card, (_EXPERIENCE_SELECTOR,))
        experience = self.normalize_experience(raw_exp) if raw_exp else None

        is_remote_locator = card.locator(_REMOTE_LABEL)
        is_remote = await is_remote_locator.count() > 0

        return JobListing(
            title=title.strip(),
            url=url,
            company=company.strip(),
            country=self.countries[0] if self.countries else None,
            salary=salary.strip() if salary else None,
            experience=experience,
            remote=bool(is_remote),
            source=self.name,
            raw={"experience_raw": raw_exp} if raw_exp else {},
        )


# ---------------------------------------------------------------------------
# Async helpers for layout-resilient extraction
# ---------------------------------------------------------------------------


async def _first_text(card: Any, selectors: tuple[str, ...]) -> str | None:
    """Return inner_text of the first selector that matches at least one
    element. Returns None if no selector matches."""
    for selector in selectors:
        loc = card.locator(selector)
        if await loc.count() > 0:
            try:
                return await loc.first.inner_text()
            except Exception:
                continue
    return None


async def _first_attribute(card: Any, selectors: tuple[str, ...], name: str) -> str | None:
    for selector in selectors:
        loc = card.locator(selector)
        if await loc.count() > 0:
            try:
                return await loc.first.get_attribute(name)
            except Exception:
                continue
    return None


async def _settle_after_navigation(page: Any) -> None:
    wait_for_load_state = getattr(page, "wait_for_load_state", None)
    if callable(wait_for_load_state):
        try:
            await wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
    await page.wait_for_timeout(500)


def _is_navigation_context_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "execution context was destroyed" in message
        or "most likely because of a navigation" in message
        or "cannot find context with specified id" in message
    )


# ---------------------------------------------------------------------------
# Country variants
# ---------------------------------------------------------------------------


@register_scraper("hh_kz")
class HHKzScraper(HHRuScraper):
    display_name = "hh.kz"
    BASE_URL = "https://hh.kz/search/vacancy"
    countries = ("KZ",)


@register_scraper("hh_uz")
class HHUzScraper(HHRuScraper):
    display_name = "hh.uz"
    BASE_URL = "https://hh.uz/search/vacancy"
    countries = ("UZ",)


@register_scraper("rabota_by")
class RabotaByScraper(HHRuScraper):
    display_name = "rabota.by"
    BASE_URL = "https://rabota.by/search/vacancy"
    countries = ("BY",)


@register_scraper("headhunter_kg")
class HeadHunterKgScraper(HHRuScraper):
    display_name = "headhunter.kg"
    BASE_URL = "https://headhunter.kg/search/vacancy"
    countries = ("KG",)
