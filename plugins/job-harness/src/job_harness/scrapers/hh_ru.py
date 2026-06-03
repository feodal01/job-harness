"""hh.ru scraper."""

from __future__ import annotations

import sys

from job_harness.base import BaseScraper
from job_harness.models import JobListing, SearchParams
from job_harness.registry import register_scraper


@register_scraper("hh_ru")
class HHRuScraper(BaseScraper):
    display_name = "hh.ru"
    BASE_URL = "https://hh.ru/search/vacancy"
    countries = ("RU",)

    def search(self, params: SearchParams) -> list[JobListing]:
        page = self.context.new_page()
        listings: list[JobListing] = []
        try:
            self._configure_page_timeout(page)
            url = self._build_search_url(params)
            self.enforce_deadline()
            page.goto(url, wait_until="domcontentloaded", timeout=self.remaining_timeout_ms())
            page.wait_for_timeout(2000)
            self._debug_screenshot(page, "search")

            if "Доступ ограничен" in page.title() or "подтвердите" in page.title().lower():
                print("WARNING: hh.ru may have detected automation.", file=sys.stderr)

            listings = self._parse_search_results(page)

            # Pagination
            while len(listings) < self.max_results:
                self.enforce_deadline()
                next_btn = page.locator('[data-qa="pager-next"]')
                if not next_btn.is_visible():
                    break
                next_btn.click()
                page.wait_for_timeout(2000)
                more = self._parse_search_results(page)
                if not more:
                    break
                listings.extend(more)

        except Exception as e:
            self.runtime_error = e
            if _is_timeout_error(e):
                self.mark_timed_out()
            print(f"HHRuScraper error: {e}", file=sys.stderr)
            self._debug_screenshot(page, "error")
        finally:
            page.close()

        return listings[:self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        page = self.context.new_page()
        try:
            self._configure_page_timeout(page)
            self.enforce_deadline()
            page.goto(listing.url, wait_until="domcontentloaded", timeout=self.remaining_timeout_ms())
            page.wait_for_timeout(2000)
            self._debug_screenshot(page, listing.url.split("/")[-1])

            description = None
            desc_el = page.locator('[data-qa="vacancy-description"]')
            if desc_el.is_visible():
                description = desc_el.inner_text()[:3000]

            skills = list(listing.skills)
            skill_els = page.locator('[data-qa="skills-element"]')
            for i in range(skill_els.count()):
                try:
                    skills.append(skill_els.nth(i).inner_text().strip())
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
        except Exception as e:
            self.runtime_error = e
            if _is_timeout_error(e):
                self.mark_timed_out()
            print(f"Error fetching detail for {listing.url}: {e}", file=sys.stderr)
            return listing
        finally:
            page.close()

    def _configure_page_timeout(self, page) -> None:
        if self.timeout_ms is None:
            return
        page.set_default_timeout(self.timeout_ms)
        page.set_default_navigation_timeout(self.timeout_ms)

    def _build_search_url(self, params: SearchParams) -> str:
        query_params = {
            "text": params.query,
            "area": "0",
            "search_field": "name",
        }
        if params.remote_only:
            query_params["schedule"] = "remote"
        if params.experience:
            exp_map = {
                "junior": "noExperience",
                "middle": "between1And3",
                "senior": "between3And6",
            }
            if params.experience in exp_map:
                query_params["experience"] = exp_map[params.experience]
        # Merge extra params
        query_params.update(params.extra)
        return self.BASE_URL + "?" + "&".join(f"{k}={v}" for k, v in query_params.items())

    def _parse_search_results(self, page) -> list[JobListing]:
        listings = []
        cards = page.locator('[data-qa="vacancy-serp__vacancy"]')
        for i in range(cards.count()):
            try:
                card = cards.nth(i)

                # Title — new layout first, fallback to old
                title_el = card.locator('[data-qa="serp-item__title-text"]')
                if title_el.count() == 0:
                    title_el = card.locator('[data-qa="vacancy-serp__vacancy-title"]')
                if title_el.count() == 0:
                    continue
                title = title_el.inner_text()

                # Link
                link_el = card.locator('[data-qa="serp-item__title"]')
                if link_el.count() == 0:
                    link_el = card.locator('[data-qa="vacancy-serp__vacancy-title"]')
                url = (link_el.get_attribute("href") or "").split("?")[0]

                # Company
                company_el = card.locator('[data-qa="vacancy-serp__vacancy-employer-text"]')
                if company_el.count() == 0:
                    company_el = card.locator('[data-qa="vacancy-serp__vacancy-employer"]')
                company = company_el.inner_text() if company_el.count() > 0 else ""

                # Salary
                salary_el = card.locator('[data-qa="vacancy-serp__vacancy-compensation"]')
                salary = salary_el.inner_text() if salary_el.count() > 0 else None

                # Experience
                exp_el = card.locator('[data-qa^="vacancy-serp__vacancy-work-experience"]')
                raw_exp = exp_el.inner_text() if exp_el.count() > 0 else None
                experience = self.normalize_experience(raw_exp)

                # Remote
                is_remote = bool(card.locator('[data-qa="vacancy-label-work-schedule-remote"]').count())

                listings.append(JobListing(
                    title=title.strip(),
                    url=url,
                    company=company.strip(),
                    country=self.countries[0] if self.countries else None,
                    salary=salary.strip() if salary else None,
                    experience=experience,
                    remote=is_remote,
                    source=self.name,
                    raw={"experience_raw": raw_exp} if raw_exp else {},
                ))
            except Exception:
                continue
        return listings


def _is_timeout_error(error: Exception) -> bool:
    return isinstance(error, TimeoutError) or "timeout" in type(error).__name__.casefold() or "timeout" in str(error).casefold()


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
