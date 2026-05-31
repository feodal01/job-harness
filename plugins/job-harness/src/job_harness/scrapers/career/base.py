"""Abstract base for per-company career site scrapers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from job_harness.models import JobListing, SearchParams


class BaseCareerScraper(ABC):
    """Scrapes vacancies from a specific company's career site.

    Each subclass knows how to navigate ONE company's career page.
    The scraper_name must match the key in employer_cache.
    """

    scraper_name: str = ""
    company: str = ""
    careers_url: str = ""

    def __init__(self, context):
        self.context = context

    @abstractmethod
    def search(self, params: SearchParams) -> list[JobListing]:
        """Search this company's career site for matching vacancies."""

    def _make_listing(self, **kwargs) -> JobListing:
        kwargs.setdefault("company", self.company)
        kwargs.setdefault("source", f"career:{self.scraper_name}")
        return JobListing(**kwargs)


# Registry for career scrapers

_CAREER_SCRAPERS: dict[str, type[BaseCareerScraper]] = {}


def register_career_scraper(name: str):
    def decorator(cls: type[BaseCareerScraper]) -> type[BaseCareerScraper]:
        _CAREER_SCRAPERS[name] = cls
        cls.scraper_name = name
        return cls
    return decorator


def get_career_scraper(name: str, context) -> BaseCareerScraper | None:
    cls = _CAREER_SCRAPERS.get(name)
    if cls:
        return cls(context)
    return None


def list_career_scrapers() -> dict[str, str]:
    return {name: f"{cls.company} ({cls.careers_url})" for name, cls in _CAREER_SCRAPERS.items()}
