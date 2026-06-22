"""Parser fixture contracts for supported sources."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts.enums import ParserFixtureKind
from job_harness.v2.contracts.source import SourceDescriptor


@dataclass(frozen=True)
class RequiredParserFixtures:
    no_results: bool = False
    pagination: bool = False
    detail: bool = False
    optional_fields: bool = False
    blocked: bool = False
    rate_limited: bool = False
    login: bool = False
    geo_blocked: bool = False
    malformed_source: bool = False

    @property
    def required_kinds(self) -> frozenset[ParserFixtureKind]:
        kinds = {ParserFixtureKind.SUCCESS_NON_EMPTY}
        if self.no_results:
            kinds.add(ParserFixtureKind.NO_RESULTS)
        if self.pagination:
            kinds.add(ParserFixtureKind.PAGINATION)
        if self.detail:
            kinds.add(ParserFixtureKind.DETAIL)
        if self.optional_fields:
            kinds.add(ParserFixtureKind.OPTIONAL_FIELDS)
        if self.blocked:
            kinds.add(ParserFixtureKind.BLOCKED)
        if self.rate_limited:
            kinds.add(ParserFixtureKind.RATE_LIMITED)
        if self.login:
            kinds.add(ParserFixtureKind.LOGIN)
        if self.geo_blocked:
            kinds.add(ParserFixtureKind.GEO_BLOCKED)
        if self.malformed_source:
            kinds.add(ParserFixtureKind.MALFORMED_SOURCE)
        return frozenset(kinds)


@dataclass(frozen=True)
class ParserFixtureCase:
    name: str
    kind: ParserFixtureKind
    captured_artifact_path: str
    metadata_path: str
    golden_path: str
    real_capture: bool
    golden_reviewed_by: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("fixture name must be non-empty")
        if not self.captured_artifact_path.strip():
            raise ValueError("captured_artifact_path must be non-empty")
        if not self.metadata_path.strip():
            raise ValueError("metadata_path must be non-empty")
        if not self.golden_path.strip():
            raise ValueError("golden_path must be non-empty")
        if not self.real_capture:
            raise ValueError("parser fixtures must be real captured source artifacts")
        reviewer = self.golden_reviewed_by.strip()
        if not reviewer:
            raise ValueError("golden_reviewed_by must identify the human reviewer")
        if reviewer.casefold() in {"parser", "llm", "model", "estimator"}:
            raise ValueError("golden answers must be manually reviewed by a human")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "golden_reviewed_by", reviewer)


@dataclass(frozen=True)
class ParserFixtureSuite:
    source_id: str
    cases: tuple[ParserFixtureCase, ...]

    def __post_init__(self) -> None:
        source_id = self.source_id.strip()
        if not source_id:
            raise ValueError("source_id must be non-empty")
        if not self.cases:
            raise ValueError("fixture suite must contain at least one case")
        object.__setattr__(self, "source_id", source_id)

    @property
    def kinds(self) -> frozenset[ParserFixtureKind]:
        return frozenset(case.kind for case in self.cases)

    def missing_required_kinds(
        self,
        requirements: RequiredParserFixtures,
    ) -> frozenset[ParserFixtureKind]:
        return requirements.required_kinds - self.kinds


@dataclass(frozen=True)
class SupportedSourceContract:
    descriptor: SourceDescriptor
    required_fixture_kinds: RequiredParserFixtures
    fixture_suite: ParserFixtureSuite

    def __post_init__(self) -> None:
        if self.fixture_suite.source_id != self.descriptor.source_id:
            raise ValueError("fixture suite source_id must match descriptor source_id")
        missing = self.fixture_suite.missing_required_kinds(self.required_fixture_kinds)
        if missing:
            names = ", ".join(sorted(kind.value for kind in missing))
            raise ValueError(f"supported source is missing required parser fixtures: {names}")
