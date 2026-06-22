"""VK career site scraper — team.vk.company.

Uses __NEXT_DATA__ SSR JSON for structured data when available, falls
back to DOM parsing. Supports server-side filtering by specialty via
the `specialty=` URL param (e.g. specialty=284 for QA).

Async-only: dispatched by SearchEngine through BrowserPool.
"""

from __future__ import annotations

import json
from typing import ClassVar

from job_harness.v1.base import BaseBrowserScraper
from job_harness.v1.browser_pool import raise_for_blocked_response
from job_harness.v1.models import RawListing, SearchParams
from job_harness.v1.registry import register_scraper
from job_harness.v1.types import FilterSupport, ScraperCapabilities, SearchCriterion, SourceGroup

# VK's specialty IDs for common queries.
_SPECIALTY_MAP = {
    "qa": 284,
    "backend": 282,
    "frontend": 287,
    "data": 269,
    "devops": 278,
    "ml": 283,
    "mobile": 286,
    "smm": 288,
    "ux": 285,
}


@register_scraper("career:vk")
class VKCareerScraper(BaseBrowserScraper):
    display_name = "ВКонтакте (career)"
    BASE_URL = "https://team.vk.company/vacancy/"
    countries = ("RU",)
    source_group = SourceGroup.COMPANY_CAREER
    source_limit = 100
    server_criteria = frozenset(
        {
            SearchCriterion.QUERY,
            SearchCriterion.COUNTRY,
            SearchCriterion.REMOTE_ONLY,
        }
    )
    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.SERVER,            # remote=true URL param
        "country": FilterSupport.CLIENT,                # RU-only
        "experience": FilterSupport.UNSUPPORTED,
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.UNSUPPORTED,
        "query_match": FilterSupport.SERVER,            # specialty=
    }

    async def search_with_page(self, page, params: SearchParams) -> list[RawListing]:
        url = self._build_url(params)
        response = await page.goto(url, wait_until="domcontentloaded")
        raise_for_blocked_response(response)
        await page.wait_for_timeout(1000)

        # Prefer __NEXT_DATA__ if present — it contains structured rows.
        vacancies = await self._read_next_data(page)
        if not vacancies:
            vacancies = await self._read_dom(page)

        listings: list[RawListing] = []
        for v in vacancies[: self.max_results]:
            listings.append(self._to_listing(v))
        return listings

    # ----- internal -------------------------------------------------------

    def _build_url(self, params: SearchParams) -> str:
        parts: list[str] = []
        specialty_id = self._detect_specialty(params.query)
        if specialty_id:
            parts.append(f"specialty={specialty_id}")
        if params.remote_only:
            parts.append("remote=true")
        if params.query and specialty_id is None:
            parts.append(f"search={params.query}")
        return self.BASE_URL + ("?" + "&".join(parts) if parts else "")

    @staticmethod
    def _detect_specialty(query: str) -> int | None:
        lowered = query.lower()
        for keyword, sid in _SPECIALTY_MAP.items():
            if keyword in lowered:
                return sid
        return None

    async def _read_next_data(self, page) -> list[dict]:
        try:
            data = await page.evaluate(
                "() => { const el = document.getElementById('__NEXT_DATA__');"
                " return el ? el.textContent : null; }"
            )
        except Exception:
            return []
        if not data:
            return []
        try:
            parsed = json.loads(data)
            value = parsed["props"]["pageProps"]["initialVacancies"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    async def _read_dom(self, page) -> list[dict]:
        # Fallback when __NEXT_DATA__ isn't present (rare).
        items = page.locator("a.vacancy_vacancyItem__jrNqL")
        count = await items.count()
        out: list[dict] = []
        for i in range(min(count, 50)):
            try:
                href = await items.nth(i).get_attribute("href") or ""
                text = (await items.nth(i).inner_text()).strip()
                if not text or href == "/vacancy/":
                    continue
                out.append({"id": href.strip("/").split("/")[-1], "title": text, "href": href})
            except Exception:
                continue
        return out

    def _to_listing(self, v: dict) -> RawListing:
        if "href" in v:
            href = v["href"]
            url = (
                f"https://team.vk.company{href}"
                if href.startswith("/")
                else href
            )
            return RawListing(
                title=v.get("title", ""),
                url=url,
                company=self.display_name,
                country="RU",
                source=self.name,
            )

        # Structured __NEXT_DATA__ shape.
        vac_id = v.get("id", "")
        url = f"https://team.vk.company/vacancy/{vac_id}/"
        group = v.get("group", {}).get("name", "")
        town = v.get("town", {}).get("name", "")
        work_format = v.get("work_format", "")
        remote = v.get("remote", False)
        tags = [t["name"] for t in v.get("tags", [])]

        location = town
        if work_format:
            location = f"{town}, {work_format}"

        return RawListing(
            title=v.get("title", ""),
            url=url,
            company=group or self.display_name,
            country="RU",
            location=location,
            remote=remote,
            skills=tuple(tags),
            source=self.name,
        )
