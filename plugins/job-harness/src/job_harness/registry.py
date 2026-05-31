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


def list_scrapers() -> list[str]:
    return list(_SCRAPERS.keys())


def get_scraper_info() -> dict[str, str]:
    return {name: cls.display_name for name, cls in _SCRAPERS.items()}


def create_scraper(name: str, context, **kwargs) -> BaseScraper:
    cls = get_scraper_class(name)
    return cls(context, **kwargs)
