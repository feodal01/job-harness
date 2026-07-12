from __future__ import annotations

import unittest

from job_harness.v2.contracts import (
    CompanySiteInput,
    InvocationScope,
    ParserFailureKind,
    ParserManifest,
    ParserRef,
    ParserRegistry,
    ParserType,
    SearchListingInput,
    SearchListingResult,
    SearchResultOutcome,
    TransportKind,
)
from job_harness.v2.ports import HttpAction, HttpResponse, OperationContext, ParserRuntime
from job_harness.v2.runtime.executors import DirectScraperExecutor


def _manifest() -> ParserManifest:
    return ParserManifest(
        parser_id="test.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id="test.search.input.v1",
        output_schema_id="test.search.output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("test",),
        supported_url_patterns=(),
        output_facts=("title",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=("aggregator",),
        query_mode="per_query",
        collection_unit="page",
        native_criteria=("query",),
        default_unit_budget=2,
        default_item_budget=20,
        default_invocation_budget=3,
        max_units_per_invocation=1,
    )


def _input() -> SearchListingInput:
    return SearchListingInput(
        source_id="test",
        target_provider_id="test",
        queries=("QA",),
        target={"kind": "catalog"},
        cursor={"page": 0},
        native_filters={},
        resolved_state=None,
    )


class _Runtime(ParserRuntime):
    def __init__(self, context: OperationContext, reserved_collection_units: int) -> None:
        self.context = context
        self._reserved_collection_units = reserved_collection_units

    @property
    def reserved_collection_units(self) -> int:
        return self._reserved_collection_units

    async def http(self, _action: HttpAction) -> HttpResponse:
        raise AssertionError("network is not used by this fake bundle")


class _RuntimeFactory:
    def __init__(self) -> None:
        self.contexts: list[OperationContext] = []

    def create(self, context: OperationContext, *, reserved_collection_units: int) -> ParserRuntime:
        self.contexts.append(context)
        return _Runtime(context, reserved_collection_units)


class _SearchBundle:
    manifest = _manifest()
    input_type = SearchListingInput
    result_type = SearchListingResult

    def __init__(self, result: object) -> None:
        self.result = result
        self.inputs: list[SearchListingInput] = []

    def plan_initial(self, _intent: object, _target: object) -> tuple[SearchListingInput, ...]:
        return ()

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> object:
        self.inputs.append(parser_input)
        self.runtime = runtime
        return self.result


class DirectScraperExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_executes_bundle_without_managed_persistence_context(self) -> None:
        bundle = _SearchBundle(
            SearchListingResult(
                outcome=SearchResultOutcome.NO_RESULTS,
                items=(),
                continuations=(),
                collection_units_consumed=1,
            )
        )
        runtime_factory = _RuntimeFactory()
        executor = DirectScraperExecutor(
            registry=ParserRegistry((bundle,)),
            runtime_factory=runtime_factory,
        )

        execution = await executor.execute(bundle.manifest.ref, _input())

        self.assertIsNotNone(execution.result)
        self.assertIsNone(execution.failure)
        self.assertEqual(bundle.inputs, [_input()])
        self.assertEqual(len(runtime_factory.contexts), 1)
        self.assertIsNone(runtime_factory.contexts[0].execution_id)
        self.assertIsNone(runtime_factory.contexts[0].invocation_id)
        self.assertTrue(runtime_factory.contexts[0].operation_id)

    async def test_wrong_input_fails_before_bundle_execution(self) -> None:
        bundle = _SearchBundle(
            SearchListingResult(
                outcome=SearchResultOutcome.NO_RESULTS,
                items=(),
                continuations=(),
                collection_units_consumed=1,
            )
        )
        executor = DirectScraperExecutor(
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
        )

        execution = await executor.execute(
            ParserRef("test.search", "1.0"),
            CompanySiteInput(site_url="https://example.com"),
        )

        self.assertIsNone(execution.result)
        failure = execution.failure
        if failure is None:
            self.fail("expected invalid-input failure")
        self.assertEqual(failure.kind, ParserFailureKind.INVALID_INPUT)
        self.assertEqual(bundle.inputs, [])

    async def test_wrong_result_type_is_invalid_output(self) -> None:
        bundle = _SearchBundle(object())
        executor = DirectScraperExecutor(
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
        )

        execution = await executor.execute(bundle.manifest.ref, _input())

        self.assertIsNone(execution.result)
        failure = execution.failure
        if failure is None:
            self.fail("expected invalid-output failure")
        self.assertEqual(failure.kind, ParserFailureKind.INVALID_OUTPUT)

    async def test_stateless_bundle_cannot_report_multiple_consumed_units(self) -> None:
        result = SearchListingResult(
            outcome=SearchResultOutcome.NO_RESULTS,
            items=(),
            continuations=(),
            collection_units_consumed=2,
        )
        bundle = _SearchBundle(result)
        executor = DirectScraperExecutor(
            registry=ParserRegistry((bundle,)),
            runtime_factory=_RuntimeFactory(),
        )

        execution = await executor.execute(bundle.manifest.ref, _input())

        self.assertIsNone(execution.result)
        failure = execution.failure
        if failure is None:
            self.fail("expected invalid-output failure")
        self.assertEqual(failure.kind, ParserFailureKind.INVALID_OUTPUT)


if __name__ == "__main__":
    unittest.main()
