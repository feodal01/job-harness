"""Strict contracts for the next-generation job search core."""

from job_harness.v2.contracts.criteria import (
    SEARCH_CRITERION_DESCRIPTORS,
    SearchCriterionDescriptor,
    TextEnrichmentPolicy,
    all_search_criterion_descriptors,
    search_criterion_descriptor,
)
from job_harness.v2.contracts.enums import (
    ALL_SEARCH_CRITERIA,
    CriterionCapability,
    DescriptionAvailability,
    Grade,
    HttpMethod,
    ParserFixtureKind,
    ProcessingDecision,
    RetryNextAction,
    SearchCriterion,
    SourceOutcome,
    SourceType,
    TextExclusionMode,
    TextField,
    Transport,
)
from job_harness.v2.contracts.errors import ClassifiedSourceError
from job_harness.v2.contracts.fixtures import (
    ParserFixtureCase,
    ParserFixtureSuite,
    RequiredParserFixtures,
    SupportedSourceContract,
)
from job_harness.v2.contracts.records import (
    AttemptCounts,
    AttemptEvidence,
    CriteriaDiagnostics,
    RawListing,
    RawSearchRecord,
    RetryInfo,
    SourceAttemptRecord,
)
from job_harness.v2.contracts.scraper import (
    DetailEnrichmentScraper,
    SourceFetchRequest,
    SourceResponseArtifact,
    SourceScraper,
    SourceSearchParseResult,
)
from job_harness.v2.contracts.search import SearchRequest, TextExclusion
from job_harness.v2.contracts.source import CriterionDeclaration, SourceDescriptor

__all__ = [
    "AttemptCounts",
    "AttemptEvidence",
    "ALL_SEARCH_CRITERIA",
    "ClassifiedSourceError",
    "CriteriaDiagnostics",
    "CriterionCapability",
    "CriterionDeclaration",
    "DescriptionAvailability",
    "DetailEnrichmentScraper",
    "Grade",
    "HttpMethod",
    "ParserFixtureCase",
    "ParserFixtureKind",
    "RequiredParserFixtures",
    "ParserFixtureSuite",
    "ProcessingDecision",
    "RawListing",
    "RawSearchRecord",
    "RetryInfo",
    "RetryNextAction",
    "SEARCH_CRITERION_DESCRIPTORS",
    "SearchCriterion",
    "SearchCriterionDescriptor",
    "SearchRequest",
    "SourceAttemptRecord",
    "SourceDescriptor",
    "SourceFetchRequest",
    "SourceOutcome",
    "SourceResponseArtifact",
    "SourceScraper",
    "SourceSearchParseResult",
    "SourceType",
    "SupportedSourceContract",
    "TextExclusion",
    "TextExclusionMode",
    "TextEnrichmentPolicy",
    "TextField",
    "Transport",
    "all_search_criterion_descriptors",
    "search_criterion_descriptor",
]
