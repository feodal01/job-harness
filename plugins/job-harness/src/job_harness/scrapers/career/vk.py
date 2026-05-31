"""VK career site scraper — team.vk.company.

Uses __NEXT_DATA__ SSR JSON for structured data. Supports server-side
filtering by specialty (e.g. ?specialty=284 for QA).
"""

from __future__ import annotations

import json

from job_harness.models import JobListing, SearchParams
from job_harness.scrapers.career.base import BaseCareerScraper, register_career_scraper

# Specialty ID mapping for common filters
SPECIALTY_MAP = {
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


@register_career_scraper("vk")
class VKCareerScraper(BaseCareerScraper):
    company = "ВКонтакте"
    careers_url = "https://team.vk.company/vacancy/"

    def search(self, params: SearchParams) -> list[JobListing]:
        page = self.context.new_page()
        results = []
        try:
            url = self._build_url(params)
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

            vacancies = self._parse_next_data(page)
            if not vacancies:
                # Fallback: parse DOM if __NEXT_DATA__ unavailable
                vacancies = self._parse_dom(page)

            for v in vacancies[:params.max_results]:
                results.append(self._to_listing(v))
        finally:
            page.close()
        return results

    def _build_url(self, params: SearchParams) -> str:
        url = self.careers_url
        query_parts = []

        specialty_id = self._detect_specialty(params.query)
        if specialty_id:
            query_parts.append(f"specialty={specialty_id}")

        if params.remote_only:
            query_parts.append("remote=true")

        if params.query and not specialty_id:
            query_parts.append(f"search={params.query}")

        if query_parts:
            url += "?" + "&".join(query_parts)
        return url

    def _detect_specialty(self, query: str) -> int | None:
        q = query.lower()
        for keyword, sid in SPECIALTY_MAP.items():
            if keyword in q:
                return sid
        return None

    def _parse_next_data(self, page) -> list[dict]:
        try:
            data = page.evaluate("""() => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }""")
            if not data:
                return []
            parsed = json.loads(data)
            return parsed["props"]["pageProps"]["initialVacancies"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    def _parse_dom(self, page) -> list[dict]:
        vacancies = []
        links = page.locator("a.vacancy_vacancyItem__jrNqL")
        for i in range(min(links.count(), 50)):
            try:
                href = links.nth(i).get_attribute("href") or ""
                text = links.nth(i).inner_text().strip()
                if not text or href == "/vacancy/":
                    continue
                vacancies.append({"id": href.strip("/").split("/")[-1], "title": text, "href": href})
            except Exception:
                continue
        return vacancies

    def _to_listing(self, v: dict) -> JobListing:
        if "href" in v:
            # DOM fallback
            href = v["href"]
            url = f"https://team.vk.company{href}" if href.startswith("/") else href
            return self._make_listing(title=v.get("title", ""), url=url)

        # __NEXT_DATA__ structured
        vac_id = v.get("id", "")
        url = f"https://team.vk.company/vacancy/{vac_id}/"
        group = v.get("group", {}).get("name", "")
        town = v.get("town", {}).get("name", "")
        work_format = v.get("work_format", "")
        remote = v.get("remote", False)
        tags = [t["name"] for t in v.get("tags", [])]
        specialty = v.get("specialty", {}).get("name", "")

        location = town
        if work_format:
            location = f"{town}, {work_format}"

        return self._make_listing(
            title=v.get("title", ""),
            url=url,
            company=group or self.company,
            location=location,
            remote=remote,
            skills=tags,
            experience=specialty,
        )
