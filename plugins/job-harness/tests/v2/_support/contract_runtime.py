from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from job_harness.v2.contracts import (
    ALL_SEARCH_CRITERIA,
    AttemptEvidence,
    CriterionCapability,
    ParserFixtureCase,
    ParserFixtureKind,
    ParserFixtureSuite,
    RawListing,
    RequiredParserFixtures,
    SearchCriterion,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
    SourceSearchParseResult,
    SourceType,
    Transport,
)
from job_harness.v2.runtime import SupportedSource


def descriptor(
    source_id: str,
    *,
    source_type: SourceType = SourceType.AGGREGATOR,
    countries: tuple[str, ...] = ("RU",),
    source_limit: int = 10,
) -> SourceDescriptor:
    capabilities = dict.fromkeys(ALL_SEARCH_CRITERIA, CriterionCapability.UNSUPPORTED)
    capabilities[SearchCriterion.QUERY] = CriterionCapability.NATIVE_REQUEST
    return SourceDescriptor.from_capabilities(
        source_id=source_id,
        source_type=source_type,
        transport=Transport.HTTP,
        countries=countries,
        source_limit=source_limit,
        capabilities=capabilities,
    )


def listing(source_id: str, suffix: str = "1") -> RawListing:
    return RawListing(
        source_listing_id=suffix,
        title=f"QA Engineer {suffix}",
        url=f"https://example.test/{source_id}/jobs/{suffix}",
        source=source_id,
        company="Acme",
        description="Real parser fixtures own full description coverage.",
    )


def fixture_suite(source_id: str) -> ParserFixtureSuite:
    return ParserFixtureSuite(
        source_id=source_id,
        cases=(
            ParserFixtureCase(
                name="success-non-empty",
                kind=ParserFixtureKind.SUCCESS_NON_EMPTY,
                captured_artifact_path=f"tests/v2/fixtures/scrapers/{source_id}/success/response.html",
                metadata_path=f"tests/v2/fixtures/scrapers/{source_id}/success/meta.json",
                golden_path=f"tests/v2/fixtures/scrapers/{source_id}/success/expected.raw.json",
                real_capture=True,
                golden_reviewed_by="maintainer",
            ),
        ),
    )


@dataclass
class FakeScraper(SourceScraper):
    source_descriptor: SourceDescriptor
    outcome: SourceOutcome = SourceOutcome.SUCCESS
    raw_listings: tuple[RawListing, ...] = ()
    parse_error: Exception | None = None

    @property
    def descriptor(self) -> SourceDescriptor:
        return self.source_descriptor

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return RequiredParserFixtures()

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=f"https://example.test/{self.descriptor.source_id}/search?q={query_variant}",
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        _response: SourceResponseArtifact,
        _request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        if self.parse_error is not None:
            raise self.parse_error
        if self.outcome == SourceOutcome.NO_RESULTS:
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )
        return SourceSearchParseResult(
            outcome=self.outcome,
            listings=self.raw_listings,
        )


def supported(scraper: SourceScraper) -> SupportedSource:
    return SupportedSource(
        scraper=scraper,
        fixture_suite=fixture_suite(scraper.descriptor.source_id),
    )


@dataclass
class FakeFetcher:
    delays: dict[tuple[str, str], float] = field(default_factory=dict)
    failures: dict[tuple[str, str], list[BaseException]] = field(default_factory=dict)
    calls: list[SourceFetchRequest] = field(default_factory=list)

    async def fetch(self, request: SourceFetchRequest) -> SourceResponseArtifact:
        self.calls.append(request)
        key = (request.source_id, request.query_variant)
        if delay := self.delays.get(key):
            await asyncio.sleep(delay)
        failures = self.failures.get(key)
        if failures:
            failure = failures.pop(0)
            raise failure
        return SourceResponseArtifact(
            source_id=request.source_id,
            url=request.url,
            media_type="text/html",
            body="<html></html>",
        )
