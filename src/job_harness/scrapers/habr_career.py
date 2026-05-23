"""Habr Career scraper."""

from __future__ import annotations

import sys

from job_harness.base import BaseScraper
from job_harness.models import JobListing, SearchParams
from job_harness.registry import register_scraper


@register_scraper("habr_career")
class HabrCareerScraper(BaseScraper):
    display_name = "Habr Career"
    BASE_URL = "https://career.habr.com/vacancies"

    def search(self, params: SearchParams) -> list[JobListing]:
        page = self.context.new_page()
        listings: list[JobListing] = []
        try:
            url = self._build_search_url(params)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            self._debug_screenshot(page, "search")

            listings = self._parse_search_results(page)

            # Pagination
            while len(listings) < self.max_results:
                next_btn = page.locator('a[rel="next"], .with-pagination__side-button--next')
                if not next_btn.is_visible():
                    break
                next_btn.click()
                page.wait_for_timeout(2000)
                more = self._parse_search_results(page)
                if not more:
                    break
                listings.extend(more)

        except Exception as e:
            print(f"HabrCareerScraper error: {e}", file=sys.stderr)
            self._debug_screenshot(page, "error")
        finally:
            page.close()

        return listings[:self.max_results]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        page = self.context.new_page()
        try:
            page.goto(listing.url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            self._debug_screenshot(page, listing.url.split("/")[-1])

            description = None
            desc_el = page.locator('.vacancy-description__content')
            if desc_el.is_visible():
                description = desc_el.inner_text()[:3000]

            requirements = None
            req_el = page.locator('.vacancy-description__requirements')
            if req_el.is_visible():
                requirements = req_el.inner_text()[:1500]

            # Enrich skills from detail page
            skills = list(listing.skills)
            skill_els = page.locator('.skill__name')
            for i in range(skill_els.count()):
                try:
                    s = skill_els.nth(i).inner_text().strip()
                    if s not in skills:
                        skills.append(s)
                except Exception:
                    continue

            return JobListing(
                title=listing.title,
                url=listing.url,
                company=listing.company,
                salary=listing.salary,
                experience=listing.experience,
                remote=listing.remote,
                location=listing.location,
                description=description,
                requirements=requirements,
                skills=skills,
                posted_date=listing.posted_date,
                source=listing.source,
                raw=listing.raw,
            )
        except Exception as e:
            print(f"Error fetching detail for {listing.url}: {e}", file=sys.stderr)
            return listing
        finally:
            page.close()

    def _build_search_url(self, params: SearchParams) -> str:
        query_params = {"q": params.query}
        if params.remote_only:
            query_params["remote"] = "true"
        if params.experience:
            query_params["qualification"] = params.experience
        query_params.update(params.extra)
        return self.BASE_URL + "?" + "&".join(f"{k}={v}" for k, v in query_params.items())

    def _parse_search_results(self, page) -> list[JobListing]:
        listings = []
        cards = page.locator('.vacancy-card')
        for i in range(cards.count()):
            try:
                card = cards.nth(i)

                title_el = card.locator('.vacancy-card__title-link')
                if not title_el.is_visible():
                    continue
                title = title_el.inner_text()
                href = title_el.get_attribute("href") or ""
                url = "https://career.habr.com" + href

                company_el = card.locator('.vacancy-card__company a')
                company = company_el.inner_text() if company_el.is_visible() else ""

                salary_el = card.locator('.basic-salary')
                salary = salary_el.inner_text() if salary_el.is_visible() else None

                # Skills from card
                skills = []
                skill_els = card.locator('.vacancy-card__skills-chip .basic-chip__text')
                for j in range(skill_els.count()):
                    try:
                        skills.append(skill_els.nth(j).inner_text().strip())
                    except Exception:
                        continue

                # Remote
                is_remote = bool(
                    card.locator('text="Можно удалённо"').count()
                    or card.locator('text="Можно из дома"').count()
                )

                # Experience from chips
                experience = None
                chip_texts = card.locator('.chip-with-icon__text')
                for j in range(chip_texts.count()):
                    try:
                        chip_text = chip_texts.nth(j).inner_text().strip()
                        experience = self.normalize_experience(chip_text)
                        if experience:
                            break
                    except Exception:
                        continue

                listings.append(JobListing(
                    title=title.strip(),
                    url=url,
                    company=company.strip(),
                    salary=salary.strip() if salary else None,
                    experience=experience,
                    remote=is_remote,
                    source=self.name,
                    skills=skills,
                ))
            except Exception:
                continue
        return listings
