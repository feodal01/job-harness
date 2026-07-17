from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanyProfileOutput,
    CompanyProfileResult,
    CompanyRef,
    CompanySiteInput,
    CompanySiteOutput,
    CompanySiteResult,
    DiscoveredEndpoint,
    FactProviderSpec,
    InvocationScope,
    ParserInvocationSpec,
    ParserManifest,
    ParserRef,
    ParserRegistry,
    ParserType,
    ProviderStage,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchRequest,
    SearchResultOutcome,
    SingletonResultOutcome,
    SourceLocation,
    TaskClass,
    TransportKind,
    VacancyDetailInput,
    VacancyDetailOutput,
    VacancyDetailResult,
)
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.runtime.graph_coordinator import GraphCoordinator
from job_harness.v2.runtime.source_registry import build_independent_parser_registry


def _search_manifest() -> ParserManifest:
    return ParserManifest(
        parser_id="hh.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id="hh.search.input.v1",
        output_schema_id="hh.search.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("hh",),
        supported_url_patterns=(),
        output_facts=("title", "vacancyUrl"),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=("aggregator",),
        query_mode="per_query",
        collection_unit="page",
        native_criteria=("query",),
        default_unit_budget=3,
        default_item_budget=100,
        default_invocation_budget=4,
    )


def _input(page: int) -> SearchListingInput:
    return SearchListingInput(
        source_id="hh_ru",
        target_provider_id="hh",
        queries=("QA",),
        target={"kind": "catalog"},
        cursor={"page": page},
        native_filters={},
        resolved_state=None,
    )


def _detail_manifest() -> ParserManifest:
    return ParserManifest(
        parser_id="hh.detail",
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id="hh.detail.input.v1",
        output_schema_id="hh.detail.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("hh",),
        supported_url_patterns=(r"https://hh\.ru/vacancy/.*",),
        output_facts=("description",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )


class _DetailBundle:
    manifest = _detail_manifest()
    input_type = VacancyDetailInput
    result_type = VacancyDetailResult

    async def execute(self, parser_input: object, runtime: object) -> object:
        raise NotImplementedError


def _profile_manifest() -> ParserManifest:
    return ParserManifest(
        parser_id="hh.profile",
        parser_type=ParserType.COMPANY_PROFILE,
        implementation_version="1.0",
        input_schema_id="hh.profile.input.v1",
        output_schema_id="hh.profile.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("hh",),
        supported_url_patterns=(r"https://hh\.ru/employer/.*",),
        output_facts=("officialSiteUrl",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )


class _ProfileBundle:
    manifest = _profile_manifest()
    input_type = CompanyProfileInput
    result_type = CompanyProfileResult

    async def execute(self, parser_input: object, runtime: object) -> object:
        raise NotImplementedError


def _site_manifest() -> ParserManifest:
    return ParserManifest(
        parser_id="web.company-site",
        parser_type=ParserType.COMPANY_SITE,
        implementation_version="1.0",
        input_schema_id="web.company-site.input.v1",
        output_schema_id="web.company-site.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("web",),
        supported_url_patterns=(r"https://.*",),
        output_facts=("careerEndpoints",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        is_fallback=True,
    )


class _SiteBundle:
    manifest = _site_manifest()
    input_type = CompanySiteInput
    result_type = CompanySiteResult

    async def execute(self, parser_input: object, runtime: object) -> object:
        raise NotImplementedError


class _DiscoveredSearchBundle:
    manifest = replace(
        _search_manifest(),
        parser_id="discovered.search",
        provider_ids=("web",),
        supported_url_patterns=(r"https://example\.com/(?:careers|jobs)",),
        source_kinds=("discovered",),
    )
    input_type = SearchListingInput
    result_type = SearchListingResult

    def plan_initial(self, intent: SearchRequest, target: dict[str, object]) -> tuple[SearchListingInput, ...]:
        return (
            SearchListingInput(
                source_id="discovered",
                target_provider_id="web",
                queries=intent.query_variants,
                target=target,
                cursor={"page": 0},
                native_filters={},
                resolved_state=None,
            ),
        )

    async def execute(self, parser_input: object, runtime: object) -> object:
        raise NotImplementedError


def _listing() -> SearchListingOutput:
    return SearchListingOutput(
        source_id="hh_ru",
        target_provider_id="hh",
        source_listing_id="123",
        title="QA Engineer",
        company=CompanyRef(name="Example"),
        location=SourceLocation("Moscow"),
        salary=None,
        work_formats=("hybrid",),
        remote_scopes=(),
        native_grade=None,
        posted_at=None,
        vacancy_url="https://hh.ru/vacancy/123",
        apply_url=None,
        summary=None,
    )


class GraphCoordinatorTest(unittest.TestCase):
    def test_eventless_turn_does_not_open_a_write_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-eventless",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry(()),
                owner_id="coordinator",
            )
            statements: list[str] = []
            repository._connection.set_trace_callback(statements.append)  # noqa: SLF001
            try:
                processed = coordinator.process_once(
                    execution_id,
                    limit=20,
                    lease_seconds=30.0,
                    now=100.0,
                )
            finally:
                repository._connection.set_trace_callback(None)  # noqa: SLF001

            self.assertEqual(processed, 0)
            self.assertFalse(
                any(
                    statement.lstrip().upper().startswith(
                        ("BEGIN", "COMMIT", "INSERT", "UPDATE", "DELETE")
                    )
                    for statement in statements
                ),
                statements,
            )

    def test_single_oversized_listing_event_is_resumed_in_250_item_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            manifest = replace(
                _search_manifest(),
                default_item_budget=300,
                default_unit_budget=2,
                default_invocation_budget=2,
            )
            execution_id = repository.create_execution(
                run_id="r-bounded-coordinator",
                intent={"query_variants": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            source_plan_id = repository.create_source_plan(
                execution_id=execution_id,
                source_id="hh_ru",
                manifest=manifest,
                queries=("QA",),
                unit_budget=2,
                item_budget=300,
                invocation_budget=2,
            )
            repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=manifest.ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=manifest.input_schema_id,
                    parser_input=_input(0),
                    task_class=TaskClass.LISTING,
                    task_key="oversized-page",
                    available_at=0.0,
                    reserved_collection_units=1,
                )
            )
            invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="worker",
                limit=1,
                lease_seconds=30.0,
                now=100.0,
            )[0]
            repository.commit_search_result(
                invocation,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=tuple(
                        replace(
                            _listing(),
                            source_listing_id=str(index),
                            vacancy_url=f"https://hh.ru/vacancy/{index}",
                        )
                        for index in range(251)
                    ),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                manifest,
                now=101.0,
            )
            registry = ParserRegistry(())
            coordinator = GraphCoordinator(
                repository=repository,
                registry=registry,
                owner_id="coordinator",
            )

            first_count = coordinator.process_once(
                execution_id,
                limit=20,
                lease_seconds=30.0,
                now=102.0,
            )

            self.assertEqual(first_count, 1)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM fact_sets"), 250)
            self.assertIsNone(self._scalar(database_path, "SELECT processed_at FROM domain_events"))
            self.assertEqual(
                self._scalar(database_path, "SELECT processing_offset FROM domain_events"),
                250,
            )

            second_count = coordinator.process_once(
                execution_id,
                limit=20,
                lease_seconds=30.0,
                now=103.0,
            )

            self.assertEqual(second_count, 1)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM fact_sets"), 251)
            self.assertIsNotNone(self._scalar(database_path, "SELECT processed_at FROM domain_events"))

    def test_listing_event_schedules_missing_fact_provider_before_page_two_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-test",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            source_plan_id = repository.create_source_plan(
                execution_id=execution_id,
                source_id="hh_ru",
                manifest=_search_manifest(),
                queries=("QA",),
                unit_budget=3,
                item_budget=100,
                invocation_budget=4,
            )
            repository.add_fact_requirement(
                source_plan_id=source_plan_id,
                criterion="description_required",
                fact_path="description",
                comparison={"operator": "exists"},
                provider=FactProviderSpec(
                    provider_id="hh-detail-description",
                    stage=ProviderStage.DETAIL_OUTPUT,
                    parser_ref=ParserRef("hh.detail", "1.0"),
                    fact_path="description",
                    depends_on_fact_paths=(),
                    required_for_final=True,
                    cost_class="detail",
                    ordering=10,
                ),
            )
            first_id = repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=_search_manifest().ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=_search_manifest().input_schema_id,
                    parser_input=_input(0),
                    task_class=TaskClass.LISTING,
                    task_key="hh-page-0",
                    available_at=0.0,
                    reserved_collection_units=1,
                )
            )
            first = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="listing-worker",
                limit=1,
                lease_seconds=30.0,
                now=100.0,
            )[0]
            repository.commit_search_result(
                first,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing(),),
                    continuations=(_input(1),),
                    collection_units_consumed=1,
                ),
                _search_manifest(),
                now=101.0,
            )

            coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry((_DetailBundle(),)),
                owner_id="coordinator",
            )
            processed = coordinator.process_once(
                execution_id,
                limit=20,
                lease_seconds=30.0,
                now=102.0,
            )

            self.assertEqual(processed, 1)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM fact_sets"), 1)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM fact_derivations"), 1)
            self.assertEqual(
                self._scalar(database_path, "SELECT deriver_id FROM fact_derivations"),
                "structured-selection-facts",
            )
            self.assertEqual(
                self._scalar(database_path, "SELECT deriver_version FROM fact_derivations"),
            "6.0",
            )
            derivation_evidence = self._scalar(
                database_path,
                "SELECT input_evidence_refs_json FROM fact_derivations",
            )
            self.assertIn("listingObservationId", str(derivation_evidence))
            fact_evidence = self._scalar(database_path, "SELECT evidence_refs_json FROM fact_sets")
            self.assertIn("factDerivationIds", str(fact_evidence))
            self.assertEqual(
                self._scalar(database_path, "SELECT outcome FROM selection_evaluations"),
                "enrich",
            )
            self.assertEqual(
                self._scalar(database_path, "SELECT COUNT(*) FROM listing_enrichment_requests"),
                1,
            )
            leased = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="mixed-worker",
                limit=2,
                lease_seconds=30.0,
                now=103.0,
            )
            self.assertEqual(
                tuple(item.spec.task_class for item in leased),
                (TaskClass.LISTING, TaskClass.DETAIL),
            )
            self.assertEqual(leased[1].spec.parent_invocation_id, first_id)

            repository.commit_detail_result(
                leased[1],
                VacancyDetailResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=VacancyDetailOutput(
                        target_provider_id="hh",
                        source_listing_id="123",
                        canonical_vacancy_url="https://hh.ru/vacancy/123",
                        title="QA Engineer",
                        company=CompanyRef(name="Example"),
                        description="Full description",
                        requirements=(),
                        responsibilities=(),
                        conditions=(),
                        skills=(),
                        employment_types=(),
                        salary=None,
                        work_formats=("hybrid",),
                        remote_scopes=(),
                        application_channels=(),
                    ),
                ),
                _detail_manifest(),
                now=104.0,
            )
            detail_events_processed = coordinator.process_once(
                execution_id,
                limit=20,
                lease_seconds=30.0,
                now=105.0,
            )

            self.assertEqual(detail_events_processed, 1)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM fact_sets"), 2)
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT outcome FROM selection_evaluations WHERE stage = 'final'",
                ),
                "keep",
            )
            self.assertEqual(
                self._scalar(database_path, "SELECT status FROM listing_enrichment_requests"),
                "satisfied",
            )

    def test_shared_profile_event_resumes_251_consumers_without_n_plus_one_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-profile",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            source_plan_id = repository.create_source_plan(
                execution_id=execution_id,
                source_id="hh_ru",
                manifest=_search_manifest(),
                queries=("QA",),
                unit_budget=1,
                item_budget=300,
                invocation_budget=1,
            )
            repository.add_fact_requirement(
                source_plan_id=source_plan_id,
                criterion="official_site_optional",
                fact_path="official_site_url",
                comparison={"operator": "exists"},
                provider=FactProviderSpec(
                    provider_id="hh-profile-site",
                    stage=ProviderStage.PROFILE_OUTPUT,
                    parser_ref=_profile_manifest().ref,
                    fact_path="official_site_url",
                    depends_on_fact_paths=("company.profile_url",),
                    required_for_final=False,
                    cost_class="profile",
                    ordering=20,
                ),
            )
            repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=_search_manifest().ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=_search_manifest().input_schema_id,
                    parser_input=_input(0),
                    task_class=TaskClass.LISTING,
                    task_key="hh-page-0",
                    available_at=0.0,
                    reserved_collection_units=1,
                )
            )
            listing_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="listing-worker",
                limit=1,
                lease_seconds=30.0,
                now=100.0,
            )[0]
            shared_company = CompanyRef(
                name="Example",
                target_provider_id="hh",
                source_company_id="10",
                profile_url="https://hh.ru/employer/10",
            )
            first = _listing()
            shared_listings = tuple(
                replace(
                    first,
                    source_listing_id=str(1000 + index),
                    vacancy_url=f"https://hh.ru/vacancy/{1000 + index}",
                    company=shared_company,
                )
                for index in range(251)
            )
            unresolved = replace(
                first,
                source_listing_id="2000",
                vacancy_url="https://hh.ru/vacancy/2000",
                company=CompanyRef(name="Name Only"),
            )
            repository.commit_search_result(
                listing_invocation,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(*shared_listings, unresolved),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                _search_manifest(),
                now=101.0,
            )
            coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry((_ProfileBundle(),)),
                owner_id="coordinator",
            )

            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=102.0)
            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=102.5)

            self.assertEqual(
                self._scalar(database_path, "SELECT COUNT(*) FROM listing_enrichment_requests"),
                252,
            )
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM listing_enrichment_requests "
                    "WHERE status = 'terminal' AND resolution_outcome = 'unresolved_no_trusted_url'",
                ),
                1,
            )
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM parser_invocations WHERE parser_type = 'company_profile'",
                ),
                1,
            )
            profile_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="profile-worker",
                limit=1,
                lease_seconds=30.0,
                now=103.0,
            )[0]
            repository.commit_profile_result(
                profile_invocation,
                CompanyProfileResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=CompanyProfileOutput(
                        target_provider_id="hh",
                        profile_url="https://hh.ru/employer/10",
                        source_company_id="10",
                        company_name="Example",
                        description=None,
                        industry=None,
                        size_text=None,
                        locations=(),
                        official_site_url="https://example.com",
                        career_endpoints=(),
                        contacts=(),
                        social_links=(),
                    ),
                ),
                _profile_manifest(),
                now=104.0,
            )

            statements: list[str] = []
            repository._connection.set_trace_callback(statements.append)  # noqa: SLF001
            try:
                coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=105.0)
            finally:
                repository._connection.set_trace_callback(None)  # noqa: SLF001

            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM listing_enrichment_requests WHERE status = 'satisfied'",
                ),
                250,
            )
            self.assertIsNone(
                self._scalar(
                    database_path,
                    "SELECT processed_at FROM domain_events "
                    "WHERE event_type = 'company_profile_observation_stored'",
                )
            )
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT processing_offset FROM domain_events "
                    "WHERE event_type = 'company_profile_observation_stored'",
                ),
                250,
            )
            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=106.0)
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM listing_enrichment_requests WHERE status = 'satisfied'",
                ),
                251,
            )
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM listing_enrichment_requests WHERE status = 'terminal'",
                ),
                1,
            )
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM selection_evaluations WHERE stage = 'final' AND outcome = 'keep'",
                ),
                252,
            )
            enrichment_selects = tuple(
                statement
                for statement in statements
                if statement.lstrip().upper().startswith("SELECT")
                and "FROM listing_enrichment_requests" in statement
            )
            self.assertLessEqual(len(enrichment_selects), 4, enrichment_selects)

    def test_provider_graph_unlocks_detail_then_profile_then_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-provider-chain",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            source_plan_id = repository.create_source_plan(
                execution_id=execution_id,
                source_id="hh_ru",
                manifest=_search_manifest(),
                queries=("QA",),
                unit_budget=1,
                item_budget=10,
                invocation_budget=1,
            )
            for criterion, fact_path, provider in (
                (
                    "profile_target",
                    "company.profile_url",
                    FactProviderSpec(
                        provider_id="hh-detail-profile-target",
                        stage=ProviderStage.DETAIL_OUTPUT,
                        parser_ref=_detail_manifest().ref,
                        fact_path="company.profile_url",
                        depends_on_fact_paths=(),
                        required_for_final=False,
                        cost_class="detail",
                        ordering=10,
                    ),
                ),
                (
                    "official_site",
                    "official_site_url",
                    FactProviderSpec(
                        provider_id="hh-profile-official-site",
                        stage=ProviderStage.PROFILE_OUTPUT,
                        parser_ref=_profile_manifest().ref,
                        fact_path="official_site_url",
                        depends_on_fact_paths=("company.profile_url",),
                        required_for_final=False,
                        cost_class="profile",
                        ordering=20,
                    ),
                ),
                (
                    "career_endpoint",
                    "career_endpoints",
                    FactProviderSpec(
                        provider_id="web-site-career-endpoint",
                        stage=ProviderStage.SITE_OUTPUT,
                        parser_ref=_site_manifest().ref,
                        fact_path="career_endpoints",
                        depends_on_fact_paths=("official_site_url",),
                        required_for_final=False,
                        cost_class="site",
                        ordering=30,
                    ),
                ),
            ):
                repository.add_fact_requirement(
                    source_plan_id=source_plan_id,
                    criterion=criterion,
                    fact_path=fact_path,
                    comparison={"operator": "exists"},
                    provider=provider,
                )
            repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=_search_manifest().ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=_search_manifest().input_schema_id,
                    parser_input=_input(0),
                    task_class=TaskClass.LISTING,
                    task_key="provider-chain-page-0",
                    available_at=0.0,
                    reserved_collection_units=1,
                )
            )
            listing_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="listing-worker",
                limit=1,
                lease_seconds=30.0,
                now=100.0,
            )[0]
            repository.commit_search_result(
                listing_invocation,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(_listing(),),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                _search_manifest(),
                now=101.0,
            )
            coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry((_DetailBundle(), _ProfileBundle(), _SiteBundle())),
                owner_id="coordinator",
            )

            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=102.0)
            detail_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="detail-worker",
                limit=5,
                lease_seconds=30.0,
                now=103.0,
            )
            self.assertEqual((TaskClass.DETAIL,), tuple(item.spec.task_class for item in detail_invocation))
            repository.commit_detail_result(
                detail_invocation[0],
                VacancyDetailResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=VacancyDetailOutput(
                        target_provider_id="hh",
                        source_listing_id="123",
                        canonical_vacancy_url="https://hh.ru/vacancy/123",
                        title="QA Engineer",
                        company=CompanyRef(
                            name="Example",
                            target_provider_id="hh",
                            source_company_id="10",
                            profile_url="https://hh.ru/employer/10",
                        ),
                        description="Full description",
                        requirements=(),
                        responsibilities=(),
                        conditions=(),
                        skills=(),
                        employment_types=(),
                        salary=None,
                        work_formats=("hybrid",),
                        remote_scopes=(),
                        application_channels=(),
                    ),
                ),
                _detail_manifest(),
                now=104.0,
            )

            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=105.0)
            profile_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="profile-worker",
                limit=5,
                lease_seconds=30.0,
                now=106.0,
            )
            self.assertEqual((TaskClass.PROFILE,), tuple(item.spec.task_class for item in profile_invocation))
            repository.commit_profile_result(
                profile_invocation[0],
                CompanyProfileResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=CompanyProfileOutput(
                        target_provider_id="hh",
                        profile_url="https://hh.ru/employer/10",
                        source_company_id="10",
                        company_name="Example",
                        description=None,
                        industry=None,
                        size_text=None,
                        locations=(),
                        official_site_url="https://example.com",
                        career_endpoints=(),
                        contacts=(),
                        social_links=(),
                    ),
                ),
                _profile_manifest(),
                now=107.0,
            )

            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=108.0)
            site_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="site-worker",
                limit=5,
                lease_seconds=30.0,
                now=109.0,
            )
            self.assertEqual((TaskClass.SITE,), tuple(item.spec.task_class for item in site_invocation))
            repository.commit_site_result(
                site_invocation[0],
                CompanySiteResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=CompanySiteOutput(
                        canonical_site_url="https://example.com",
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
                ),
                _site_manifest(),
                now=110.0,
            )

            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=111.0)

            self.assertEqual(
                3,
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM listing_enrichment_requests WHERE status = 'satisfied'",
                ),
            )
            self.assertEqual(
                "keep",
                self._scalar(
                    database_path,
                    "SELECT outcome FROM selection_evaluations WHERE stage = 'final' ORDER BY rowid DESC LIMIT 1",
                ),
            )

    def test_optional_child_late_consumer_reuses_completed_profile_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            execution_id = repository.create_execution(
                run_id="r-late-profile",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
            )
            enrichment_execution_id = repository.create_execution(
                run_id="r-late-profile",
                intent={"queries": ["QA"]},
                append_sequence=0,
                policy_version="enrichment-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
                speculative_admission_budget=2,
                execution_kind="enrichment",
                parent_execution_id=execution_id,
            )
            source_plan_id = repository.create_source_plan(
                execution_id=execution_id,
                source_id="hh_ru",
                manifest=_search_manifest(),
                queries=("QA",),
                unit_budget=2,
                item_budget=10,
                invocation_budget=2,
            )
            repository.add_fact_requirement(
                source_plan_id=source_plan_id,
                criterion="official_site_optional",
                fact_path="official_site_url",
                comparison={"operator": "exists"},
                provider=FactProviderSpec(
                    provider_id="hh-profile-site-late",
                    stage=ProviderStage.PROFILE_OUTPUT,
                    parser_ref=_profile_manifest().ref,
                    fact_path="official_site_url",
                    depends_on_fact_paths=("company.profile_url",),
                    required_for_final=False,
                    cost_class="profile",
                    ordering=20,
                ),
            )
            repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=source_plan_id,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=_search_manifest().ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=_search_manifest().input_schema_id,
                    parser_input=_input(0),
                    task_class=TaskClass.LISTING,
                    task_key="late-profile-page-0",
                    available_at=0.0,
                    reserved_collection_units=1,
                )
            )
            first_page = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="listing-worker",
                limit=1,
                lease_seconds=30.0,
                now=100.0,
            )[0]
            shared_company = CompanyRef(
                name="Example",
                target_provider_id="hh",
                source_company_id="10",
                profile_url="https://hh.ru/employer/10",
            )
            repository.commit_search_result(
                first_page,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(replace(_listing(), company=shared_company),),
                    continuations=(_input(1),),
                    collection_units_consumed=1,
                ),
                _search_manifest(),
                now=101.0,
            )
            parent_coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry((_ProfileBundle(),)),
                owner_id="parent-coordinator",
                request=SearchRequest(query_variants=("QA",)),
                requirement_scope="required",
                optional_execution_id=enrichment_execution_id,
            )
            child_coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry((_ProfileBundle(),)),
                owner_id="child-coordinator",
                requirement_scope="optional",
            )
            parent_coordinator.process_once(
                execution_id,
                limit=20,
                lease_seconds=30.0,
                now=102.0,
            )

            second_page = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="listing-worker",
                limit=1,
                lease_seconds=30.0,
                now=103.0,
            )[0]
            profile_invocation = repository.lease_ready_invocations(
                execution_id=enrichment_execution_id,
                owner_id="profile-worker",
                limit=1,
                lease_seconds=30.0,
                now=103.0,
            )[0]
            repository.commit_profile_result(
                profile_invocation,
                CompanyProfileResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=CompanyProfileOutput(
                        target_provider_id="hh",
                        profile_url="https://hh.ru/employer/10",
                        source_company_id="10",
                        company_name="Example",
                        description=None,
                        industry=None,
                        size_text=None,
                        locations=(),
                        official_site_url="https://example.com",
                        career_endpoints=(),
                        contacts=(),
                        social_links=(),
                    ),
                ),
                _profile_manifest(),
                now=104.0,
            )
            child_coordinator.process_once(
                enrichment_execution_id,
                limit=20,
                lease_seconds=30.0,
                now=105.0,
            )

            repository.commit_search_result(
                second_page,
                SearchListingResult(
                    outcome=SearchResultOutcome.SUCCESS,
                    items=(
                        replace(
                            _listing(),
                            source_listing_id="124",
                            vacancy_url="https://hh.ru/vacancy/124",
                            company=shared_company,
                        ),
                    ),
                    continuations=(),
                    collection_units_consumed=1,
                ),
                _search_manifest(),
                now=107.0,
            )
            parent_coordinator.process_once(
                execution_id,
                limit=20,
                lease_seconds=30.0,
                now=108.0,
            )
            child_coordinator.process_once(
                enrichment_execution_id,
                limit=20,
                lease_seconds=30.0,
                now=109.0,
            )

            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM listing_enrichment_requests "
                    "WHERE execution_id = ? AND status = 'satisfied'",
                    (enrichment_execution_id,),
                ),
                2,
            )
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM selection_evaluations "
                    "WHERE execution_id = ? AND stage = 'final' AND outcome = 'keep'",
                    (enrichment_execution_id,),
                ),
                2,
            )

    def test_discovered_career_endpoint_creates_an_independent_source_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            request = SearchRequest(query_variants=("QA",))
            execution_id = repository.create_execution(
                run_id="r-discovery",
                intent={"query_variants": ["QA"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
                discovery_plan_budget=1,
            )
            site_manifest = ParserManifest(
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
            )
            repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=None,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=site_manifest.ref,
                    parser_type=ParserType.COMPANY_SITE,
                    input_schema_id=site_manifest.input_schema_id,
                    parser_input=CompanySiteInput(site_url="https://example.com"),
                    task_class=TaskClass.SITE,
                    task_key="site-example",
                    available_at=0.0,
                    reserved_collection_units=None,
                )
            )
            site_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="site-worker",
                limit=1,
                lease_seconds=30.0,
                now=100.0,
            )[0]
            repository.commit_site_result(
                site_invocation,
                CompanySiteResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=CompanySiteOutput(
                        canonical_site_url="https://example.com",
                        company_name="Example",
                        contacts=(),
                        social_links=(),
                        career_endpoints=(
                            DiscoveredEndpoint(
                                kind="career_page",
                                url="https://example.com/careers",
                                provider_hint="web",
                                confidence="confirmed",
                                discovery_method="explicit_link",
                            ),
                            DiscoveredEndpoint(
                                kind="career_page",
                                url="https://example.com/jobs",
                                provider_hint="web",
                                confidence="confirmed",
                                discovery_method="explicit_link",
                            ),
                        ),
                    ),
                ),
                site_manifest,
                now=101.0,
            )
            coordinator = GraphCoordinator(
                repository=repository,
                registry=ParserRegistry((_DiscoveredSearchBundle(),)),
                owner_id="coordinator",
                request=request,
            )

            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=102.0)

            with closing(sqlite3.connect(database_path)) as connection:
                statuses = dict(
                    connection.execute(
                        "SELECT resolution_status, COUNT(*) FROM discovered_endpoints GROUP BY resolution_status"
                    ).fetchall()
                )
                discovery_plans_created = connection.execute(
                    "SELECT discovery_plans_created FROM search_executions"
                ).fetchone()[0]
            self.assertEqual(statuses, {"budget_exhausted": 1, "resolved": 1})
            self.assertEqual(discovery_plans_created, 1)
            self.assertEqual(self._scalar(database_path, "SELECT COUNT(*) FROM source_plans"), 1)
            self.assertEqual(
                self._scalar(
                    database_path,
                    "SELECT COUNT(*) FROM parser_invocations WHERE parser_type = 'search_listing'",
                ),
                1,
            )

    def test_production_ats_discovery_persists_input_derived_page_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "run.sqlite"
            repository = SqliteGraphRepository(database_path)
            self.addCleanup(repository.close)
            registry = build_independent_parser_registry(("hh_ru",))
            site_manifest = registry.manifest(ParserRef("web.company-site", "1.0"))
            request = SearchRequest(query_variants=("AI lead",))
            execution_id = repository.create_execution(
                run_id="r-production-discovery",
                intent={"query_variants": ["AI lead"]},
                append_sequence=0,
                policy_version="policy-v1",
                runtime_config_version="runtime-v1",
                active_runtime_budget_ms=1_000_000,
                discovery_plan_budget=1,
            )
            repository.enqueue_invocation(
                ParserInvocationSpec(
                    execution_id=execution_id,
                    source_plan_id=None,
                    parent_invocation_id=None,
                    cause_event_id=None,
                    parser_ref=site_manifest.ref,
                    parser_type=ParserType.COMPANY_SITE,
                    input_schema_id=site_manifest.input_schema_id,
                    parser_input=CompanySiteInput(site_url="https://example.com"),
                    task_class=TaskClass.SITE,
                    task_key="production-site-example",
                    available_at=0.0,
                    reserved_collection_units=None,
                )
            )
            site_invocation = repository.lease_ready_invocations(
                execution_id=execution_id,
                owner_id="site-worker",
                limit=1,
                lease_seconds=30.0,
                now=100.0,
            )[0]
            repository.commit_site_result(
                site_invocation,
                CompanySiteResult(
                    outcome=SingletonResultOutcome.SUCCESS,
                    item=CompanySiteOutput(
                        canonical_site_url="https://example.com",
                        company_name="Example",
                        contacts=(),
                        social_links=(),
                        career_endpoints=(
                            DiscoveredEndpoint(
                                kind="career_page",
                                url="https://jobs.lever.co/example-company",
                                provider_hint="ats:lever",
                                confidence="confirmed",
                                discovery_method="explicit_link",
                            ),
                        ),
                    ),
                ),
                site_manifest,
                now=101.0,
            )
            coordinator = GraphCoordinator(
                repository=repository,
                registry=registry,
                owner_id="coordinator",
                request=request,
            )

            coordinator.process_once(execution_id, limit=20, lease_seconds=30.0, now=102.0)

            with closing(sqlite3.connect(database_path)) as connection:
                row = connection.execute(
                    """
                    SELECT parser_id, input_json
                    FROM parser_invocations
                    WHERE parser_type = 'search_listing'
                    """
                ).fetchone()
            assert row is not None
            parser_input = json.loads(row[1])
            self.assertEqual(row[0], "ats.discovered.search")
            self.assertEqual(
                parser_input["cursor"]["request"]["url"],
                "https://api.lever.co/v0/postings/example-company?mode=json",
            )
            self.assertEqual(parser_input["source_id"], parser_input["target_provider_id"])

    @staticmethod
    def _scalar(
        database_path: Path,
        query: str,
        parameters: tuple[object, ...] = (),
    ) -> object:
        with closing(sqlite3.connect(database_path)) as connection:
            row = connection.execute(query, parameters).fetchone()
        if row is None:
            raise AssertionError("expected one row")
        return row[0]


if __name__ == "__main__":
    unittest.main()
