from __future__ import annotations

import unittest
from dataclasses import replace

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanyProfileOutput,
    CompanyProfileResult,
    CompanyRef,
    CompanySiteInput,
    CompanySiteResult,
    InvocationScope,
    ParserManifest,
    ParserRef,
    ParserRegistry,
    ParserType,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchResultOutcome,
    SingletonResultOutcome,
    SourceLocation,
    TargetParserResolver,
    TransportKind,
    VacancyDetailInput,
    VacancyDetailResult,
)


def _manifest(
    *,
    parser_id: str = "hh.search",
    parser_type: ParserType = ParserType.SEARCH_LISTING,
    invocation_scope: InvocationScope = InvocationScope.STATELESS_UNIT,
    max_units_per_invocation: int = 1,
    patterns: tuple[str, ...] = (r"https://hh\.ru/.*",),
    is_fallback: bool = False,
) -> ParserManifest:
    return ParserManifest(
        parser_id=parser_id,
        parser_type=parser_type,
        implementation_version="1.0",
        input_schema_id=f"{parser_id}.input.v1",
        output_schema_id=f"{parser_id}.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("hh",),
        supported_url_patterns=patterns,
        output_facts=("title", "vacancyUrl"),
        invocation_scope=invocation_scope,
        source_kinds=("aggregator",) if parser_type == ParserType.SEARCH_LISTING else (),
        query_mode="per_query" if parser_type == ParserType.SEARCH_LISTING else None,
        collection_unit="page" if parser_type == ParserType.SEARCH_LISTING else None,
        native_criteria=("query",) if parser_type == ParserType.SEARCH_LISTING else (),
        default_unit_budget=5 if parser_type == ParserType.SEARCH_LISTING else None,
        default_item_budget=100 if parser_type == ParserType.SEARCH_LISTING else None,
        default_invocation_budget=6 if parser_type == ParserType.SEARCH_LISTING else None,
        max_units_per_invocation=max_units_per_invocation,
        is_fallback=is_fallback,
    )


def _listing() -> SearchListingOutput:
    return SearchListingOutput(
        source_id="hh_ru",
        target_provider_id="hh",
        source_listing_id="123",
        title="QA Engineer",
        company=CompanyRef(name="Example"),
        location=SourceLocation(text="Moscow"),
        salary=None,
        work_formats=("hybrid",),
        remote_scopes=(),
        native_grade=None,
        posted_at=None,
        vacancy_url="https://hh.ru/vacancy/123",
        apply_url=None,
        summary=None,
    )


class ParserManifestTest(unittest.TestCase):
    def test_stateless_listing_parser_consumes_at_most_one_unit_per_invocation(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_units_per_invocation must be 1"):
            _manifest(max_units_per_invocation=2)

    def test_session_batch_is_reserved_for_listing_parsers(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_batch is only valid for search_listing"):
            _manifest(
                parser_id="hh.detail",
                parser_type=ParserType.VACANCY_DETAIL,
                invocation_scope=InvocationScope.SESSION_BATCH,
                max_units_per_invocation=2,
            )

    def test_non_listing_manifest_rejects_listing_planning_fields(self) -> None:
        manifest = _manifest(parser_id="hh.detail", parser_type=ParserType.VACANCY_DETAIL)
        with self.assertRaisesRegex(ValueError, "listing planning fields"):
            replace(manifest, query_mode="per_query")


class ParserInputTest(unittest.TestCase):
    def test_inputs_contain_only_parser_business_target_state(self) -> None:
        search = SearchListingInput(
            source_id="hh_ru",
            target_provider_id="hh",
            queries=("QA Engineer",),
            target={"kind": "catalog"},
            cursor={"page": 0},
            native_filters={"area": "113"},
            resolved_state=None,
        )
        detail = VacancyDetailInput(
            target_provider_id="hh",
            vacancy_url="https://hh.ru/vacancy/123",
            source_listing_id="123",
        )
        profile = CompanyProfileInput(
            target_provider_id="hh",
            company_profile_url="https://hh.ru/employer/10",
            source_company_id="10",
        )
        site = CompanySiteInput(site_url="https://example.com/careers")

        self.assertEqual(search.cursor, {"page": 0})
        self.assertEqual(detail.source_listing_id, "123")
        self.assertEqual(profile.source_company_id, "10")
        self.assertEqual(site.site_url, "https://example.com/careers")


class ParserResultTest(unittest.TestCase):
    def test_no_results_cannot_hide_items_or_continuations(self) -> None:
        continuation = SearchListingInput(
            source_id="hh_ru",
            target_provider_id="hh",
            queries=("QA",),
            target={"kind": "catalog"},
            cursor={"page": 1},
            native_filters={},
            resolved_state=None,
        )
        with self.assertRaisesRegex(ValueError, "no_results"):
            SearchListingResult(
                outcome=SearchResultOutcome.NO_RESULTS,
                items=(_listing(),),
                continuations=(continuation,),
                collection_units_consumed=1,
            )

    def test_zero_unit_bootstrap_requires_a_continuation_and_no_items(self) -> None:
        with self.assertRaisesRegex(ValueError, "zero-unit bootstrap"):
            SearchListingResult(
                outcome=SearchResultOutcome.SUCCESS,
                items=(_listing(),),
                continuations=(),
                collection_units_consumed=0,
            )

    def test_singleton_result_states_cannot_be_mixed(self) -> None:
        output = CompanyProfileOutput(
            target_provider_id="hh",
            profile_url="https://hh.ru/employer/10",
            source_company_id="10",
            company_name="Example",
            description=None,
            industry=None,
            size_text=None,
            locations=(),
            official_site_url=None,
            career_endpoints=(),
            contacts=(),
            social_links=(),
        )
        with self.assertRaisesRegex(ValueError, "not_found requires item=None"):
            CompanyProfileResult(outcome=SingletonResultOutcome.NOT_FOUND, item=output)

        self.assertIsNone(
            VacancyDetailResult(outcome=SingletonResultOutcome.NOT_FOUND, item=None).item
        )
        self.assertIsNone(CompanySiteResult(outcome=SingletonResultOutcome.NOT_FOUND, item=None).item)


class ParserRegistryTest(unittest.TestCase):
    def test_exact_lookup_is_pinned_by_id_and_version(self) -> None:
        parser = _Bundle(_manifest())
        registry = ParserRegistry((parser,))

        self.assertIs(registry.get(ParserRef("hh.search", "1.0")), parser)
        with self.assertRaisesRegex(KeyError, "hh.search@2.0"):
            registry.get(ParserRef("hh.search", "2.0"))

    def test_registry_has_no_implicit_target_routing(self) -> None:
        registry = ParserRegistry((_Bundle(_manifest()),))

        self.assertFalse(hasattr(registry, "resolve_target"))

    def test_target_resolution_rejects_ambiguous_specific_matches(self) -> None:
        resolver = TargetParserResolver(
            (
                _manifest(parser_id="hh.detail.a", parser_type=ParserType.VACANCY_DETAIL),
                _manifest(parser_id="hh.detail.b", parser_type=ParserType.VACANCY_DETAIL),
            )
        )

        resolution = resolver.resolve(
            ParserType.VACANCY_DETAIL,
            "hh",
            "https://hh.ru/vacancy/123",
        )
        self.assertEqual(resolution.kind, "ambiguous_target")
        self.assertEqual(
            resolution.candidate_refs,
            (ParserRef("hh.detail.a", "1.0"), ParserRef("hh.detail.b", "1.0")),
        )

    def test_target_resolution_prefers_provider_specific_parser_over_fallback(self) -> None:
        resolver = TargetParserResolver(
            (
                _manifest(
                    parser_id="web.fallback",
                    parser_type=ParserType.COMPANY_SITE,
                    patterns=(r"https://.+",),
                    is_fallback=True,
                ),
                replace(
                    _manifest(
                        parser_id="acme.site",
                        parser_type=ParserType.COMPANY_SITE,
                        patterns=(r"https://careers\.acme\.test/.*",),
                    ),
                    provider_ids=("acme",),
                ),
            )
        )

        resolution = resolver.resolve(
            ParserType.COMPANY_SITE,
            "acme",
            "https://careers.acme.test/jobs",
        )

        self.assertEqual("resolved", resolution.kind)
        self.assertEqual(ParserRef("acme.site", "1.0"), resolution.parser_ref)

    def test_target_resolution_uses_declared_fallback_only_without_specific_match(self) -> None:
        resolver = TargetParserResolver(
            (
                _manifest(
                    parser_id="web.fallback",
                    parser_type=ParserType.COMPANY_SITE,
                    patterns=(r"https://.+",),
                    is_fallback=True,
                ),
                replace(
                    _manifest(
                        parser_id="acme.site",
                        parser_type=ParserType.COMPANY_SITE,
                        patterns=(r"https://careers\.acme\.test/.*",),
                    ),
                    provider_ids=("acme",),
                ),
            )
        )

        resolution = resolver.resolve(
            ParserType.COMPANY_SITE,
            "other",
            "https://other.test/careers",
        )

        self.assertEqual("resolved", resolution.kind)
        self.assertEqual(ParserRef("web.fallback", "1.0"), resolution.parser_ref)

    def test_registration_requires_a_complete_self_contained_bundle(self) -> None:
        with self.assertRaisesRegex(TypeError, "input_type"):
            ParserRegistry((_IncompleteBundle(_manifest()),))

    def test_registration_rejects_contract_types_that_disagree_with_manifest(self) -> None:
        bundle = _Bundle(_manifest())
        bundle.input_type = CompanySiteInput

        with self.assertRaisesRegex(TypeError, "contract types"):
            ParserRegistry((bundle,))


class _Bundle:
    def __init__(self, manifest: ParserManifest) -> None:
        self.manifest = manifest
        self.input_type: type[object]
        self.result_type: type[object]
        if manifest.parser_type == ParserType.SEARCH_LISTING:
            self.input_type = SearchListingInput
            self.result_type = SearchListingResult
        else:
            self.input_type = VacancyDetailInput
            self.result_type = VacancyDetailResult

    def plan_initial(self, _intent: object, _target: object) -> tuple[SearchListingInput, ...]:
        return ()

    async def execute(self, parser_input: object, runtime: object) -> object:
        raise NotImplementedError


class _IncompleteBundle:
    def __init__(self, manifest: ParserManifest) -> None:
        self.manifest = manifest


if __name__ == "__main__":
    unittest.main()
