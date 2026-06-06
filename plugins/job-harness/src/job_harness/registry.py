"""Scraper registry — discover and instantiate scrapers by name."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from job_harness.types import CAPABILITY_FLAGS, FilterSupport, ScraperCapabilities

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
    def _caps(cls) -> dict[str, str]:
        # TypedDict.items() loses precise value types for mypy; do the
        # lookup keyed by the known CAPABILITY_FLAGS instead.
        return {
            flag: cast(FilterSupport, cls.capabilities.get(flag, FilterSupport.UNSUPPORTED)).value
            for flag in CAPABILITY_FLAGS
        }

    return {
        name: {
            "display_name": cls.display_name,
            "countries": list(cls.countries),
            "requires_browser": cls.requires_browser,
            "detail_requires_browser": cls.detail_requires_browser,
            "transport": cls.transport().value,
            "capabilities": _caps(cls),
        }
        for name, cls in _SCRAPERS.items()
    }


def get_scraper_capabilities(name: str) -> ScraperCapabilities:
    return get_scraper_class(name).capabilities


def iter_registered() -> list[tuple[str, type[BaseScraper]]]:
    """Snapshot of the registry. Stable iteration order matches insertion."""
    return list(_SCRAPERS.items())


def create_scraper(name: str, context, **kwargs) -> BaseScraper:
    cls = get_scraper_class(name)
    return cls(context, **kwargs)
