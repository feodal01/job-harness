from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanyProfileOutput,
    CompanyProfileResult,
    CompanyRef,
    CompanySiteInput,
    CompanySiteOutput,
    CompanySiteResult,
    CompensationCriterion,
    CompensationPeriod,
    DiscoveredEndpoint,
    Grade,
    InvocationScope,
    ParserManifest,
    ParserRef,
    ParserRegistry,
    ParserType,
    RemoteScope,
    SalaryRange,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchRequest,
    SearchResultOutcome,
    SearchScenario,
    SingletonResultOutcome,
    SourceLocation,
    SourceType,
    TransportKind,
    VacancyDetailInput,
    VacancyDetailOutput,
    VacancyDetailResult,
    WorkFormat,
)
from job_harness.v2.persistence.graph_repository import read_graph_processed_payload
from job_harness.v2.ports import (
    HttpAction,
    HttpResponse,
    OperationContext,
    ParserAttemptMetrics,
    ParserRuntime,
    ParserRuntimeFactory,
    RetrySafety,
)
from job_harness.v2.runtime.atomic_artifacts import atomic_write_bytes
from job_harness.v2.runtime.fact_derivers import derive_selection_facts
from job_harness.v2.runtime.graph_execution import (
    merge_enriched_items,
    merge_workflow_items,
)
from job_harness.v2.runtime.graph_pipeline import (
    GraphSearchPipeline,
    GraphSearchPipelineConfig,
)
from job_harness.v2.runtime.graph_scheduler import GraphSearchProgress
from job_harness.v2.runtime.public_projection import public_vacancy_projection
from job_harness.v2.runtime.request_retry import (
    RequestAttemptError,
    RequestFailureKind,
    RequestRetryPolicy,
)
from job_harness.v2.source_catalog import ListingParserBinding

_LISTING_AND_DETAIL_TASK_COUNT = 2
_SPECULATIVE_BATCH_SIZE = 25
_SPECULATIVE_SOURCE_BATCH_SIZE = 10
_ARTIFACT_CRASH_WRITE_NUMBER = 4


class _Runtime(ParserRuntime):
    @property
    def reserved_collection_units(self) -> int:
        return 1

    @property
    def attempt_metrics(self) -> ParserAttemptMetrics:
        return ParserAttemptMetrics()

    async def http(self, _action: HttpAction) -> HttpResponse:
        raise AssertionError("fake bundle does not use network")


class _RuntimeFactory:
    def create(self, context: OperationContext, *, reserved_collection_units: int) -> ParserRuntime:
        del context, reserved_collection_units
        return _Runtime()


def _pipeline(
    *,
    config: GraphSearchPipelineConfig,
    registry: ParserRegistry,
    runtime_factory: ParserRuntimeFactory,
    source_bindings: tuple[ListingParserBinding, ...] | None = None,
    clock: Callable[[], float] = time.time,
    artifact_writer: Callable[[Path, bytes], None] = atomic_write_bytes,
) -> GraphSearchPipeline:
    bindings = source_bindings or tuple(
        ListingParserBinding(
            source_id=manifest.provider_ids[0],
            source_type=SourceType(manifest.source_kinds[0]),
            parser_ref=manifest.ref,
        )
        for manifest in registry.manifests()
        if manifest.parser_type == ParserType.SEARCH_LISTING
    )
    return GraphSearchPipeline(
        config=config,
        registry=registry,
        runtime_factory=runtime_factory,
        source_bindings=bindings,
        clock=clock,
        artifact_writer=artifact_writer,
    )


class _SearchBundle:
    manifest = ParserManifest(
        parser_id="test.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id="test.search.input.v1",
        output_schema_id="search-listing-output.v2",
        transport=TransportKind.HTTP,
        provider_ids=("test",),
        supported_url_patterns=(),
        output_facts=("title", "vacancyUrl"),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=("aggregator",),
        query_mode="per_query",
        collection_unit="page",
        native_criteria=("query",),
        default_unit_budget=2,
        default_item_budget=20,
        default_invocation_budget=3,
    )
    input_type = SearchListingInput
    result_type = SearchListingResult

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            SearchListingInput(
                source_id="test",
                target_provider_id="test",
                queries=intent.query_variants,
                target=target,
                cursor={"page": 0},
                native_filters={},
                resolved_state=None,
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        del parser_input, runtime
        return SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=(
                SearchListingOutput(
                    source_id="test",
                    target_provider_id="test",
                    source_listing_id="1",
                    title="QA Engineer",
                    company=CompanyRef(name="Example"),
                    location=SourceLocation("Remote"),
                    salary=None,
                    work_formats=("remote",),
                    remote_scopes=(),
                    native_grade=None,
                    posted_at=None,
                    vacancy_url="https://example.com/jobs/1",
                    apply_url=None,
                    summary=None,
                ),
            ),
            continuations=(),
            collection_units_consumed=1,
        )


class _CountingSearchBundle(_SearchBundle):
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        self.calls += 1
        return await super().execute(parser_input, runtime)


class _CanonicalSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        output_facts=(
            "title",
            "location",
            "salary",
            "work_formats",
            "remote_scopes",
            "native_grade",
            "summary",
            "vacancy_url",
        ),
    )

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        del parser_input, runtime
        return SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=(
                SearchListingOutput(
                    source_id="test",
                    target_provider_id="test",
                    source_listing_id="canonical-1",
                    title="Middle Data Analyst",
                    company=CompanyRef(name="Example"),
                    location=SourceLocation(
                        text="London | Vilnius",
                        cities=("London", "Vilnius"),
                        countries=("GB", "LT"),
                        regions=("EU",),
                    ),
                    salary=SalaryRange(
                        salary_from=300_000,
                        salary_to=400_000,
                        currency="RUR",
                        gross=True,
                        period="month",
                    ),
                    work_formats=("hybrid", "remote"),
                    remote_scopes=(RemoteScope("country", "DE"),),
                    native_grade="senior",
                    posted_at=None,
                    vacancy_url="https://example.com/jobs/canonical-1",
                    apply_url=None,
                    summary="Relocation assistance is available.",
                ),
            ),
            continuations=(),
            collection_units_consumed=1,
        )


class _FailingDetailBundle:
    manifest = ParserManifest(
        parser_id="test.detail",
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id="test.detail.input.v1",
        output_schema_id="vacancy-detail-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("test",),
        supported_url_patterns=(r"https://example\.com/jobs/.*",),
        output_facts=("description",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )
    input_type = VacancyDetailInput
    result_type = VacancyDetailResult

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        del parser_input, runtime
        raise ValueError("detail unavailable")


class _FailingSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="failed.search",
        provider_ids=("failed",),
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            replace(
                super().plan_initial(intent, target)[0],
                source_id="failed",
                target_provider_id="failed",
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        del parser_input, runtime
        raise ValueError("listing parser failed")


class _ArbitraryNamedSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="listing-engine-v9",
        provider_ids=("catalog-source",),
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            replace(
                super().plan_initial(intent, target)[0],
                source_id="catalog-source",
                target_provider_id="catalog-source",
            ),
        )

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            items=(
                replace(
                    result.items[0],
                    source_id="catalog-source",
                    target_provider_id="catalog-source",
                ),
            ),
        )


class _FlakyContinuationSearchBundle(_SearchBundle):
    def __init__(self) -> None:
        self.calls = {0: 0, 1: 0}

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        page = int(parser_input.cursor["page"])
        self.calls[page] += 1
        if page == 1 and self.calls[page] == 1:
            raise RequestAttemptError(
                failure_kind=RequestFailureKind.TIMEOUT,
                retry_safety=RetrySafety.SAFE,
                message="temporary page 2 timeout",
            )
        if page == 1:
            return SearchListingResult(
                outcome=SearchResultOutcome.NO_RESULTS,
                items=(),
                continuations=(),
                collection_units_consumed=1,
            )
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            continuations=(replace(parser_input, cursor={"page": 1}),),
        )


class _EmployerSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="employer.search",
        provider_ids=("employer",),
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            replace(
                super().plan_initial(intent, target)[0],
                source_id="employer",
                target_provider_id="employer",
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            items=(
                replace(
                    result.items[0],
                    source_id="employer",
                    target_provider_id="employer",
                    company=CompanyRef(
                        name="Example",
                        target_provider_id="employer",
                        source_company_id="10",
                        profile_url="https://jobs.example.com/employer/10",
                    ),
                ),
            ),
        )


class _EmployerSearchWithSiteBundle(_EmployerSearchBundle):
    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        result = await super().execute(parser_input, runtime)
        company = result.items[0].company
        assert company is not None
        return replace(
            result,
            items=(
                replace(
                    result.items[0],
                    company=replace(company, official_site_url="https://example.com"),
                ),
            ),
        )


class _EmployerProfileBundle:
    manifest = ParserManifest(
        parser_id="employer.company-profile",
        parser_type=ParserType.COMPANY_PROFILE,
        implementation_version="1.0",
        input_schema_id="employer.company-profile.input.v1",
        output_schema_id="company-profile-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("employer",),
        supported_url_patterns=(r"https://jobs\.example\.com/employer/.*",),
        output_facts=("officialSiteUrl",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )
    input_type = CompanyProfileInput
    result_type = CompanyProfileResult

    async def execute(
        self,
        parser_input: CompanyProfileInput,
        runtime: ParserRuntime,
    ) -> CompanyProfileResult:
        del runtime
        return CompanyProfileResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=CompanyProfileOutput(
                target_provider_id=parser_input.target_provider_id,
                profile_url=parser_input.company_profile_url,
                source_company_id=parser_input.source_company_id,
                company_name="Example",
                description=None,
                industry=None,
                size_text=None,
                locations=(SourceLocation("Russia"),),
                official_site_url="https://example.com",
                career_endpoints=(),
                contacts=(),
                social_links=(),
            ),
        )


class _EmployerSiteBundle:
    manifest = ParserManifest(
        parser_id="web.company-site",
        parser_type=ParserType.COMPANY_SITE,
        implementation_version="1.0",
        input_schema_id="web.company-site.input.v1",
        output_schema_id="company-site-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("web",),
        supported_url_patterns=(r"https://.*",),
        output_facts=("careerEndpoints",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        is_fallback=True,
    )
    input_type = CompanySiteInput
    result_type = CompanySiteResult

    async def execute(self, parser_input: CompanySiteInput, runtime: ParserRuntime) -> CompanySiteResult:
        del runtime
        return CompanySiteResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=CompanySiteOutput(
                canonical_site_url=parser_input.site_url,
                company_name="Example",
                contacts=(),
                social_links=(),
                career_endpoints=(
                    DiscoveredEndpoint(
                        kind="career_page",
                        url="https://example.com/careers",
                        provider_hint=None,
                        confidence="confirmed",
                        discovery_method="explicit_link",
                    ),
                ),
            ),
        )


class _DiscoveredCareerSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="career.discovered.search",
        provider_ids=("career",),
        supported_url_patterns=(r"https://example\.com/careers",),
        source_kinds=("company_career",),
        query_mode="downstream_only",
        default_unit_budget=1,
        default_item_budget=10,
        default_invocation_budget=1,
    )

    def plan_initial(
        self,
        intent: SearchRequest,
        target: dict[str, object],
    ) -> tuple[SearchListingInput, ...]:
        return (
            SearchListingInput(
                source_id="career",
                target_provider_id="career",
                queries=intent.query_variants,
                target=target,
                cursor={"page": 0},
                native_filters={},
                resolved_state=None,
            ),
        )

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        del parser_input, runtime
        return SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=(
                SearchListingOutput(
                    source_id="career",
                    target_provider_id="career",
                    source_listing_id="career-1",
                    title="QA Lead",
                    company=CompanyRef(name="Example"),
                    location=SourceLocation("Remote"),
                    salary=None,
                    work_formats=("remote",),
                    remote_scopes=(),
                    native_grade="lead",
                    posted_at=None,
                    vacancy_url="https://example.com/careers/jobs/career-1",
                    apply_url=None,
                    summary=None,
                ),
            ),
            continuations=(),
            collection_units_consumed=1,
        )


class _RelocationDetailBundle(_FailingDetailBundle):
    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        del runtime
        return VacancyDetailResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=VacancyDetailOutput(
                target_provider_id=parser_input.target_provider_id,
                source_listing_id=parser_input.source_listing_id,
                canonical_vacancy_url=parser_input.vacancy_url,
                title="QA Engineer",
                company=None,
                description="Relocation assistance is available for this role.",
                requirements=(),
                responsibilities=(),
                conditions=(),
                skills=(),
                employment_types=(),
                salary=None,
                work_formats=("remote",),
                remote_scopes=(),
                application_channels=(),
            ),
        )


class _MissingFactDetailBundle(_FailingDetailBundle):
    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        del runtime
        return VacancyDetailResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=VacancyDetailOutput(
                target_provider_id=parser_input.target_provider_id,
                source_listing_id=parser_input.source_listing_id,
                canonical_vacancy_url=parser_input.vacancy_url,
                title="QA Engineer",
                company=None,
                description=None,
                requirements=(),
                responsibilities=(),
                conditions=(),
                skills=(),
                employment_types=(),
                salary=None,
                work_formats=("remote",),
                remote_scopes=(),
                application_channels=(),
            ),
        )


class _UnknownGradeDetailBundle(_FailingDetailBundle):
    manifest = replace(
        _FailingDetailBundle.manifest,
        parser_id="grade-resolver-v7",
        output_facts=("native_grade",),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        parser_input: VacancyDetailInput,
        runtime: ParserRuntime,
    ) -> VacancyDetailResult:
        del runtime
        self.calls += 1
        return VacancyDetailResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=VacancyDetailOutput(
                target_provider_id=parser_input.target_provider_id,
                source_listing_id=parser_input.source_listing_id,
                canonical_vacancy_url=parser_input.vacancy_url,
                title="QA Engineer",
                company=None,
                description=None,
                requirements=(),
                responsibilities=(),
                conditions=(),
                skills=(),
                employment_types=(),
                salary=None,
                work_formats=(),
                remote_scopes=(),
                application_channels=(),
                native_grade=None,
            ),
        )


class _RankedBatchSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        default_unit_budget=1,
        default_item_budget=100,
        default_invocation_budget=1,
    )

    def build_action(self, _parser_input: SearchListingInput) -> HttpAction:
        return HttpAction(
            method="GET",
            url="https://example.com/jobs",
            retry_safety=RetrySafety.SAFE,
        )

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        del parser_input, runtime
        items = tuple(
            SearchListingOutput(
                source_id="test",
                target_provider_id="test",
                source_listing_id=f"ranked-{index:02d}",
                title=(
                    "QA one two three Engineer"
                    if index < _SPECULATIVE_BATCH_SIZE
                    else "QA Engineer"
                ),
                company=CompanyRef(name="Example"),
                location=SourceLocation("Remote"),
                salary=None,
                work_formats=("remote",),
                remote_scopes=(),
                native_grade=None,
                posted_at=None,
                vacancy_url=f"https://example.com/jobs/ranked-{index:02d}",
                apply_url=None,
                summary=None,
            )
            for index in range(60)
        )
        return SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=items,
            continuations=(),
            collection_units_consumed=1,
        )


class _RankedSourceBatchSearchBundle(_RankedBatchSearchBundle):
    def __init__(self, source_id: str) -> None:
        self._source_id = source_id
        self.manifest = replace(
            _RankedBatchSearchBundle.manifest,
            parser_id=f"{source_id}.search",
            provider_ids=(source_id,),
            default_item_budget=12,
        )

    def plan_initial(
        self,
        intent: SearchRequest,
        target: dict[str, object],
    ) -> tuple[SearchListingInput, ...]:
        return (
            SearchListingInput(
                source_id=self._source_id,
                target_provider_id=self._source_id,
                queries=intent.query_variants,
                target=target,
                cursor={"page": 0},
                native_filters={},
                resolved_state=None,
            ),
        )

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            items=tuple(
                replace(
                    item,
                    source_id=self._source_id,
                    target_provider_id=self._source_id,
                    source_listing_id=f"{self._source_id}-{item.source_listing_id}",
                    vacancy_url=(
                        f"https://example.com/jobs/{self._source_id}-{item.source_listing_id}"
                    ),
                )
                for item in result.items[:12]
            ),
        )


class _CountingOptionalDetailBundle(_FailingDetailBundle):
    def __init__(self) -> None:
        self.source_listing_ids: list[str] = []

    def build_action(self, parser_input: VacancyDetailInput) -> HttpAction:
        return HttpAction(
            method="GET",
            url=parser_input.vacancy_url,
            retry_safety=RetrySafety.SAFE,
        )

    async def execute(
        self,
        parser_input: VacancyDetailInput,
        runtime: ParserRuntime,
    ) -> VacancyDetailResult:
        del runtime
        source_listing_id = parser_input.source_listing_id
        if source_listing_id is None:
            raise AssertionError("speculative detail input must retain source listing id")
        self.source_listing_ids.append(source_listing_id)
        return VacancyDetailResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=VacancyDetailOutput(
                target_provider_id=parser_input.target_provider_id,
                source_listing_id=parser_input.source_listing_id,
                canonical_vacancy_url=parser_input.vacancy_url,
                title=None,
                company=None,
                description=f"Description for {parser_input.source_listing_id}",
                requirements=(),
                responsibilities=(),
                conditions=(),
                skills=(),
                employment_types=(),
                salary=None,
                work_formats=(),
                remote_scopes=(),
                application_channels=(),
            ),
        )


class _CountingSourceDetailBundle(_CountingOptionalDetailBundle):
    def __init__(self, source_id: str, source_listing_ids: list[str]) -> None:
        super().__init__()
        self.source_listing_ids = source_listing_ids
        self.manifest = replace(
            _CountingOptionalDetailBundle.manifest,
            parser_id=f"{source_id}.detail",
            provider_ids=(source_id,),
        )


class _DuplicateAfterDetailSearchBundle(_SearchBundle):
    def __init__(self, release_duplicate: asyncio.Event) -> None:
        self._release_duplicate = release_duplicate

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        first = super().plan_initial(intent, target)[0]
        return first, replace(first, cursor={"page": 1})

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        if parser_input.cursor == {"page": 1}:
            await self._release_duplicate.wait()
        return await super().execute(parser_input, runtime)


class _BlockingSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="blocking.search",
        provider_ids=("blocking",),
    )

    def __init__(self, release: asyncio.Event) -> None:
        self._release = release

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            replace(
                super().plan_initial(intent, target)[0],
                source_id="blocking",
                target_provider_id="blocking",
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        await self._release.wait()
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            items=(
                replace(
                    result.items[0],
                    source_id="blocking",
                    target_provider_id="blocking",
                    source_listing_id="blocking-1",
                    vacancy_url="https://blocking.example/jobs/1",
                ),
            ),
        )


class _UnblockingSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="unblocking.search",
        provider_ids=("unblocking",),
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            replace(
                super().plan_initial(intent, target)[0],
                source_id="unblocking",
                target_provider_id="unblocking",
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            items=(
                replace(
                    result.items[0],
                    source_id="unblocking",
                    target_provider_id="unblocking",
                    source_listing_id="unblocking-1",
                    vacancy_url="https://unblocking.example/jobs/1",
                ),
            ),
        )


class _UnblockingDetailBundle(_FailingDetailBundle):
    manifest = replace(
        _FailingDetailBundle.manifest,
        parser_id="unblocking.detail",
        provider_ids=("unblocking",),
        supported_url_patterns=(r"https://unblocking\.example/jobs/.*",),
    )

    def __init__(self, release: asyncio.Event) -> None:
        self._release = release

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        del runtime
        self._release.set()
        return VacancyDetailResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=VacancyDetailOutput(
                target_provider_id=parser_input.target_provider_id,
                source_listing_id=parser_input.source_listing_id,
                canonical_vacancy_url=parser_input.vacancy_url,
                title="QA Engineer",
                company=None,
                description="Detailed role description.",
                requirements=(),
                responsibilities=(),
                conditions=(),
                skills=(),
                employment_types=(),
                salary=None,
                work_formats=("remote",),
                remote_scopes=(),
                application_channels=(),
            ),
        )


class _CompanySearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="company.search",
        provider_ids=("company",),
        source_kinds=("company_career",),
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            replace(
                super().plan_initial(intent, target)[0],
                source_id="company",
                target_provider_id="company",
            ),
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        result = await super().execute(parser_input, runtime)
        return replace(
            result,
            items=(
                replace(
                    result.items[0],
                    source_id="company",
                    target_provider_id="company",
                    vacancy_url="https://company.example/jobs/1",
                ),
            ),
        )


class _PerQueryBudgetBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        parser_id="budget.search",
        provider_ids=("budget",),
        default_unit_budget=1,
        default_item_budget=2,
        default_invocation_budget=1,
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return tuple(
            SearchListingInput(
                source_id="budget",
                target_provider_id="budget",
                queries=(query,),
                target=target,
                cursor={"query": query},
                native_filters={},
                resolved_state=None,
            )
            for query in intent.query_variants
        )

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        del runtime
        query = parser_input.queries[0]
        slug = query.casefold().replace(" ", "-")
        return SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=tuple(
                SearchListingOutput(
                    source_id="budget",
                    target_provider_id="budget",
                    source_listing_id=f"{slug}-{index}",
                    title=f"{query} {index}",
                    company=CompanyRef(name="Example"),
                    location=None,
                    salary=None,
                    work_formats=(),
                    remote_scopes=(),
                    native_grade=None,
                    posted_at=None,
                    vacancy_url=f"https://example.com/jobs/{slug}-{index}",
                    apply_url=None,
                    summary=None,
                )
                for index in range(2)
            ),
            continuations=(),
            collection_units_consumed=1,
        )


class _DownstreamBudgetBundle(_PerQueryBudgetBundle):
    manifest = replace(
        _PerQueryBudgetBundle.manifest,
        query_mode="downstream_only",
        native_criteria=(),
    )

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            SearchListingInput(
                source_id="budget",
                target_provider_id="budget",
                queries=intent.query_variants,
                target=target,
                cursor={"page": 0},
                native_filters={},
                resolved_state=None,
            ),
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _DeadlineSearchBundle(_SearchBundle):
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        self._clock.now += 2.0
        return await super().execute(parser_input, runtime)


class GraphSearchPipelineTest(unittest.IsolatedAsyncioTestCase):
    def test_enrichment_merge_uses_source_listing_identity_when_url_changes(self) -> None:
        search_item = {
            "sourceId": "hh_ru",
            "sourceListingId": "123",
            "vacancyUrl": "https://hh.ru/vacancy/123?from=search",
            "title": "QA Engineer",
        }
        enrichment_item = {
            "sourceId": "hh_ru",
            "sourceListingId": "123",
            "vacancyUrl": "https://hh.ru/vacancy/123",
            "title": "QA Engineer",
            "description": "Full description",
        }

        merged = merge_enriched_items((search_item,), (enrichment_item,))

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["vacancyUrl"], "https://hh.ru/vacancy/123")
        self.assertEqual(merged[0]["description"], "Full description")

    def test_workflow_merge_deduplicates_normalized_url_aliases(self) -> None:
        search_item = {
            "sourceId": "aggregator",
            "vacancyUrl": "https://EXAMPLE.com/jobs/123/?view=full#description",
            "title": "QA Engineer",
            "relevanceScore": 3.0,
        }
        discovered_item = {
            "sourceId": "career:example",
            "vacancyUrl": "https://example.com/jobs/123?view=full",
            "company": {"name": "Example"},
            "relevanceScore": 4.0,
        }

        merged = merge_workflow_items((search_item,), (), (discovered_item,))

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["relevanceScore"], 4.0)
        self.assertEqual(merged[0]["company"], {"name": "Example"})

    async def test_resume_repairs_partial_artifacts_without_refetching_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            search_bundle = _CountingSearchBundle()
            writes = 0

            def fail_during_report(path: Path, content: bytes) -> None:
                nonlocal writes
                writes += 1
                if writes == _ARTIFACT_CRASH_WRITE_NUMBER:
                    raise OSError("simulated report write crash")
                atomic_write_bytes(path, content)

            failed_pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((search_bundle,)),
                runtime_factory=_RuntimeFactory(),
                artifact_writer=fail_during_report,
            )

            with self.assertRaisesRegex(OSError, "simulated report write crash"):
                await failed_pipeline.run(
                    SearchRequest(query_variants=("QA",)),
                    run_id="r-artifact-resume",
                )

            database_path = Path(directory) / "r-artifact-resume" / "run.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                execution_id = connection.execute(
                    """
                    SELECT execution_id FROM search_executions
                    WHERE execution_kind = 'search'
                    """
                ).fetchone()[0]
                statuses_before = dict(
                    connection.execute(
                        "SELECT execution_kind, status FROM search_executions"
                    ).fetchall()
                )
            self.assertEqual(search_bundle.calls, 1)
            self.assertEqual(statuses_before["search"], "completed")
            self.assertEqual(statuses_before["discovered_search"], "completed")
            self.assertEqual(statuses_before["enrichment"], "artifacts_pending")

            resumed = await _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((search_bundle,)),
                runtime_factory=_RuntimeFactory(),
            ).resume_execution(execution_id)

            self.assertEqual(search_bundle.calls, 1)
            self.assertEqual(len(resumed.final_items), 1)
            self.assertTrue(resumed.paths.report_html_path.exists())
            self.assertTrue(resumed.paths.execution_json_path.exists())
            with closing(sqlite3.connect(database_path)) as connection:
                statuses_after = dict(
                    connection.execute(
                        "SELECT execution_kind, status FROM search_executions"
                    ).fetchall()
                )
                artifact_statuses = {
                    row[0]
                    for row in connection.execute(
                        "SELECT status FROM execution_artifacts"
                    ).fetchall()
                }
            self.assertEqual(set(statuses_after.values()), {"completed"})
            self.assertEqual(artifact_statuses, {"verified"})

    async def test_listing_source_uses_explicit_catalog_parser_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_ArbitraryNamedSearchBundle(),)),
                runtime_factory=_RuntimeFactory(),
                source_bindings=(
                    ListingParserBinding(
                        source_id="catalog-source",
                        source_type=SourceType.AGGREGATOR,
                        parser_ref=ParserRef("listing-engine-v9", "1.0"),
                    ),
                ),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-explicit-listing-binding",
            )

            self.assertEqual(1, len(execution.final_items))
            self.assertEqual("catalog-source", execution.final_items[0]["sourceId"])

    async def test_pipeline_uses_one_canonical_contract_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_CanonicalSearchBundle(),)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("Data Analyst",),
                    grades=(Grade.MIDDLE,),
                    compensation=CompensationCriterion(
                        250_000,
                        "RUB",
                        CompensationPeriod.MONTH,
                        gross=True,
                    ),
                    work_formats=(WorkFormat.HYBRID,),
                ),
                run_id="r-canonical-contract",
            )

            self.assertEqual(1, len(execution.final_items))
            result = execution.final_items[0]
            self.assertEqual(["London", "Vilnius"], result["location"]["cities"])
            self.assertEqual(["GB", "LT"], result["location"]["countries"])
            self.assertEqual(
                ["country:DE"],
                result["workplace"]["remoteScopes"],
            )
            self.assertEqual(
                {"resolved": ["middle"], "conflict": True},
                result["grade"],
            )
            self.assertEqual(
                {
                    "minimum": 300_000,
                    "maximum": 400_000,
                    "currency": "RUB",
                    "period": "month",
                    "gross": True,
                },
                result["compensation"],
            )
            self.assertIs(result["relocation"]["supported"], True)
            self.assertEqual([], execution.processed_payload["filtered_out_results"])
            report = execution.paths.report_html_path.read_text(encoding="utf-8")
            self.assertIn("London", report)
            self.assertIn("Vilnius", report)
            self.assertIn("country:DE", report)
            self.assertIn("300000", report)

    def test_public_projection_uses_exact_canonical_selection_facts(self) -> None:
        projected = public_vacancy_projection(
            {
                "title": "Middle QA Lead",
                "vacancy_url": "https://example.com/jobs/1",
                "location": {"text": "stale"},
                "salary": {"salary_from": 1},
                "work_formats": ["remote"],
                "remote_scopes": [],
                "native_grade": "senior",
                "derived_facts": {
                    "structured-selection-facts": {
                        "location": {
                            "raw_text": "London | Vilnius",
                            "cities": ["London", "Vilnius"],
                            "countries": ["GB", "LT"],
                            "regions": ["EU"],
                            "evidence": ["location"],
                        },
                        "workplace": {
                            "formats": ["hybrid", "remote"],
                            "remote_scopes": ["country:DE"],
                            "evidence": ["work_formats", "remote_scopes"],
                        },
                        "grade": {
                            "title_evidence": ["middle", "lead"],
                            "source_evidence": ["senior"],
                            "resolved": ["middle", "lead"],
                            "conflict": True,
                            "evidence": ["title", "native_grade"],
                        },
                        "compensation": {
                            "minimum": 300_000,
                            "maximum": 400_000,
                            "currency": "RUB",
                            "period": "month",
                            "gross": True,
                            "evidence": ["salary"],
                        },
                        "relocation": {
                            "supported": True,
                            "destinations": ["US"],
                            "evidence": ["description"],
                        },
                        "visa_sponsorship": {
                            "supported": False,
                            "evidence": ["description"],
                        },
                        "employer_geographies": ["country:RU"],
                    }
                },
            }
        )

        self.assertEqual(["London", "Vilnius"], projected["location"]["cities"])
        self.assertEqual(
            ["country:DE"],
            projected["workplace"]["remoteScopes"],
        )
        self.assertEqual(["middle", "lead"], projected["grade"]["resolved"])
        self.assertEqual("RUB", projected["compensation"]["currency"])
        self.assertEqual(
            {"supported": True, "destinations": ["US"]},
            projected["relocation"],
        )
        self.assertNotIn("evidence", str(projected))
        self.assertNotIn("nativeGrade", projected)

    async def test_unknown_grade_enriches_then_rejects_after_provider_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detail = _UnknownGradeDetailBundle()
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), detail)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    grades=(Grade.SENIOR,),
                ),
                run_id="r-unknown-grade",
            )

            self.assertEqual(1, detail.calls)
            self.assertEqual(0, execution.processed_payload["result_count"])
            self.assertEqual(
                1,
                len(execution.processed_payload["filtered_out_results"]),
            )
            self.assertEqual(
                ["insufficient_evidence:grades"],
                execution.processed_payload["filtered_out_results"][0][
                    "decision_reasons"
                ],
            )

    async def test_only_failed_continuation_page_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = _FlakyContinuationSearchBundle()
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    company_enrichment_enabled=False,
                    request_retry_policy=RequestRetryPolicy(random_fraction=lambda: 0.0),
                ),
                registry=ParserRegistry((bundle,)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-page-retry",
            )

            self.assertEqual(bundle.calls, {0: 1, 1: 2})
            self.assertEqual(len(execution.final_items), 1)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                attempts = tuple(
                    connection.execute(
                        "SELECT invocation.task_key, attempt.retry_decision "
                        "FROM parser_attempts AS attempt "
                        "JOIN parser_invocations AS invocation "
                        "ON invocation.invocation_id = attempt.invocation_id "
                        "ORDER BY invocation.task_key, attempt.attempt_number"
                    )
                )
            self.assertEqual(sum(decision == "scheduled" for _, decision in attempts), 1)

    async def test_receipt_marks_partial_source_coverage_as_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    company_enrichment_enabled=False,
                ),
                registry=ParserRegistry((_SearchBundle(), _FailingSearchBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-degraded",
            )

            self.assertEqual(execution.receipt["execution_quality"], "degraded")
            self.assertEqual(execution.processed_payload["execution_quality"], "degraded")
            self.assertEqual(
                execution.processed_payload["source_coverage"],
                execution.receipt["diagnostics"]["source_coverage"],
            )
            self.assertEqual(
                execution.receipt["diagnostics"]["source_coverage"],
                {
                    "planned": 2,
                    "complete": 1,
                    "degraded": 1,
                    "failed": 1,
                    "status_counts": {"failed": 1, "succeeded": 1},
                },
            )

    async def test_receipt_marks_zero_usable_failed_sources_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    company_enrichment_enabled=False,
                ),
                registry=ParserRegistry((_FailingSearchBundle(),)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-failed",
            )

            self.assertEqual(execution.receipt["execution_quality"], "failed")
            self.assertEqual(execution.final_items, ())

    async def test_proven_scenario_branch_does_not_fetch_unneeded_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    company_enrichment_enabled=False,
                ),
                registry=ParserRegistry((_EmployerSearchBundle(), _EmployerProfileBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    scenarios=(
                        SearchScenario(
                            work_formats=(WorkFormat.REMOTE,),
                        ),
                        SearchScenario(
                            employer_geographies=("country:RU",),
                        ),
                    ),
                ),
                run_id="r-scenario-short-circuit",
            )

            self.assertEqual(len(execution.final_items), 1)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                profile_count = connection.execute(
                    "SELECT COUNT(*) FROM parser_invocations WHERE parser_type = 'company_profile'"
                ).fetchone()[0]
            self.assertEqual(profile_count, 0)

    async def test_requested_employer_geography_fetches_profile_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    company_enrichment_enabled=False,
                ),
                registry=ParserRegistry((_EmployerSearchBundle(), _EmployerProfileBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    employer_geographies=("country:RU",),
                ),
                run_id="r-required-employer-geography",
            )

            self.assertEqual(len(execution.final_items), 1)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                requirement = connection.execute(
                    """
                    SELECT requirement.criterion, provider.required
                    FROM criterion_requirements AS requirement
                    JOIN listing_enrichment_requests AS provider
                      ON provider.provider_id IN (
                          SELECT provider_id FROM fact_providers
                          WHERE requirement_id = requirement.requirement_id
                      )
                    WHERE requirement.criterion = 'employer_geographies'
                    """
                ).fetchone()
            self.assertEqual(requirement, ("employer_geographies", 1))

    async def test_default_company_enrichment_runs_profile_then_site_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry(
                    (_EmployerSearchBundle(), _EmployerProfileBundle(), _EmployerSiteBundle())
                ),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-company-enrichment",
            )

            self.assertEqual(len(execution.final_items), 1)
            self.assertEqual(execution.final_items[0]["officialSiteUrl"], "https://example.com")
            self.assertEqual(
                execution.final_items[0]["careerEndpoints"][0]["url"],
                "https://example.com/careers",
            )
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                task_counts = dict(
                    connection.execute(
                        "SELECT parser_type, COUNT(*) FROM parser_invocations GROUP BY parser_type"
                    ).fetchall()
                )
                enrichment_execution = connection.execute(
                    """
                    SELECT execution_id, parent_execution_id, status
                    FROM search_executions
                    WHERE execution_kind = 'enrichment'
                    """
                ).fetchone()
                optional_execution_ids = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT DISTINCT execution_id
                        FROM parser_invocations
                        WHERE parser_type IN ('company_profile', 'company_site')
                        """
                    ).fetchall()
                }
            self.assertEqual(task_counts["company_profile"], 1)
            self.assertEqual(task_counts["company_site"], 1)
            assert enrichment_execution is not None
            self.assertEqual(enrichment_execution[1], execution.execution_id)
            self.assertEqual(enrichment_execution[2], "completed")
            self.assertEqual(optional_execution_ids, {enrichment_execution[0]})

    async def test_discovered_career_listing_runs_in_separate_child_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ParserRegistry(
                (
                    _EmployerSearchBundle(),
                    _EmployerProfileBundle(),
                    _EmployerSiteBundle(),
                    _DiscoveredCareerSearchBundle(),
                )
            )
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=registry,
                runtime_factory=_RuntimeFactory(),
                source_bindings=(
                    ListingParserBinding(
                        source_id="employer",
                        source_type=SourceType.AGGREGATOR,
                        parser_ref=_EmployerSearchBundle.manifest.ref,
                    ),
                ),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-discovered-career",
            )

            self.assertEqual(len(execution.final_items), 2)
            self.assertIn(
                "https://example.com/careers/jobs/career-1",
                {item["vacancyUrl"] for item in execution.final_items},
            )
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                child = connection.execute(
                    """
                    SELECT execution_id, parent_execution_id, status
                    FROM search_executions
                    WHERE execution_kind = 'discovered_search'
                    """
                ).fetchone()
                discovered_plan = connection.execute(
                    """
                    SELECT execution_id, origin_endpoint_id, status
                    FROM source_plans
                    WHERE origin_endpoint_id IS NOT NULL
                    """
                ).fetchone()
                discovered_invocations = connection.execute(
                    """
                    SELECT COUNT(*) FROM parser_invocations
                    WHERE execution_id = ? AND parser_type = 'search_listing'
                    """,
                    (None if child is None else child[0],),
                ).fetchone()[0]
            assert child is not None
            assert discovered_plan is not None
            self.assertEqual(child[1], execution.enrichment_execution_id)
            self.assertEqual(child[2], "completed")
            self.assertEqual(child[0], execution.discovered_search_execution_id)
            self.assertEqual(discovered_plan[0], child[0])
            self.assertIsNotNone(discovered_plan[1])
            self.assertEqual(discovered_plan[2], "succeeded")
            self.assertEqual(discovered_invocations, 1)
            self.assertEqual(
                execution.receipt["discovered_search"]["diagnostics"]["result_count"],
                1,
            )

    async def test_listing_official_site_skips_profile_and_runs_site_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry(
                    (
                        _EmployerSearchWithSiteBundle(),
                        _EmployerProfileBundle(),
                        _EmployerSiteBundle(),
                    )
                ),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-company-listing-site",
            )

            self.assertEqual(len(execution.final_items), 1)
            self.assertEqual(
                execution.final_items[0]["careerEndpoints"][0]["url"],
                "https://example.com/careers",
            )
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                task_counts = dict(
                    connection.execute(
                        "SELECT parser_type, COUNT(*) FROM parser_invocations GROUP BY parser_type"
                    ).fetchall()
                )
            self.assertNotIn("company_profile", task_counts)
            self.assertEqual(task_counts["company_site"], 1)

    async def test_provider_success_without_declared_fact_settles_without_hanging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _MissingFactDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-missing-provider-fact",
            )

            self.assertEqual(len(execution.final_items), 1)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                enrichment = connection.execute(
                    "SELECT status, resolution_outcome FROM listing_enrichment_requests"
                ).fetchone()
            self.assertEqual(enrichment, ("terminal", "provider_output_missing_fact"))

    async def test_completed_listing_refills_worker_slot_before_slow_peer_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = asyncio.Event()
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    task_batch_size=2,
                    attempt_timeout_seconds=0.2,
                ),
                registry=ParserRegistry(
                    (
                        _BlockingSearchBundle(release),
                        _UnblockingSearchBundle(),
                        _UnblockingDetailBundle(release),
                    )
                ),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(SearchRequest(query_variants=("QA",)), run_id="r-streaming")

            self.assertEqual({item["sourceId"] for item in execution.final_items}, {"blocking", "unblocking"})

    async def test_unknown_listing_relocation_is_resolved_by_detail_before_final_filter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _RelocationDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",), relocation=True),
                run_id="r-relocation",
            )

            self.assertEqual(len(execution.final_items), 1)
            self.assertIs(execution.final_items[0]["relocation"]["supported"], True)

    async def test_shared_detail_invocation_is_materialized_once_per_listing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _RelocationDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            with patch(
                "job_harness.v2.runtime.graph_coordinator.derive_selection_facts",
                wraps=derive_selection_facts,
            ) as derivation_evaluator:
                execution = await pipeline.run(
                    SearchRequest(query_variants=("QA",), relocation=True),
                    run_id="r-shared-detail-materialization",
                )

            self.assertEqual(len(execution.final_items), 1)
            self.assertEqual(derivation_evaluator.call_count, 2)

    async def test_late_duplicate_listing_preserves_completed_detail_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release_duplicate = asyncio.Event()

            def release_after_detail(progress: GraphSearchProgress) -> None:
                if progress.tasks_completed >= _LISTING_AND_DETAIL_TASK_COUNT:
                    release_duplicate.set()

            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    task_batch_size=2,
                    progress_callback=release_after_detail,
                    progress_interval_seconds=0.0,
                ),
                registry=ParserRegistry(
                    (
                        _DuplicateAfterDetailSearchBundle(release_duplicate),
                        _RelocationDetailBundle(),
                    )
                ),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",), relocation=True),
                run_id="r-late-duplicate",
            )

            self.assertEqual(len(execution.final_items), 1)
            self.assertIs(execution.final_items[0]["relocation"]["supported"], True)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                counts = {
                    table: connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE execution_id = ?",
                        (execution.execution_id,),
                    ).fetchone()[0]
                    for table in (
                        "listing_observations",
                        "vacancy_detail_observations",
                        "fact_derivations",
                        "fact_sets",
                        "selection_evaluations",
                    )
                }
            self.assertEqual(counts["listing_observations"], 2)
            self.assertEqual(counts["vacancy_detail_observations"], 1)
            self.assertEqual(counts["fact_derivations"], 2)
            self.assertEqual(counts["fact_sets"], 2)
            self.assertEqual(counts["selection_evaluations"], 2)

    async def test_unknown_relocation_is_rejected_when_detail_provider_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _FailingDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",), relocation=True),
                run_id="r-relocation-failed",
            )

            self.assertEqual(execution.final_items, ())
            self.assertEqual("degraded", execution.receipt["execution_quality"])
            self.assertEqual("degraded", execution.processed_payload["execution_quality"])
            self.assertEqual(
                1,
                execution.receipt["diagnostics"]["required_enrichment_failures"],
            )

    async def test_per_query_source_budget_scales_with_query_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_PerQueryBudgetBundle(),)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA", "SDET")),
                run_id="r-budget",
            )

            self.assertEqual(len(execution.final_items), 4)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                plan = connection.execute(
                    "SELECT unit_budget, item_budget, invocation_budget, status FROM source_plans"
                ).fetchone()
                failed = connection.execute(
                    "SELECT COUNT(*) FROM parser_invocations WHERE status = 'failed'"
                ).fetchone()[0]
            self.assertEqual(plan, (2, 4, 2, "succeeded"))
            self.assertEqual(failed, 0)

    async def test_downstream_only_source_budget_does_not_scale_with_query_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_DownstreamBudgetBundle(),)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA", "SDET", "Tester")),
                run_id="r-downstream-budget",
            )

            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                plan = connection.execute(
                    "SELECT unit_budget, item_budget, invocation_budget, status FROM source_plans"
                ).fetchone()
                invocation_count = connection.execute(
                    "SELECT COUNT(*) FROM parser_invocations WHERE parser_type = 'search_listing'"
                ).fetchone()[0]
            self.assertEqual(plan, (1, 2, 1, "succeeded"))
            self.assertEqual(invocation_count, 1)

    async def test_pipeline_reports_progress_without_querying_result_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            progress: list[GraphSearchProgress] = []
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    progress_callback=progress.append,
                    progress_interval_seconds=0.0,
                ),
                registry=ParserRegistry((_SearchBundle(),)),
                runtime_factory=_RuntimeFactory(),
            )

            await pipeline.run(SearchRequest(query_variants=("QA",)), run_id="r-progress")

            self.assertGreaterEqual(len(progress), 1)
            self.assertGreaterEqual(progress[-1].tasks_completed, 1)
            self.assertGreaterEqual(progress[-1].events_processed, 1)

    async def test_runs_managed_graph_to_final_snapshot_without_legacy_raw_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _FailingDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(SearchRequest(query_variants=("QA",)), run_id="r-test")

            self.assertEqual(execution.append_sequence, 0)
            self.assertEqual(len(execution.final_items), 1)
            self.assertEqual(execution.final_items[0]["title"], "QA Engineer")
            self.assertEqual(execution.final_items[0]["sourceId"], "test")
            self.assertNotIn("source_id", execution.final_items[0])
            self.assertNotIn("target_provider_id", execution.final_items[0])
            self.assertTrue(execution.paths.report_html_path.exists())
            self.assertTrue(execution.paths.execution_json_path.exists())
            receipt = json.loads(execution.paths.execution_json_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt, execution.receipt)
            self.assertEqual(receipt["diagnostics"]["listing_observation_count"], 1)
            self.assertEqual(receipt["diagnostics"]["result_count"], 1)
            self.assertNotIn("failed", receipt["diagnostics"]["invocations"]["status_counts"])
            self.assertEqual(receipt["enrichment"]["execution_id"], execution.enrichment_execution_id)
            self.assertEqual(
                receipt["enrichment"]["diagnostics"]["invocations"]["status_counts"]["failed"],
                1,
            )
            source_plan = receipt["diagnostics"]["source_plans"][0]
            self.assertGreaterEqual(source_plan["elapsed_ms"], 0)
            self.assertLess(source_plan["elapsed_ms"], 5_000)
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                status = connection.execute("SELECT status FROM search_executions").fetchone()[0]
                enrichment_status = connection.execute(
                    "SELECT status FROM listing_enrichment_requests"
                ).fetchone()[0]
            self.assertNotIn("raw_listings", tables)
            self.assertIn("listing_observations", tables)
            self.assertEqual(status, "completed")
            self.assertEqual(enrichment_status, "terminal")
            stored_payload = read_graph_processed_payload(
                execution.paths.database_path,
                projector=public_vacancy_projection,
            )
            self.assertEqual(stored_payload["execution_id"], execution.execution_id)
            self.assertEqual(stored_payload["result_count"], 1)

    async def test_graph_report_projects_title_matching_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(),)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    work_formats=(WorkFormat.OFFICE,),
                ),
                run_id="r-filtered-report",
            )

            self.assertEqual(0, execution.processed_payload["result_count"])
            self.assertEqual([], execution.processed_payload["results"])
            self.assertEqual(1, len(execution.processed_payload["filtered_out_results"]))
            filtered = execution.processed_payload["filtered_out_results"][0]
            self.assertEqual("QA Engineer", filtered["title"])
            self.assertEqual("filtered_out", filtered["decision"])
            self.assertEqual(["work_format_mismatch"], filtered["decision_reasons"])

            stored_payload = read_graph_processed_payload(
                execution.paths.database_path,
                projector=public_vacancy_projection,
            )
            self.assertEqual(execution.processed_payload, stored_payload)
            report = execution.paths.report_html_path.read_text(encoding="utf-8")
            self.assertIn('"decision": "filtered_out"', report)
            self.assertIn('"decision_reasons": ["work_format_mismatch"]', report)

    async def test_source_selector_honors_business_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _CompanySearchBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    source_types=(SourceType.COMPANY_CAREER,),
                ),
                run_id="r-company",
            )

            self.assertEqual(len(execution.final_items), 1)
            self.assertEqual(execution.final_items[0]["sourceId"], "company")
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                source_ids = tuple(
                    row[0]
                    for row in connection.execute("SELECT source_id FROM source_plans ORDER BY source_id")
                )
            self.assertEqual(source_ids, ("company",))

    async def test_preliminary_reject_does_not_schedule_detail_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_SearchBundle(), _FailingDetailBundle())),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(
                    query_variants=("QA",),
                    exclude_companies=("Example",),
                ),
                run_id="r-rejected",
            )

            self.assertEqual(execution.final_items, ())
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                detail_tasks = connection.execute(
                    "SELECT COUNT(*) FROM parser_invocations WHERE parser_type = 'vacancy_detail'"
                ).fetchone()[0]
                final_outcome = connection.execute(
                    "SELECT outcome FROM selection_evaluations WHERE stage = 'final'"
                ).fetchone()[0]
            self.assertEqual(detail_tasks, 0)
            self.assertEqual(final_outcome, "reject")

    async def test_speculative_enrichment_caps_each_source_inside_opportunistic_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detail = _CountingOptionalDetailBundle()
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry((_RankedBatchSearchBundle(), detail)),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA Engineer",)),
                run_id="r-ranked-speculative-admission",
            )

            self.assertEqual(len(execution.final_items), 60)
            self.assertEqual(len(detail.source_listing_ids), _SPECULATIVE_SOURCE_BATCH_SIZE)
            self.assertEqual(
                set(detail.source_listing_ids),
                {
                    item["sourceListingId"]
                    for item in execution.final_items[:_SPECULATIVE_SOURCE_BATCH_SIZE]
                },
            )
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                speculative_count = connection.execute(
                    """
                    SELECT speculative_admissions_created
                    FROM search_executions WHERE execution_id = ?
                    """,
                    (execution.enrichment_execution_id,),
                ).fetchone()[0]
                resource_rows = connection.execute(
                    """
                    SELECT DISTINCT resource_key, resource_key_resolved
                    FROM parser_invocations
                    WHERE parser_id IN ('test.search', 'test.detail')
                    """
                ).fetchall()
            self.assertEqual(speculative_count, _SPECULATIVE_SOURCE_BATCH_SIZE)
            self.assertEqual(resource_rows, [("example.com", 1)])

    async def test_speculative_enrichment_fills_global_budget_across_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detail_listing_ids: list[str] = []
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
                registry=ParserRegistry(
                    (
                        _RankedSourceBatchSearchBundle("source-a"),
                        _RankedSourceBatchSearchBundle("source-b"),
                        _RankedSourceBatchSearchBundle("source-c"),
                        _CountingSourceDetailBundle("source-a", detail_listing_ids),
                        _CountingSourceDetailBundle("source-b", detail_listing_ids),
                        _CountingSourceDetailBundle("source-c", detail_listing_ids),
                    )
                ),
                runtime_factory=_RuntimeFactory(),
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA Engineer",)),
                run_id="r-multi-source-speculative-admission",
            )

            self.assertEqual(len(execution.final_items), 36)
            self.assertEqual(len(detail_listing_ids), _SPECULATIVE_BATCH_SIZE)
            admissions_by_source = Counter(
                listing_id.split("-ranked-", 1)[0]
                for listing_id in detail_listing_ids
            )
            self.assertEqual(len(admissions_by_source), 3)
            self.assertLessEqual(
                max(admissions_by_source.values()),
                _SPECULATIVE_SOURCE_BATCH_SIZE,
            )
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                speculative_count = connection.execute(
                    """
                    SELECT speculative_admissions_created
                    FROM search_executions WHERE execution_id = ?
                    """,
                    (execution.enrichment_execution_id,),
                ).fetchone()[0]
            self.assertEqual(speculative_count, _SPECULATIVE_BATCH_SIZE)

    async def test_deadline_fences_late_result_and_completes_with_deadline_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            pipeline = _pipeline(
                config=GraphSearchPipelineConfig(
                    runs_dir=Path(directory),
                    execution_timeout_seconds=1.0,
                ),
                registry=ParserRegistry((_DeadlineSearchBundle(clock),)),
                runtime_factory=_RuntimeFactory(),
                clock=clock,
            )

            execution = await pipeline.run(
                SearchRequest(query_variants=("QA",)),
                run_id="r-deadline",
            )

            self.assertEqual(execution.final_items, ())
            with closing(sqlite3.connect(execution.paths.database_path)) as connection:
                invocation_status = connection.execute(
                    "SELECT status FROM parser_invocations"
                ).fetchone()[0]
                source_status = connection.execute(
                    "SELECT status FROM source_plans"
                ).fetchone()[0]
                execution_row = connection.execute(
                    "SELECT status, completion_reason FROM search_executions"
                ).fetchone()
            self.assertEqual(invocation_status, "cancelled")
            self.assertEqual(source_status, "cancelled")
            self.assertEqual(execution_row, ("completed", "deadline"))


if __name__ == "__main__":
    unittest.main()
