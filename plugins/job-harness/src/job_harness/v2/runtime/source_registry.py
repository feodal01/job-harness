"""Runtime assembly for supported v2 source implementations."""

from __future__ import annotations

from collections.abc import Callable

from job_harness.v2.contracts import SourceScraper
from job_harness.v2.runtime.catalog import SourceCatalog, SupportedSource
from job_harness.v2.runtime.sources import (
    AirSlateCareerSource,
    AmoCRMCareerSource,
    AppFollowCareerSource,
    ChainstackCareerSource,
    CoinsPaidCareerSource,
    FinderWorkSource,
    GeekJobSource,
    GetmatchSource,
    HabrCareerSource,
    HhRuSource,
    HireHiSource,
    HirifySource,
    IBSCareerSource,
    ItJobsUzSource,
    JetBrainsCareerSource,
    JobTurboSource,
    OutschoolCareerSource,
    StaffAmSource,
    TalantoSource,
    TalentoSource,
    TermiusCareerSource,
    ThreeCommasCareerSource,
    TruvCareerSource,
    VKCareerSource,
    WallarmCareerSource,
    WintermuteCareerSource,
    ZeroAviaCareerSource,
)
from job_harness.v2.source_catalog import source_catalog_entries, source_fixture_suite

_SOURCE_FACTORIES: dict[str, Callable[[], SourceScraper]] = {
    "habr_career": HabrCareerSource,
    "hh_ru": HhRuSource,
    "career:vk": VKCareerSource,
    "career:jetbrains": JetBrainsCareerSource,
    "career:ibs": IBSCareerSource,
    "talanto": TalantoSource,
    "geekjob": GeekJobSource,
    "talento": TalentoSource,
    "finder_work": FinderWorkSource,
    "getmatch": GetmatchSource,
    "it_jobs_uz": ItJobsUzSource,
    "hirify": HirifySource,
    "jobturbo": JobTurboSource,
    "hirehi": HireHiSource,
    "staff_am": StaffAmSource,
    "career:amocrm": AmoCRMCareerSource,
    "career:appfollow": AppFollowCareerSource,
    "career:coinspaid": CoinsPaidCareerSource,
    "career:airslate": AirSlateCareerSource,
    "career:wintermute": WintermuteCareerSource,
    "career:truv": TruvCareerSource,
    "career:termius": TermiusCareerSource,
    "career:outschool": OutschoolCareerSource,
    "career:zeroavia": ZeroAviaCareerSource,
    "career:wallarm": WallarmCareerSource,
    "career:chainstack": ChainstackCareerSource,
    "career:3commas": ThreeCommasCareerSource,
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
    catalog_ids = _catalog_source_ids()
    _raise_for_source_factory_catalog_mismatch(catalog_ids)
    return catalog_ids


def _selected_source_ids(source_ids: tuple[str, ...]) -> tuple[str, ...]:
    available = implemented_source_ids()
    if not source_ids:
        return available

    unknown = tuple(source_id for source_id in source_ids if source_id not in available)
    if unknown:
        raise ValueError(f"unknown or unimplemented v2 sources: {', '.join(unknown)}")
    return tuple(source_id for source_id in available if source_id in source_ids)


def _catalog_source_ids() -> tuple[str, ...]:
    return tuple(entry.source_id for entry in source_catalog_entries())


def _raise_for_source_factory_catalog_mismatch(source_ids: tuple[str, ...]) -> None:
    missing = tuple(source_id for source_id in source_ids if source_id not in _SOURCE_FACTORIES)
    if missing:
        raise ValueError(f"catalog sources missing runtime implementation: {', '.join(missing)}")
    catalog_id_set = set(source_ids)
    extra = tuple(source_id for source_id in _SOURCE_FACTORIES if source_id not in catalog_id_set)
    if extra:
        raise ValueError(f"runtime implementations missing catalog rows: {', '.join(extra)}")
