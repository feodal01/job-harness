"""Employer-first source backed by the bundled company directory."""

from __future__ import annotations

from typing import ClassVar

from job_harness.v1.base import BaseScraper
from job_harness.v1.company_directory import CompanyProfile, search_company_directory
from job_harness.v1.models import RawListing, SearchParams
from job_harness.v1.registry import register_scraper
from job_harness.v1.types import FilterSupport, ScraperCapabilities, SearchCriterion, SourceGroup


@register_scraper("company_directory")
class CompanyDirectoryScraper(BaseScraper):
    display_name = "Company Directory"
    requires_browser = False
    detail_requires_browser = False
    source_group = SourceGroup.DIRECTORY
    source_limit = 200
    server_criteria = frozenset(
        {SearchCriterion.QUERY, SearchCriterion.COUNTRY, SearchCriterion.REMOTE_ONLY}
    )

    # The bundled directory tells us per-company facts (remote, country)
    # but nothing per-vacancy. experience/has_salary cannot be enforced
    # at this layer.
    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.CLIENT,
        "country": FilterSupport.CLIENT,
        "experience": FilterSupport.UNSUPPORTED,
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.UNSUPPORTED,
        "query_match": FilterSupport.CLIENT,
    }

    def search(self, params: SearchParams) -> list[RawListing]:
        profiles = search_company_directory(
            params.query,
            country=params.location or params.country,
            remote_only=params.remote_only,
            max_results=self.max_results,
        )
        return [self._listing(profile, params.query) for profile in profiles]

    def fetch_detail(self, listing: RawListing) -> RawListing:
        return listing

    @staticmethod
    def _listing(profile: CompanyProfile, query: str) -> RawListing:
        url = profile.best_jobs_url
        if not url:
            raise ValueError(f"Company profile has no searchable URL: {profile.name}")
        country = profile.countries[0] if profile.countries else None
        return RawListing(
            title=f"{profile.name}: employer career entrypoint for {query}",
            url=url,
            company=profile.name,
            country=country,
            remote=profile.remote,
            location=", ".join(profile.countries) or None,
            description=profile.description,
            skills=tuple(profile.stack),
            source="company_directory",
            raw={
                "careers_url": profile.careers_url,
                "linkedin_url": profile.linkedin_url,
                "linkedin_jobs_url": profile.linkedin_jobs_url,
                "industry": profile.industry,
                "headcount": profile.headcount,
                "job_types": list(profile.job_types),
                "countries": list(profile.countries),
                "sources": list(profile.sources),
            },
        )
