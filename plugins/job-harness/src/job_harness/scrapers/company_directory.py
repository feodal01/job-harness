"""Employer-first source backed by the bundled company directory."""

from __future__ import annotations

from job_harness.base import BaseScraper
from job_harness.company_directory import CompanyProfile, search_company_directory
from job_harness.models import JobListing, SearchParams
from job_harness.registry import register_scraper


@register_scraper("company_directory")
class CompanyDirectoryScraper(BaseScraper):
    display_name = "Company Directory"
    requires_browser = False
    detail_requires_browser = False

    def search(self, params: SearchParams) -> list[JobListing]:
        profiles = search_company_directory(
            params.query,
            country=params.location or params.country,
            remote_only=params.remote_only,
            max_results=self.max_results,
        )
        return [self._listing(profile, params.query) for profile in profiles]

    def fetch_detail(self, listing: JobListing) -> JobListing:
        return listing

    @staticmethod
    def _listing(profile: CompanyProfile, query: str) -> JobListing:
        url = profile.best_jobs_url
        if not url:
            raise ValueError(f"Company profile has no searchable URL: {profile.name}")
        country = profile.countries[0] if profile.countries else None
        return JobListing(
            title=f"{profile.name}: employer career entrypoint for {query}",
            url=url,
            company=profile.name,
            country=country,
            remote=profile.remote,
            location=", ".join(profile.countries) or None,
            description=profile.description,
            skills=list(profile.stack),
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
