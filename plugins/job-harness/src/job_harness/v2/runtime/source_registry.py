"""Runtime assembly for supported v2 source implementations."""

from __future__ import annotations

from collections.abc import Callable

from job_harness.v2.contracts import SourceScraper
from job_harness.v2.runtime.catalog import SourceCatalog, SupportedSource
from job_harness.v2.runtime.sources import (
    FinderWorkSource,
    GeekJobSource,
    GetmatchSource,
    HabrCareerSource,
    HhRuSource,
    HirifySource,
    ItJobsUzSource,
    JetBrainsCareerSource,
    TalantoSource,
    TalentoSource,
    VKCareerSource,
)
from job_harness.v2.source_catalog import source_catalog_entries, source_fixture_suite

_SOURCE_FACTORIES: dict[str, Callable[[], SourceScraper]] = {
    "habr_career": HabrCareerSource,
    "hh_ru": HhRuSource,
    "career:vk": VKCareerSource,
    "career:jetbrains": JetBrainsCareerSource,
    "talanto": TalantoSource,
    "geekjob": GeekJobSource,
    "talento": TalentoSource,
    "finder_work": FinderWorkSource,
    "getmatch": GetmatchSource,
    "it_jobs_uz": ItJobsUzSource,
    "hirify": HirifySource,
}


def build_supported_source_catalog(source_ids: tuple[str, ...] = ()) -> SourceCatalog:
    ordered_ids = _selected_source_ids(source_ids)
    supported_sources = tuple(
        SupportedSource(
            scraper=_SOURCE_FACTORIES[source_id](),
            fixture_suite=source_fixture_suite(source_id),
        )
        for source_id in ordered_ids
    )
    return SourceCatalog(supported_sources)


def implemented_source_ids() -> tuple[str, ...]:
    catalog_ids = tuple(entry.source_id for entry in source_catalog_entries())
    return tuple(source_id for source_id in catalog_ids if source_id in _SOURCE_FACTORIES)


def _selected_source_ids(source_ids: tuple[str, ...]) -> tuple[str, ...]:
    available = implemented_source_ids()
    if not source_ids:
        return available

    unknown = tuple(source_id for source_id in source_ids if source_id not in available)
    if unknown:
        raise ValueError(f"unknown or unimplemented v2 sources: {', '.join(unknown)}")
    return tuple(source_id for source_id in available if source_id in source_ids)
