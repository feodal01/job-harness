"""Typed contracts for independently callable scraper bundles."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, cast
from urllib.parse import urlsplit

from job_harness.v2.contracts.json_types import JsonObject
from job_harness.v2.contracts.search import SearchRequest


class ParserType(StrEnum):
    SEARCH_LISTING = "search_listing"
    VACANCY_DETAIL = "vacancy_detail"
    COMPANY_PROFILE = "company_profile"
    COMPANY_SITE = "company_site"


class InvocationScope(StrEnum):
    STATELESS_UNIT = "stateless_unit"
    SESSION_BATCH = "session_batch"


class TransportKind(StrEnum):
    HTTP = "http"
    BROWSER = "browser"
    HYBRID = "hybrid"


class SearchResultOutcome(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    PARTIAL_SUCCESS = "partial_success"


class SingletonResultOutcome(StrEnum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"


class ParserFailureKind(StrEnum):
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    SOURCE_TIMEOUT = "source_timeout"
    HTTP_CLIENT_ERROR = "http_client_error"
    HTTP_SERVER_ERROR = "http_server_error"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    INVALID_INPUT = "invalid_input"
    INVALID_SOURCE_OUTPUT = "invalid_source_output"
    IMPLEMENTATION_UNAVAILABLE = "implementation_unavailable"
    RESOURCE_FAILURE = "resource_failure"
    UNSUPPORTED_TARGET = "unsupported_target"


@dataclass(frozen=True, order=True)
class ParserRef:
    parser_id: str
    implementation_version: str

    def __post_init__(self) -> None:
        _require_text(self.parser_id, "parser_id")
        _require_text(self.implementation_version, "implementation_version")


@dataclass(frozen=True)
class ParserManifest:
    parser_id: str
    parser_type: ParserType
    implementation_version: str
    input_schema_id: str
    output_schema_id: str
    transport: TransportKind
    provider_ids: tuple[str, ...]
    supported_url_patterns: tuple[str, ...]
    output_facts: tuple[str, ...]
    invocation_scope: InvocationScope
    source_kinds: tuple[str, ...] = ()
    query_mode: str | None = None
    collection_unit: str | None = None
    native_criteria: tuple[str, ...] = ()
    default_unit_budget: int | None = None
    default_item_budget: int | None = None
    default_invocation_budget: int | None = None
    max_units_per_invocation: int = 1
    is_fallback: bool = False

    def __post_init__(self) -> None:
        self._validate_common_fields()
        self._validate_scope()
        self._validate_planning_fields()

    def _validate_common_fields(self) -> None:
        for value, name in (
            (self.parser_id, "parser_id"),
            (self.implementation_version, "implementation_version"),
            (self.input_schema_id, "input_schema_id"),
            (self.output_schema_id, "output_schema_id"),
        ):
            _require_text(value, name)
        if not self.provider_ids or any(not value.strip() for value in self.provider_ids):
            raise ValueError("provider_ids must contain non-empty values")
        for pattern in self.supported_url_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid supported URL pattern: {pattern}") from exc
        if self.max_units_per_invocation < 1:
            raise ValueError("max_units_per_invocation must be >= 1")

    def _validate_scope(self) -> None:
        is_listing = self.parser_type == ParserType.SEARCH_LISTING
        if is_listing and self.is_fallback:
            raise ValueError("search_listing parsers cannot be target fallbacks")
        if self.invocation_scope == InvocationScope.SESSION_BATCH and not is_listing:
            raise ValueError("session_batch is only valid for search_listing parsers")
        if self.invocation_scope == InvocationScope.STATELESS_UNIT and self.max_units_per_invocation != 1:
            raise ValueError("max_units_per_invocation must be 1 for stateless_unit")

    def _validate_planning_fields(self) -> None:
        is_listing = self.parser_type == ParserType.SEARCH_LISTING
        listing_fields = (
            self.source_kinds,
            self.query_mode,
            self.collection_unit,
            self.native_criteria,
            self.default_unit_budget,
            self.default_item_budget,
            self.default_invocation_budget,
        )
        if is_listing:
            if not self.source_kinds or self.query_mode not in {"per_query", "query_group", "downstream_only"}:
                raise ValueError("search_listing requires source kinds and a valid query mode")
            if self.collection_unit not in {"page", "cursor_batch"}:
                raise ValueError("search_listing requires a valid collection unit")
            for value, name in (
                (self.default_unit_budget, "default_unit_budget"),
                (self.default_item_budget, "default_item_budget"),
                (self.default_invocation_budget, "default_invocation_budget"),
            ):
                if value is None or value < 1:
                    raise ValueError(f"{name} must be >= 1")
        elif any(value not in ((), None) for value in listing_fields):
            raise ValueError("non-listing parser cannot declare listing planning fields")

    @property
    def ref(self) -> ParserRef:
        return ParserRef(self.parser_id, self.implementation_version)

    def as_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class SearchListingInput:
    source_id: str
    target_provider_id: str
    queries: tuple[str, ...]
    target: JsonObject
    cursor: JsonObject
    native_filters: JsonObject
    resolved_state: JsonObject | None

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id")
        _require_text(self.target_provider_id, "target_provider_id")
        if not self.queries or any(not query.strip() for query in self.queries):
            raise ValueError("queries must contain non-empty values")
        kind = self.target.get("kind")
        if kind not in {"catalog", "discovered_url"}:
            raise ValueError("target.kind must be catalog or discovered_url")
        if kind == "discovered_url":
            _require_http_url(self.target.get("url"), "target.url")


@dataclass(frozen=True)
class VacancyDetailInput:
    target_provider_id: str
    vacancy_url: str
    source_listing_id: str | None

    def __post_init__(self) -> None:
        _require_text(self.target_provider_id, "target_provider_id")
        _require_http_url(self.vacancy_url, "vacancy_url")


@dataclass(frozen=True)
class CompanyProfileInput:
    target_provider_id: str
    company_profile_url: str
    source_company_id: str | None

    def __post_init__(self) -> None:
        _require_text(self.target_provider_id, "target_provider_id")
        _require_http_url(self.company_profile_url, "company_profile_url")


@dataclass(frozen=True)
class CompanySiteInput:
    site_url: str

    def __post_init__(self) -> None:
        _require_http_url(self.site_url, "site_url")


type ParserInput = SearchListingInput | VacancyDetailInput | CompanyProfileInput | CompanySiteInput


@dataclass(frozen=True)
class CompanyRef:
    name: str | None = None
    target_provider_id: str | None = None
    source_company_id: str | None = None
    profile_url: str | None = None
    official_site_url: str | None = None
    source_vacancies_url: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.profile_url, "profile_url"),
            (self.official_site_url, "official_site_url"),
            (self.source_vacancies_url, "source_vacancies_url"),
        ):
            if value is not None:
                _require_http_url(value, name)


@dataclass(frozen=True)
class SourceLocation:
    text: str | None = None
    cities: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        text = self.text.strip() if self.text is not None else None
        cities = _clean_values(self.cities)
        countries = tuple(value.upper() for value in _clean_values(self.countries))
        regions = tuple(value.upper() for value in _clean_values(self.regions))
        if not any((text, cities, countries, regions)):
            raise ValueError("source location requires explicit evidence")
        if any(not re.fullmatch(r"[A-Z]{2}", value) for value in countries):
            raise ValueError("location countries must be ISO 3166-1 alpha-2 codes")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "cities", cities)
        object.__setattr__(self, "countries", countries)
        object.__setattr__(self, "regions", regions)


@dataclass(frozen=True)
class SalaryRange:
    salary_from: int | None
    salary_to: int | None
    currency: str | None
    gross: bool | None
    period: str | None

    def __post_init__(self) -> None:
        if self.salary_from is not None and self.salary_from < 0:
            raise ValueError("salary_from must be >= 0")
        if self.salary_to is not None and self.salary_to < 0:
            raise ValueError("salary_to must be >= 0")
        if self.currency is not None:
            currency = self.currency.strip().upper()
            if currency == "RUR":
                currency = "RUB"
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("currency must be an ISO 4217 alpha-3 code")
            object.__setattr__(self, "currency", currency)
        if self.gross is not None and not isinstance(self.gross, bool):
            raise ValueError("gross must be boolean when provided")
        if self.period not in {None, "hour", "day", "month", "year"}:
            raise ValueError("invalid salary period")


@dataclass(frozen=True)
class RemoteScope:
    kind: str
    code: str | None

    def __post_init__(self) -> None:
        if self.kind not in {"country", "region", "worldwide"}:
            raise ValueError("invalid remote scope kind")
        if self.kind == "worldwide" and self.code is not None:
            raise ValueError("worldwide scope cannot have a code")
        if self.kind != "worldwide" and not self.code:
            raise ValueError("country and region scopes require a code")


@dataclass(frozen=True)
class ApplicationChannel:
    kind: str
    value: str
    label: str | None = None


@dataclass(frozen=True)
class PublicContact:
    kind: str
    value: str
    label: str | None = None


@dataclass(frozen=True)
class SocialLink:
    network: str
    url: str

    def __post_init__(self) -> None:
        _require_text(self.network, "network")
        _require_http_url(self.url, "url")


@dataclass(frozen=True)
class DiscoveredEndpoint:
    kind: str
    url: str
    provider_hint: str | None
    confidence: str
    discovery_method: str

    def __post_init__(self) -> None:
        if self.kind not in {"career_listing", "career_page", "ats_board"}:
            raise ValueError("invalid discovered endpoint kind")
        _require_http_url(self.url, "url")
        if self.confidence not in {"confirmed", "probable", "candidate"}:
            raise ValueError("invalid endpoint confidence")
        if self.discovery_method not in {"explicit_link", "redirect", "structured_data", "platform_signature"}:
            raise ValueError("invalid endpoint discovery method")


@dataclass(frozen=True)
class SearchListingOutput:
    source_id: str
    target_provider_id: str
    source_listing_id: str | None
    title: str
    company: CompanyRef | None
    location: SourceLocation | None
    salary: SalaryRange | None
    work_formats: tuple[str, ...]
    remote_scopes: tuple[RemoteScope, ...]
    native_grade: str | None
    posted_at: date | datetime | None
    vacancy_url: str
    apply_url: str | None
    summary: str | None
    relocation: bool | None = None
    relocation_destinations: tuple[SourceLocation, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id")
        _require_text(self.target_provider_id, "target_provider_id")
        _require_text(self.title, "title")
        _require_work_formats(self.work_formats)
        _require_http_url(self.vacancy_url, "vacancy_url")
        if self.apply_url is not None:
            _require_http_url(self.apply_url, "apply_url")
        if self.relocation_destinations and self.relocation is not True:
            raise ValueError("relocation destinations require relocation=True")


@dataclass(frozen=True)
class VacancyDetailOutput:
    target_provider_id: str
    source_listing_id: str | None
    canonical_vacancy_url: str
    title: str | None
    company: CompanyRef | None
    description: str | None
    requirements: tuple[str, ...]
    responsibilities: tuple[str, ...]
    conditions: tuple[str, ...]
    skills: tuple[str, ...]
    employment_types: tuple[str, ...]
    salary: SalaryRange | None
    work_formats: tuple[str, ...]
    remote_scopes: tuple[RemoteScope, ...]
    application_channels: tuple[ApplicationChannel, ...]
    native_grade: str | None = None
    location: SourceLocation | None = None
    relocation: bool | None = None
    relocation_destinations: tuple[SourceLocation, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.target_provider_id, "target_provider_id")
        _require_http_url(self.canonical_vacancy_url, "canonical_vacancy_url")
        _require_work_formats(self.work_formats)
        if self.relocation_destinations and self.relocation is not True:
            raise ValueError("relocation destinations require relocation=True")


@dataclass(frozen=True)
class CompanyProfileOutput:
    target_provider_id: str
    profile_url: str
    source_company_id: str | None
    company_name: str | None
    description: str | None
    industry: str | None
    size_text: str | None
    locations: tuple[SourceLocation, ...]
    official_site_url: str | None
    career_endpoints: tuple[DiscoveredEndpoint, ...]
    contacts: tuple[PublicContact, ...]
    social_links: tuple[SocialLink, ...]

    def __post_init__(self) -> None:
        _require_text(self.target_provider_id, "target_provider_id")
        _require_http_url(self.profile_url, "profile_url")
        if self.official_site_url is not None:
            _require_http_url(self.official_site_url, "official_site_url")


@dataclass(frozen=True)
class CompanySiteOutput:
    canonical_site_url: str
    company_name: str | None
    contacts: tuple[PublicContact, ...]
    social_links: tuple[SocialLink, ...]
    career_endpoints: tuple[DiscoveredEndpoint, ...]

    def __post_init__(self) -> None:
        _require_http_url(self.canonical_site_url, "canonical_site_url")


@dataclass(frozen=True)
class SearchListingResult:
    outcome: SearchResultOutcome
    items: tuple[SearchListingOutput, ...]
    continuations: tuple[SearchListingInput, ...]
    collection_units_consumed: int
    public_notice: str | None = None
    kind: str = "search_listing"

    def __post_init__(self) -> None:
        if self.collection_units_consumed < 0:
            raise ValueError("collection_units_consumed must be >= 0")
        if self.outcome == SearchResultOutcome.NO_RESULTS:
            if self.items or self.continuations:
                raise ValueError("no_results requires no items and no continuations")
            if self.collection_units_consumed == 0:
                raise ValueError("no_results must consume a collection unit")
        elif self.outcome == SearchResultOutcome.SUCCESS and not (self.items or self.continuations):
            raise ValueError("success requires items or continuations")
        elif self.outcome == SearchResultOutcome.PARTIAL_SUCCESS and not self.items:
            raise ValueError("partial_success requires at least one item")
        if self.collection_units_consumed == 0 and (self.items or not self.continuations):
            raise ValueError("zero-unit bootstrap requires no items and at least one continuation")


@dataclass(frozen=True)
class VacancyDetailResult:
    outcome: SingletonResultOutcome
    item: VacancyDetailOutput | None
    public_notice: str | None = None
    kind: str = "vacancy_detail"

    def __post_init__(self) -> None:
        _validate_singleton(self.outcome, self.item)


@dataclass(frozen=True)
class CompanyProfileResult:
    outcome: SingletonResultOutcome
    item: CompanyProfileOutput | None
    public_notice: str | None = None
    kind: str = "company_profile"

    def __post_init__(self) -> None:
        _validate_singleton(self.outcome, self.item)


@dataclass(frozen=True)
class CompanySiteResult:
    outcome: SingletonResultOutcome
    item: CompanySiteOutput | None
    public_notice: str | None = None
    kind: str = "company_site"

    def __post_init__(self) -> None:
        _validate_singleton(self.outcome, self.item)


type ParserResult = SearchListingResult | VacancyDetailResult | CompanyProfileResult | CompanySiteResult


class ScraperBundle(Protocol):
    manifest: ParserManifest
    input_type: type[ParserInput]
    result_type: type[ParserResult]

    async def execute(self, parser_input: ParserInput, runtime: object) -> ParserResult:
        """Execute one independently callable parser input."""


class SearchScraperBundle(Protocol):
    manifest: ParserManifest
    input_type: type[SearchListingInput]
    result_type: type[SearchListingResult]

    def plan_initial(self, intent: SearchRequest, target: JsonObject) -> tuple[SearchListingInput, ...]:
        """Map business intent to initial self-contained search inputs."""

    async def execute(self, parser_input: SearchListingInput, runtime: object) -> SearchListingResult:
        """Execute one independently callable listing parser input."""


@dataclass(frozen=True)
class ParserFailure:
    kind: ParserFailureKind
    public_notice: str | None = None


@dataclass(frozen=True)
class ParserExecutionResult:
    result: ParserResult | None = None
    failure: ParserFailure | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.failure is None):
            raise ValueError("parser execution requires exactly one result or failure")


@dataclass(frozen=True)
class TargetResolution:
    kind: str
    parser_ref: ParserRef | None = None
    candidate_refs: tuple[ParserRef, ...] = ()


class TargetParserResolver:
    """Resolve a typed provider URL to a parser without registry-order coupling."""

    def __init__(self, manifests: Iterable[ParserManifest]) -> None:
        self._manifests = tuple(sorted(manifests, key=lambda manifest: manifest.ref))

    def resolve(
        self,
        parser_type: ParserType,
        provider_hint: str | None,
        normalized_url: str,
    ) -> TargetResolution:
        _require_http_url(normalized_url, "normalized_url")
        if provider_hint is not None:
            _require_text(provider_hint, "provider_hint")
        matching = tuple(
            manifest
            for manifest in self._manifests
            if manifest.parser_type == parser_type
            and any(re.search(pattern, normalized_url) for pattern in manifest.supported_url_patterns)
        )
        specific = tuple(
            manifest.ref
            for manifest in matching
            if not manifest.is_fallback
            and (provider_hint is None or provider_hint in manifest.provider_ids)
        )
        if specific:
            return _target_resolution(specific)
        fallbacks = tuple(manifest.ref for manifest in matching if manifest.is_fallback)
        return _target_resolution(fallbacks)

    def has_candidate(
        self,
        parser_type: ParserType,
        provider_hint: str | None,
        *,
        output_fact: str | None = None,
    ) -> bool:
        if provider_hint is not None:
            _require_text(provider_hint, "provider_hint")
        return any(
            manifest.parser_type == parser_type
            and (output_fact is None or output_fact in manifest.output_facts)
            and (
                manifest.is_fallback
                or provider_hint is None
                or provider_hint in manifest.provider_ids
            )
            for manifest in self._manifests
        )


class ParserRegistry:
    def __init__(self, entries: Iterable[object]) -> None:
        by_ref: dict[ParserRef, ScraperBundle] = {}
        manifests: dict[ParserRef, ParserManifest] = {}
        for implementation in entries:
            manifest = getattr(implementation, "manifest", None)
            if not isinstance(manifest, ParserManifest):
                raise TypeError("parser registration must be a self-contained bundle with a manifest")
            _validate_bundle_contract(implementation, manifest)
            if manifest.ref in by_ref:
                parser_name = f"{manifest.ref.parser_id}@{manifest.ref.implementation_version}"
                raise ValueError(f"duplicate parser registration: {parser_name}")
            by_ref[manifest.ref] = cast(ScraperBundle, implementation)
            manifests[manifest.ref] = manifest
        self._by_ref: Mapping[ParserRef, ScraperBundle] = MappingProxyType(by_ref)
        self._manifests: Mapping[ParserRef, ParserManifest] = MappingProxyType(manifests)

    def get(self, parser_ref: ParserRef) -> ScraperBundle:
        try:
            return self._by_ref[parser_ref]
        except KeyError as exc:
            raise KeyError(f"unknown parser: {parser_ref.parser_id}@{parser_ref.implementation_version}") from exc

    def manifest(self, parser_ref: ParserRef) -> ParserManifest:
        try:
            return self._manifests[parser_ref]
        except KeyError as exc:
            raise KeyError(f"unknown parser: {parser_ref.parser_id}@{parser_ref.implementation_version}") from exc

    def manifests(self) -> tuple[ParserManifest, ...]:
        return tuple(self._manifests.values())

    def contains(self, parser_ref: ParserRef) -> bool:
        return parser_ref in self._by_ref

    def search_bundles(self) -> tuple[SearchScraperBundle, ...]:
        return tuple(
            cast(SearchScraperBundle, self._by_ref[parser_ref])
            for parser_ref, manifest in self._manifests.items()
            if manifest.parser_type == ParserType.SEARCH_LISTING
        )

def _validate_singleton(outcome: SingletonResultOutcome, item: object | None) -> None:
    if outcome == SingletonResultOutcome.SUCCESS and item is None:
        raise ValueError("success requires exactly one item")
    if outcome == SingletonResultOutcome.NOT_FOUND and item is not None:
        raise ValueError("not_found requires item=None")


def _target_resolution(candidates: tuple[ParserRef, ...]) -> TargetResolution:
    if not candidates:
        return TargetResolution(kind="unsupported_target")
    if len(candidates) > 1:
        return TargetResolution(kind="ambiguous_target", candidate_refs=candidates)
    return TargetResolution(kind="resolved", parser_ref=candidates[0])


def _validate_bundle_contract(implementation: object, manifest: ParserManifest) -> None:
    input_type = getattr(implementation, "input_type", None)
    result_type = getattr(implementation, "result_type", None)
    if not isinstance(input_type, type):
        raise TypeError("parser bundle must declare input_type")
    if not isinstance(result_type, type):
        raise TypeError("parser bundle must declare result_type")
    expected_types = {
        ParserType.SEARCH_LISTING: (SearchListingInput, SearchListingResult),
        ParserType.VACANCY_DETAIL: (VacancyDetailInput, VacancyDetailResult),
        ParserType.COMPANY_PROFILE: (CompanyProfileInput, CompanyProfileResult),
        ParserType.COMPANY_SITE: (CompanySiteInput, CompanySiteResult),
    }[manifest.parser_type]
    if (input_type, result_type) != expected_types:
        raise TypeError("parser bundle contract types must match manifest parser_type")
    if not callable(getattr(implementation, "execute", None)):
        raise TypeError("parser bundle must implement execute")
    if manifest.parser_type == ParserType.SEARCH_LISTING and not callable(
        getattr(implementation, "plan_initial", None)
    ):
        raise TypeError("search-listing bundle must implement plan_initial")


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _clean_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _require_http_url(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an absolute http(s) URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute http(s) URL")


def _require_work_formats(values: tuple[str, ...]) -> None:
    if any(value not in {"onsite", "hybrid", "remote"} for value in values):
        raise ValueError("work_formats contains an invalid value")
