from __future__ import annotations

import unittest

from job_harness.v2.contracts import (
    CriterionCapability,
    CriterionDeclaration,
    ParserFixtureCase,
    ParserFixtureKind,
    ParserFixtureSuite,
    RequiredParserFixtures,
    SearchCriterion,
    SourceDescriptor,
    SourceType,
    SupportedSourceContract,
    Transport,
)
from job_harness.v2.contracts.enums import ALL_SEARCH_CRITERIA

_CONTRACT_TEST_SOURCE_ID = "contract_test_source"


def _criteria(
    capability: CriterionCapability = CriterionCapability.UNSUPPORTED,
) -> tuple[CriterionDeclaration, ...]:
    return tuple(
        CriterionDeclaration(criterion, capability)
        for criterion in ALL_SEARCH_CRITERIA
    )


def _descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        source_id=_CONTRACT_TEST_SOURCE_ID,
        source_type=SourceType.AGGREGATOR,
        transport=Transport.HTTP,
        countries=("ru",),
        source_limit=100,
        criteria=_criteria(),
    )


def _fixture_case(kind: ParserFixtureKind = ParserFixtureKind.SUCCESS_NON_EMPTY) -> ParserFixtureCase:
    return ParserFixtureCase(
        name=f"{kind.value}-case",
        kind=kind,
        captured_artifact_path=f"tests/v2/fixtures/scrapers/{_CONTRACT_TEST_SOURCE_ID}/{kind.value}/response.html",
        metadata_path=f"tests/v2/fixtures/scrapers/{_CONTRACT_TEST_SOURCE_ID}/{kind.value}/meta.json",
        golden_path=f"tests/v2/fixtures/scrapers/{_CONTRACT_TEST_SOURCE_ID}/{kind.value}/expected.raw.json",
        real_capture=True,
        golden_reviewed_by="maintainer",
    )


class SourceDescriptorTest(unittest.TestCase):
    def test_requires_exactly_one_declaration_per_search_criterion(self) -> None:
        # Arrange
        incomplete = tuple(
            CriterionDeclaration(criterion, CriterionCapability.UNSUPPORTED)
            for criterion in ALL_SEARCH_CRITERIA
            if criterion != SearchCriterion.VACANCY_GEOGRAPHIES
        )

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "missing: vacancy_geographies"):
            SourceDescriptor(
                source_id="hh_ru",
                source_type=SourceType.AGGREGATOR,
                transport=Transport.HTTP,
                countries=("RU",),
                source_limit=100,
                criteria=incomplete,
            )

    def test_rejects_invalid_source_id_and_limit(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "source_id"):
            SourceDescriptor(
                source_id="HH RU",
                source_type=SourceType.AGGREGATOR,
                transport=Transport.HTTP,
                countries=("RU",),
                source_limit=100,
                criteria=_criteria(),
            )
        with self.assertRaisesRegex(ValueError, "source_limit"):
            SourceDescriptor(
                source_id="hh_ru",
                source_type=SourceType.AGGREGATOR,
                transport=Transport.HTTP,
                countries=("RU",),
                source_limit=0,
                criteria=_criteria(),
            )

    def test_exposes_capability_groups(self) -> None:
        # Arrange
        capabilities = dict.fromkeys(ALL_SEARCH_CRITERIA, CriterionCapability.UNSUPPORTED)
        capabilities[SearchCriterion.QUERY] = CriterionCapability.NATIVE_REQUEST
        capabilities[SearchCriterion.PUBLISHED_SINCE] = CriterionCapability.STRUCTURED_OUTPUT

        # Act
        descriptor = SourceDescriptor.from_capabilities(
            source_id="hh_ru",
            source_type=SourceType.AGGREGATOR,
            transport=Transport.HTTP,
            countries=("RU",),
            source_limit=100,
            capabilities=capabilities,
        )

        # Assert
        self.assertEqual(
            CriterionCapability.NATIVE_REQUEST,
            descriptor.capability_for(SearchCriterion.QUERY),
        )
        self.assertEqual({SearchCriterion.QUERY}, descriptor.native_request_criteria)
        self.assertEqual(
            {SearchCriterion.PUBLISHED_SINCE},
            descriptor.structured_output_criteria,
        )
        self.assertIn(SearchCriterion.VACANCY_GEOGRAPHIES, descriptor.unsupported_criteria)


class ParserFixtureContractTest(unittest.TestCase):
    def test_rejects_generated_source_response_as_parser_fixture(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "real captured"):
            ParserFixtureCase(
                name="generated",
                kind=ParserFixtureKind.SUCCESS_NON_EMPTY,
                captured_artifact_path="generated.html",
                metadata_path="meta.json",
                golden_path="expected.raw.json",
                real_capture=False,
                golden_reviewed_by="maintainer",
            )

    def test_rejects_non_human_golden_answer(self) -> None:
        # Arrange / Act / Assert
        with self.assertRaisesRegex(ValueError, "manually reviewed"):
            ParserFixtureCase(
                name="llm",
                kind=ParserFixtureKind.SUCCESS_NON_EMPTY,
                captured_artifact_path="response.html",
                metadata_path="meta.json",
                golden_path="expected.raw.json",
                real_capture=True,
                golden_reviewed_by="llm",
            )

    def test_supported_source_requires_complete_fixture_suite(self) -> None:
        # Arrange
        requirements = RequiredParserFixtures(
            no_results=True,
            pagination=True,
            detail=True,
        )
        incomplete_suite = ParserFixtureSuite(
            source_id=_CONTRACT_TEST_SOURCE_ID,
            cases=(_fixture_case(ParserFixtureKind.SUCCESS_NON_EMPTY),),
        )

        # Act / Assert
        with self.assertRaisesRegex(ValueError, "detail"):
            SupportedSourceContract(
                descriptor=_descriptor(),
                required_fixture_kinds=requirements,
                fixture_suite=incomplete_suite,
            )

    def test_supported_source_accepts_complete_fixture_suite(self) -> None:
        # Arrange
        requirements = RequiredParserFixtures(no_results=True, detail=True)
        suite = ParserFixtureSuite(
            source_id=_CONTRACT_TEST_SOURCE_ID,
            cases=(
                _fixture_case(ParserFixtureKind.SUCCESS_NON_EMPTY),
                _fixture_case(ParserFixtureKind.NO_RESULTS),
                _fixture_case(ParserFixtureKind.DETAIL),
            ),
        )

        # Act
        contract = SupportedSourceContract(
            descriptor=_descriptor(),
            required_fixture_kinds=requirements,
            fixture_suite=suite,
        )

        # Assert
        self.assertEqual(_CONTRACT_TEST_SOURCE_ID, contract.descriptor.source_id)

    def test_supported_source_accepts_empty_suite_when_no_fixtures_are_required(self) -> None:
        # Arrange
        requirements = RequiredParserFixtures(success_non_empty=False)
        suite = ParserFixtureSuite(source_id=_CONTRACT_TEST_SOURCE_ID, cases=())

        # Act
        contract = SupportedSourceContract(
            descriptor=_descriptor(),
            required_fixture_kinds=requirements,
            fixture_suite=suite,
        )

        # Assert
        self.assertEqual(_CONTRACT_TEST_SOURCE_ID, contract.descriptor.source_id)


if __name__ == "__main__":
    unittest.main()
