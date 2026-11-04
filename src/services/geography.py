from typing import Any

from src.geography.catalog import DEFAULT_LOCALE, GeographyCatalog


class GeographyService:
    """HTTP API view of the application's single geography catalog."""

    def __init__(self, catalog: GeographyCatalog) -> None:
        self.catalog = catalog

    def countries(self, locale: str = DEFAULT_LOCALE) -> list[dict[str, str]]:
        return [{"code": code, "name": name} for code, name in self.catalog.country_names(locale).items()]

    def cities(self, country: str, text: str, locale: str = DEFAULT_LOCALE) -> list[dict[str, Any]]:
        return self.catalog.search(country, text, locale)
