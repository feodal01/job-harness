"""Supported source catalog for the contract-first runtime."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import (
    ParserFixtureSuite,
    SearchRequest,
    SourceScraper,
    SupportedSourceContract,
)


@dataclass(frozen=True)
class SupportedSource:
    """A scraper that is allowed to run because its fixture contract is complete."""

    scraper: SourceScraper
    fixture_suite: ParserFixtureSuite

    def __post_init__(self) -> None:
        SupportedSourceContract(
            descriptor=self.scraper.descriptor,
            required_fixture_kinds=self.scraper.required_fixture_kinds,
            fixture_suite=self.fixture_suite,
        )


class SourceCatalog:
    """Strict registry of sources that satisfy the support contract."""

    def __init__(self, sources: tuple[SupportedSource, ...]) -> None:
        if not sources:
            raise ValueError("SourceCatalog requires at least one supported source")

        by_id: dict[str, SourceScraper] = {}
        for source in sources:
            source_id = source.scraper.descriptor.source_id
            if source_id in by_id:
                raise ValueError(f"duplicate source_id: {source_id}")
            by_id[source_id] = source.scraper

        self._ordered_ids = tuple(by_id)
        self._by_id = by_id

    @property
    def source_ids(self) -> tuple[str, ...]:
        return self._ordered_ids

    def get(self, source_id: str) -> SourceScraper:
        try:
            return self._by_id[source_id]
        except KeyError as exc:
            raise ValueError(f"unknown source: {source_id}") from exc

    def select(self, request: SearchRequest) -> tuple[SourceScraper, ...]:
        selected_ids = self._selected_ids(request)
        selected = [self._by_id[source_id] for source_id in selected_ids]

        if request.source_types:
            allowed_types = frozenset(request.source_types)
            selected = [
                scraper
                for scraper in selected
                if scraper.descriptor.source_type in allowed_types
            ]

        if request.countries:
            wanted_countries = frozenset(request.countries)
            selected = [
                scraper
                for scraper in selected
                if not scraper.descriptor.countries
                or bool(wanted_countries & frozenset(scraper.descriptor.countries))
            ]

        if request.exclude_companies:
            excluded = tuple(item.casefold() for item in request.exclude_companies)
            selected = [
                scraper
                for scraper in selected
                if not any(item in scraper.descriptor.source_id.casefold() for item in excluded)
            ]

        return tuple(selected)

    def _selected_ids(self, request: SearchRequest) -> tuple[str, ...]:
        if not request.sources:
            return self._ordered_ids

        unknown = tuple(source_id for source_id in request.sources if source_id not in self._by_id)
        if unknown:
            raise ValueError(f"unknown sources: {', '.join(unknown)}")
        return tuple(source_id for source_id in self._ordered_ids if source_id in request.sources)
