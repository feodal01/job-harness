"""Known company career-page probing as a normal search source."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from job_harness.base import BaseBrowserScraper
from job_harness.company_directory import CompanyProfile
from job_harness.models import JobListing, SearchParams
from job_harness.registry import register_scraper
from job_harness.types import FilterSupport, ScraperCapabilities

_SOFT_DEADLINE_BUFFER_MS = 3_000
_PER_COMPANY_TIMEOUT_MS = 5_000
_COMPANY_CAREER_WORKERS = 12


def _query_terms(query: str) -> list[str]:
    from job_harness.company_career_search import _query_terms as impl

    return impl(query)


def _load_company_targets(**kwargs: Any) -> list[CompanyProfile]:
    from job_harness.company_career_batch import _load_company_targets as impl

    return impl(**kwargs)


async def _check_company(
    context: Any,
    company: CompanyProfile,
    query_terms: list[str],
    timeout_ms: int,
) -> dict:
    from job_harness.company_career_batch import _check_company as impl

    return await impl(context, company, query_terms, timeout_ms)


@register_scraper("company_careers")
class CompanyCareersScraper(BaseBrowserScraper):
    display_name = "Known Company Careers"
    requires_browser = True
    detail_requires_browser = True

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.BEST_EFFORT,
        "country": FilterSupport.CLIENT,
        "experience": FilterSupport.UNSUPPORTED,
        "location": FilterSupport.BEST_EFFORT,
        "has_salary": FilterSupport.UNSUPPORTED,
        "query_match": FilterSupport.BEST_EFFORT,
    }

    async def search_with_page(self, page: Any, params: SearchParams) -> list[JobListing]:
        query_terms = _query_terms(params.query)
        companies = _load_company_targets(
            country=params.location or params.country,
            remote_only=params.remote_only,
            max_results=None,
        )
        context = getattr(page, "context", None) or _SinglePageContext(page)
        queue: asyncio.Queue[CompanyProfile] = asyncio.Queue()
        for company in companies:
            queue.put_nowait(company)

        listings: list[JobListing] = []
        worker_count = 1 if isinstance(context, _SinglePageContext) else _COMPANY_CAREER_WORKERS

        async def worker() -> None:
            while True:
                try:
                    company = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                try:
                    await self._check_one_company(
                        context=context,
                        company=company,
                        query_terms=query_terms,
                        params=params,
                        listings=listings,
                    )
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker(), name=f"company-careers:{index}")
            for index in range(min(worker_count, len(companies)))
        ]
        if workers:
            await asyncio.gather(*workers)

        if not queue.empty():
            self.mark_timed_out()

        return listings

    async def _check_one_company(
        self,
        *,
        context: Any,
        company: CompanyProfile,
        query_terms: list[str],
        params: SearchParams,
        listings: list[JobListing],
    ) -> None:
        remaining_ms = self.remaining_timeout_ms()
        if remaining_ms <= _SOFT_DEADLINE_BUFFER_MS:
            self.mark_timed_out()
            return
        company_timeout_ms = min(
            _PER_COMPANY_TIMEOUT_MS,
            max(1, remaining_ms - _SOFT_DEADLINE_BUFFER_MS),
        )
        try:
            record = await asyncio.wait_for(
                _check_company(
                    context,
                    company,
                    query_terms,
                    timeout_ms=company_timeout_ms,
                ),
                timeout=max(0.001, company_timeout_ms / 1000),
            )
        except TimeoutError:
            self.mark_timed_out()
            return
        except Exception:
            return
        if record.get("status") != "ok":
            return
        for hit in record.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            if params.remote_only and hit.get("remote_match") is False:
                continue
            listings.append(_listing_from_hit(hit, source=self.name, method=record.get("method")))
            remaining_ms = self.remaining_timeout_ms()
            if remaining_ms <= _SOFT_DEADLINE_BUFFER_MS:
                self.mark_timed_out()
                break


class _SinglePageContext:
    """Fallback adapter for tests that pass a bare fake Page."""

    def __init__(self, page: Any) -> None:
        self._page = page
        self._used = False

    async def new_page(self) -> Any:
        if self._used:
            raise RuntimeError("page has no context for additional company probes")
        self._used = True
        return self._page


def _listing_from_hit(hit: dict[str, Any], *, source: str, method: Any) -> JobListing:
    countries = [str(country) for country in hit.get("countries") or [] if country]
    stack = [str(item) for item in hit.get("stack") or [] if item]
    job_types = [str(item) for item in hit.get("job_types") or [] if item]
    matched_text = str(hit.get("matched_text") or "")
    remote_match = hit.get("remote_match")

    return JobListing(
        title=str(hit.get("title") or hit.get("vacancy_url") or ""),
        url=str(hit.get("vacancy_url") or ""),
        company=str(hit.get("company") or ""),
        country=countries[0] if countries else None,
        remote=remote_match is True,
        location=", ".join(countries) or None,
        description=matched_text or None,
        skills=stack,
        source=source,
        raw={
            "careers_url": hit.get("careers_url"),
            "matched_text": matched_text,
            "score": hit.get("score"),
            "countries": countries,
            "stack": stack,
            "job_types": job_types,
            "remote_match": remote_match,
            "method": method,
        },
    )
