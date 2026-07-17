"""Runtime assembly for supported v2 source implementations."""

from __future__ import annotations

from collections.abc import Callable

from job_harness.v2.contracts import DetailEnrichmentScraper, ParserRegistry, SourceScraper
from job_harness.v2.runtime.catalog import SourceCatalog, SupportedSource
from job_harness.v2.runtime.source_bundles import (
    detail_bundle,
    discovered_ats_search_bundle,
    generic_company_site_bundle,
    hh_company_profile_bundle,
    search_bundle,
)
from job_harness.v2.runtime.sources import (
    AmoCRMCareerSource,
    FinderWorkSource,
    GeekJobSource,
    GetmatchSource,
    HabrCareerSource,
    HhRuSource,
    HireHiSource,
    HirifySource,
    IBSCareerSource,
    ItJobsUzSource,
    JobTurboSource,
    StaffAmSource,
    TalantoSource,
    TalentoSource,
    VKCareerSource,
)
from job_harness.v2.runtime.sources.aggregators.hh_ru import hh_employer_profile_locations
from job_harness.v2.runtime.sources.companies.ats import (
    ATS_COMPANY_SOURCE_CONFIGS,
    ats_company_source,
)
from job_harness.v2.source_catalog import (
    source_catalog_entries,
    source_catalog_entry,
    source_fixture_suite,
)


def _ats_source_factory(source_id: str) -> Callable[[], SourceScraper]:
    def factory() -> SourceScraper:
        return ats_company_source(source_id)

    return factory


_SOURCE_FACTORIES: dict[str, Callable[[], SourceScraper]] = {
    "habr_career": HabrCareerSource,
    "hh_ru": HhRuSource,
    "career:vk": VKCareerSource,
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
    **{source_id: _ats_source_factory(source_id) for source_id in ATS_COMPANY_SOURCE_CONFIGS},
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


def build_independent_parser_registry(source_ids: tuple[str, ...] = ()) -> ParserRegistry:
    bundles: list[object] = []
    selected_ids = _selected_source_ids(source_ids)
    for source_id in selected_ids:
        source = _SOURCE_FACTORIES[source_id]()
        bundles.append(search_bundle(source, source_catalog_entry(source_id).listing_parser_ref))
        if isinstance(source, DetailEnrichmentScraper) and source.required_fixture_kinds.detail:
            bundles.append(detail_bundle(source))
    if "hh_ru" in selected_ids:
        bundles.append(hh_company_profile_bundle(hh_employer_profile_locations))
    bundles.append(discovered_ats_search_bundle())
    bundles.append(generic_company_site_bundle())
    return ParserRegistry(bundles)


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
