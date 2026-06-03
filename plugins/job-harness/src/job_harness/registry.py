"""Scraper registry — discover and instantiate scrapers by name."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_harness.base import BaseScraper

_SCRAPERS: dict[str, type[BaseScraper]] = {}


def register_scraper(name: str):
    """Decorator to register a scraper class."""
    def decorator(cls: type[BaseScraper]) -> type[BaseScraper]:
        _SCRAPERS[name] = cls
        cls.name = name
        return cls
    return decorator


def get_scraper_class(name: str) -> type[BaseScraper]:
    if name not in _SCRAPERS:
        raise ValueError(f"Unknown scraper: {name}. Available: {list(_SCRAPERS.keys())}")
    return _SCRAPERS[name]


def list_scrapers(country: str | None = None) -> list[str]:
    return [name for name, cls in _SCRAPERS.items() if cls.supports_country(country)]


def get_scraper_info() -> dict[str, str]:
    return {name: cls.display_name for name, cls in _SCRAPERS.items()}


def get_scraper_metadata() -> dict[str, dict]:
    return {
        name: {
            "display_name": cls.display_name,
            "countries": list(cls.countries),
            "requires_browser": cls.requires_browser,
            "detail_requires_browser": cls.detail_requires_browser,
        }
        for name, cls in _SCRAPERS.items()
    }


def create_scraper(name: str, context, **kwargs) -> BaseScraper:
    cls = get_scraper_class(name)
    return cls(context, **kwargs)
