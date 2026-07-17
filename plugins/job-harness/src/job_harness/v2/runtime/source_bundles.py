"""Independent typed bundles backed by source-specific parser implementations."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from job_harness.v2.contracts import (
    ApplicationChannel,
    CompanyProfileInput,
    CompanyProfileOutput,
    CompanyProfileResult,
    CompanyRef,
    CompanySiteInput,
    CompanySiteOutput,
    CompanySiteResult,
    DetailEnrichmentScraper,
    DiscoveredEndpoint,
    HttpMethod,
    InvocationScope,
    ParserManifest,
    ParserRef,
    ParserType,
    PublicContact,
    RawListing,
    RemoteScope,
    SalaryRange,
    SearchCriterion,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchRequest,
    SearchResultOutcome,
    SingletonResultOutcome,
    SourceFetchRequest,
    SourceLocation,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
    TransportKind,
    VacancyDetailInput,
    VacancyDetailOutput,
    VacancyDetailResult,
)
from job_harness.v2.geography import is_region_scope, normalize_source_geographies
from job_harness.v2.ports import HttpAction, ParserRuntime, RetrySafety
from job_harness.v2.runtime.application_channel_profiles import official_site_url_from_profile
from job_harness.v2.runtime.application_channel_resolver import best_career_link
from job_harness.v2.runtime.application_channel_sources import (
    HH_APPLICATION_CHANNEL_POLICY,
    ApplicationChannelResolutionPolicy,
)
from job_harness.v2.runtime.company_contacts import profile_contacts_from_html, site_contacts_from_html
from job_harness.v2.runtime.errors import HttpStatusError
from job_harness.v2.runtime.sources.companies.ats import (
    AtsCompanySourceConfig,
    ats_company_initial_request,
    ats_company_source_from_config,
    detect_ats_company_config,
)
from job_harness.v2.serialization import JsonObject, to_jsonable

_WORK_FORMAT_MARKERS = {
    "remote": "remote",
    "удален": "remote",
    "удалён": "remote",
    "hybrid": "hybrid",
    "гибрид": "hybrid",
    "on_site": "onsite",
    "onsite": "onsite",
    "on-site": "onsite",
    "office": "onsite",
    "офис": "onsite",
}
_WORK_FORMAT_ORDER = ("remote", "hybrid", "onsite")
_ATS_PROVIDER_IDS = (
    "ats:ashby",
    "ats:bamboohr",
    "ats:breezy",
    "ats:comeet",
    "ats:dreamjob",
    "ats:greenhouse",
    "ats:huntflow",
    "ats:icims",
    "ats:jazzhr",
    "ats:jobvite",
    "ats:join",
    "ats:lever",
    "ats:personio",
    "ats:recruitee",
    "ats:smartrecruiters",
    "ats:successfactors",
    "ats:taleo",
    "ats:teamtailor",
    "ats:workable",
    "ats:workday",
    "ats:ycombinator",
)
_ATS_DISCOVERY_URL_PATTERNS = (
    r"https?://(?:jobs|api)(?:\.eu)?\.lever\.co/.*",
    r"https?://(?:job-boards(?:\.eu)?|boards|boards-api)\.greenhouse\.io/.*",
    r"https?://(?:jobs|api)\.ashbyhq\.com/.*",
    r"https?://apply\.workable\.com/.*",
    r"https?://[^/]+\.(?:recruitee\.com|bamboohr\.com|breezy\.hr|huntflow\.io|teamtailor\.com)/.*",
    r"https?://(?:jobs|api)\.smartrecruiters\.com/.*",
    r"https?://(?:www\.)?comeet\.com/.*",
    r"https?://jobs\.jobvite\.com/.*",
    r"https?://[^/]+\.applytojob\.com/.*",
    r"https?://[^/]+\.icims\.com/jobs/search.*",
    r"https?://[^/]+\.taleo\.net/.*/ats/careers/v2/searchResults.*",
    r"https?://[^/]*successfactors[^/]*/career(?:\?.*)?",
    r"https?://[^/]+\.myworkdayjobs\.com/.*",
    r"https?://[^/]+\.jobs\.personio\.(?:de|com)/.*",
    r"https?://join\.com/companies/[^/]+.*",
    r"https?://dreamjob\.ru/employers/[^/]+/vakansii.*",
    r"https?://www\.ycombinator\.com/companies/[^/]+/jobs.*",
)


@dataclass(frozen=True)
class SearchSourceBundle:
    source: SourceScraper
    manifest: ParserManifest
    input_type = SearchListingInput
    result_type = SearchListingResult

    def plan_initial(self, intent: SearchRequest, target: JsonObject) -> tuple[SearchListingInput, ...]:
        requests = self.source.build_search_requests(intent)
        native_filters = _native_filters(intent, self.manifest.native_criteria)
        return tuple(
            SearchListingInput(
                source_id=self.source.descriptor.source_id,
                target_provider_id=(
                    self.source.descriptor.identity_namespace
                    or self.source.descriptor.source_id
                ),
                queries=(
                    intent.query_variants
                    if self.manifest.query_mode == "downstream_only"
                    else request.query_variants
                ),
                target=target,
                cursor={"request": _request_payload(request)},
                native_filters=native_filters,
                resolved_state=None,
            )
            for request in requests
        )

    def build_action(self, parser_input: SearchListingInput) -> HttpAction:
        return _http_action(_request_from_input(parser_input))

    async def execute(self, parser_input: SearchListingInput, runtime: ParserRuntime) -> SearchListingResult:
        request = _request_from_input(parser_input)
        response = await runtime.http(_http_action(request))
        artifact = SourceResponseArtifact(
            source_id=request.source_id,
            url=response.final_url,
            media_type=response.media_type,
            body=response.body.decode("utf-8", errors="replace"),
        )
        parsed = self.source.parse_search_response(artifact, request)
        continuations = parsed.parallel_requests or ((parsed.next_request,) if parsed.next_request is not None else ())
        outcome = _search_outcome(parsed.outcome)
        if outcome == SearchResultOutcome.SUCCESS and not parsed.listings and not continuations:
            outcome = SearchResultOutcome.NO_RESULTS
        return SearchListingResult(
            outcome=outcome,
            items=tuple(
                _listing_output(
                    listing,
                    target_provider_id=parser_input.target_provider_id,
                )
                for listing in parsed.listings
            ),
            continuations=tuple(_continuation(parser_input, request) for request in continuations),
            collection_units_consumed=1,
        )


@dataclass(frozen=True)
class DiscoveredAtsSearchBundle:
    """Parse a discovered ATS board without requiring a catalog source row."""

    manifest = ParserManifest(
        parser_id="ats.discovered.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id="ats.discovered.search.input.v1",
        output_schema_id="search-listing-output.v2",
        transport=TransportKind.HTTP,
        provider_ids=_ATS_PROVIDER_IDS,
        supported_url_patterns=_ATS_DISCOVERY_URL_PATTERNS,
        output_facts=(
            "title",
            "company",
            "location",
            "salary",
            "work_formats",
            "remote_scopes",
            "native_grade",
            "posted_at",
            "vacancy_url",
            "apply_url",
            "summary",
            "relocation",
            "relocation_destinations",
        ),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=("company_career",),
        query_mode="query_group",
        collection_unit="page",
        native_criteria=(),
        default_unit_budget=200,
        default_item_budget=200,
        default_invocation_budget=201,
        max_units_per_invocation=1,
    )
    input_type = SearchListingInput
    result_type = SearchListingResult

    def plan_initial(self, intent: SearchRequest, target: JsonObject) -> tuple[SearchListingInput, ...]:
        config = _discovered_ats_config(target)
        if config is None:
            return ()
        query_variants = (
            intent.query_variants
            if config.platform == "workday"
            else intent.query_variants[:1]
        )
        return tuple(
            SearchListingInput(
                source_id=config.source_id,
                target_provider_id=config.source_id,
                queries=intent.query_variants,
                target=target,
                cursor={
                    "request": _request_payload(
                        ats_company_initial_request(config, query_variant=query_variant)
                    )
                },
                native_filters={},
                resolved_state={"platform": config.platform},
            )
            for query_variant in query_variants
        )

    def build_action(self, parser_input: SearchListingInput) -> HttpAction:
        return _http_action(_request_from_input(parser_input))

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        config = _discovered_ats_config(parser_input.target)
        if config is None or config.source_id != parser_input.source_id:
            raise ValueError("discovered ATS input does not match its target URL")
        request = _request_from_input(parser_input)
        response = await runtime.http(_http_action(request))
        artifact = SourceResponseArtifact(
            source_id=request.source_id,
            url=response.final_url,
            media_type=response.media_type,
            body=response.body.decode("utf-8", errors="replace"),
        )
        source = ats_company_source_from_config(config)
        parsed = source.parse_search_response(artifact, request)
        continuations = parsed.parallel_requests or (
            (parsed.next_request,) if parsed.next_request is not None else ()
        )
        outcome = _search_outcome(parsed.outcome)
        if outcome == SearchResultOutcome.SUCCESS and not parsed.listings and not continuations:
            outcome = SearchResultOutcome.NO_RESULTS
        return SearchListingResult(
            outcome=outcome,
            items=tuple(
                _listing_output(listing, target_provider_id=parser_input.target_provider_id)
                for listing in parsed.listings
            ),
            continuations=tuple(
                _continuation(parser_input, continuation)
                for continuation in continuations
            ),
            collection_units_consumed=1,
        )


@dataclass(frozen=True)
class DetailSourceBundle:
    source: DetailEnrichmentScraper
    manifest: ParserManifest
    input_type = VacancyDetailInput
    result_type = VacancyDetailResult

    def build_action(self, parser_input: VacancyDetailInput) -> HttpAction:
        _anchor, request = self._request(parser_input)
        return _http_action(request)

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        anchor, request = self._request(parser_input)
        try:
            response = await runtime.http(_http_action(request))
        except HttpStatusError as exc:
            if _is_missing_resource(exc):
                return VacancyDetailResult(
                    outcome=SingletonResultOutcome.NOT_FOUND,
                    item=None,
                )
            raise
        artifact = SourceResponseArtifact(
            source_id=request.source_id,
            url=response.final_url,
            media_type=response.media_type,
            body=response.body.decode("utf-8", errors="replace"),
        )
        detailed = self.source.parse_detail_response(artifact, anchor)
        return VacancyDetailResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=_detail_output(detailed, parser_input),
        )

    def _request(self, parser_input: VacancyDetailInput) -> tuple[RawListing, SourceFetchRequest]:
        anchor = RawListing(
            source_listing_id=parser_input.source_listing_id,
            title=parser_input.source_listing_id or parser_input.vacancy_url,
            url=parser_input.vacancy_url,
            source=self.source.descriptor.source_id,
        )
        return anchor, self.source.build_detail_request(anchor)


@dataclass(frozen=True)
class CompanyProfileSourceBundle:
    policy: ApplicationChannelResolutionPolicy
    manifest: ParserManifest
    profile_locations: Callable[[str], tuple[str, ...]]
    input_type = CompanyProfileInput
    result_type = CompanyProfileResult

    def build_action(self, parser_input: CompanyProfileInput) -> HttpAction:
        return HttpAction(
            method="GET",
            url=parser_input.company_profile_url,
            retry_safety=RetrySafety.SAFE,
        )

    async def execute(self, parser_input: CompanyProfileInput, runtime: ParserRuntime) -> CompanyProfileResult:
        try:
            response = await runtime.http(self.build_action(parser_input))
        except HttpStatusError as exc:
            if _is_missing_resource(exc):
                return CompanyProfileResult(
                    outcome=SingletonResultOutcome.NOT_FOUND,
                    item=None,
                )
            raise
        html = response.body.decode("utf-8", errors="replace")
        official_site_url = official_site_url_from_profile(
            base_url=response.final_url,
            html=html,
            policy=self.policy,
        )
        return CompanyProfileResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=CompanyProfileOutput(
                target_provider_id=parser_input.target_provider_id,
                profile_url=response.final_url,
                source_company_id=parser_input.source_company_id,
                company_name=None,
                description=None,
                industry=None,
                size_text=None,
                locations=tuple(
                    SourceLocation(value)
                    for value in self.profile_locations(html)
                ),
                official_site_url=official_site_url,
                career_endpoints=(),
                contacts=_public_contacts(
                    profile_contacts_from_html(
                        source_id=self.policy.source_id,
                        base_url=response.final_url,
                        html=html,
                    )
                ),
                social_links=(),
            ),
        )


@dataclass(frozen=True)
class CompanySiteSourceBundle:
    policy: ApplicationChannelResolutionPolicy
    manifest: ParserManifest
    input_type = CompanySiteInput
    result_type = CompanySiteResult

    def build_action(self, parser_input: CompanySiteInput) -> HttpAction:
        return HttpAction(
            method="GET",
            url=parser_input.site_url,
            retry_safety=RetrySafety.SAFE,
        )

    async def execute(self, parser_input: CompanySiteInput, runtime: ParserRuntime) -> CompanySiteResult:
        try:
            response = await runtime.http(self.build_action(parser_input))
        except HttpStatusError as exc:
            if _is_missing_resource(exc):
                return CompanySiteResult(
                    outcome=SingletonResultOutcome.NOT_FOUND,
                    item=None,
                )
            raise
        html = response.body.decode("utf-8", errors="replace")
        career_url = best_career_link(
            base_url=response.final_url,
            html=html,
            policy=self.policy,
        )
        endpoints = (
            ()
            if career_url is None
            else (
                DiscoveredEndpoint(
                    kind="career_page",
                    url=career_url,
                    provider_hint=_ats_provider_hint(career_url),
                    confidence="confirmed",
                    discovery_method="explicit_link",
                ),
            )
        )
        return CompanySiteResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=CompanySiteOutput(
                canonical_site_url=response.final_url,
                company_name=None,
                contacts=_public_contacts(
                    site_contacts_from_html(
                        base_url=response.final_url,
                        html=html,
                        source="company_site_homepage",
                    )
                ),
                social_links=(),
                career_endpoints=endpoints,
            ),
        )


def search_bundle(source: SourceScraper, parser_ref: ParserRef) -> SearchSourceBundle:
    descriptor = source.descriptor
    manifest = ParserManifest(
        parser_id=parser_ref.parser_id,
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version=parser_ref.implementation_version,
        input_schema_id=f"{descriptor.source_id}.search.input.v1",
        output_schema_id="search-listing-output.v2",
        transport=TransportKind.HTTP,
        provider_ids=(descriptor.source_id,),
        supported_url_patterns=(),
        output_facts=(
            "title",
            "company",
            "location",
            "salary",
            "work_formats",
            "remote_scopes",
            "native_grade",
            "posted_at",
            "vacancy_url",
            "apply_url",
            "summary",
            "relocation",
            "relocation_destinations",
        ),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=(descriptor.source_type.value,),
        query_mode=(
            "per_query"
            if SearchCriterion.QUERY in descriptor.native_request_criteria
            else "downstream_only"
        ),
        collection_unit="page",
        native_criteria=tuple(sorted(item.value for item in descriptor.native_request_criteria)),
        default_unit_budget=max(1, descriptor.source_limit),
        default_item_budget=descriptor.source_limit,
        default_invocation_budget=max(2, descriptor.source_limit + 1),
        max_units_per_invocation=1,
    )
    return SearchSourceBundle(source=source, manifest=manifest)


def discovered_ats_search_bundle() -> DiscoveredAtsSearchBundle:
    return DiscoveredAtsSearchBundle()


def detail_bundle(source: SourceScraper) -> DetailSourceBundle:
    if not isinstance(source, DetailEnrichmentScraper):
        raise ValueError(f"source does not implement vacancy detail parsing: {source.descriptor.source_id}")
    descriptor = source.descriptor
    manifest = ParserManifest(
        parser_id=f"{descriptor.source_id}.detail",
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id=f"{descriptor.source_id}.detail.input.v1",
        output_schema_id="vacancy-detail-output.v3",
        transport=TransportKind.HTTP,
        provider_ids=(descriptor.source_id,),
        supported_url_patterns=(r"https?://.+",),
        output_facts=(
            "description",
            "company",
            "requirements",
            "responsibilities",
            "conditions",
            "skills",
            "employment_types",
            "salary",
            "work_formats",
            "remote_scopes",
            "application_channels",
            "location",
            "native_grade",
            "relocation",
            "relocation_destinations",
        ),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )
    return DetailSourceBundle(source=source, manifest=manifest)


def hh_company_profile_bundle(
    profile_locations: Callable[[str], tuple[str, ...]],
) -> CompanyProfileSourceBundle:
    return CompanyProfileSourceBundle(
        policy=HH_APPLICATION_CHANNEL_POLICY,
        profile_locations=profile_locations,
        manifest=ParserManifest(
            parser_id="hh_ru.company-profile",
            parser_type=ParserType.COMPANY_PROFILE,
            implementation_version="1.0",
            input_schema_id="hh_ru.company-profile.input.v1",
            output_schema_id="company-profile-output.v1",
            transport=TransportKind.HTTP,
            provider_ids=("hh_ru",),
            supported_url_patterns=(r"https://(?:[^/]+\.)?hh\.ru/employer/[^/?#]+",),
            output_facts=("official_site_url", "locations", "contacts"),
            invocation_scope=InvocationScope.STATELESS_UNIT,
        ),
    )


def generic_company_site_bundle() -> CompanySiteSourceBundle:
    policy = ApplicationChannelResolutionPolicy(
        source_id="web",
        career_markers=HH_APPLICATION_CHANNEL_POLICY.career_markers,
        career_text_markers=HH_APPLICATION_CHANNEL_POLICY.career_text_markers,
        career_text_exact=HH_APPLICATION_CHANNEL_POLICY.career_text_exact,
        ats_domains=HH_APPLICATION_CHANNEL_POLICY.ats_domains,
        non_career_link_domains=HH_APPLICATION_CHANNEL_POLICY.non_career_link_domains,
    )
    return CompanySiteSourceBundle(
        policy=policy,
        manifest=ParserManifest(
            parser_id="web.company-site",
            parser_type=ParserType.COMPANY_SITE,
            implementation_version="1.0",
            input_schema_id="web.company-site.input.v1",
            output_schema_id="company-site-output.v1",
            transport=TransportKind.HTTP,
            provider_ids=("web",),
            supported_url_patterns=(r"https?://.+",),
            output_facts=("contacts", "career_endpoints"),
            invocation_scope=InvocationScope.STATELESS_UNIT,
            is_fallback=True,
        ),
    )


def _discovered_ats_config(target: JsonObject) -> AtsCompanySourceConfig | None:
    if target.get("kind") != "discovered_url":
        return None
    url = target.get("url")
    if not isinstance(url, str):
        return None
    try:
        detected = detect_ats_company_config(url)
    except ValueError:
        return None
    digest = hashlib.sha256(
        f"{detected.platform}\0{detected.career_url}".encode()
    ).hexdigest()[:16]
    return replace(
        detected,
        source_id=f"ats:{detected.platform}:{digest}",
    )


def _ats_provider_hint(url: str) -> str | None:
    try:
        return f"ats:{detect_ats_company_config(url).platform}"
    except ValueError:
        return None


def _request_payload(request: SourceFetchRequest) -> JsonObject:
    return {
        "source_id": request.source_id,
        "query_variant": request.query_variant,
        "query_variants": list(request.query_variants),
        "url": request.url,
        "method": request.method.value,
        "headers": dict(request.headers),
        "body_base64": None if request.body is None else base64.b64encode(request.body).decode("ascii"),
    }


def _request_from_input(parser_input: SearchListingInput) -> SourceFetchRequest:
    payload = parser_input.cursor.get("request")
    if not isinstance(payload, dict):
        raise ValueError("search cursor is missing request state")
    raw_body = payload.get("body_base64")
    body = None
    if raw_body is not None:
        if not isinstance(raw_body, str):
            raise ValueError("request body cursor must be base64 text")
        body = base64.b64decode(raw_body, validate=True)
    method = payload.get("method")
    if not isinstance(method, str):
        raise ValueError("request method cursor must be text")
    return SourceFetchRequest(
        source_id=_required_text(payload, "source_id"),
        query_variant=_required_text(payload, "query_variant"),
        query_variants=tuple(_string_list(payload, "query_variants")),
        url=_required_text(payload, "url"),
        method=HttpMethod(method),
        headers=_string_mapping(payload, "headers"),
        body=body,
    )


def _http_action(request: SourceFetchRequest) -> HttpAction:
    return HttpAction(
        method=request.method.value,
        url=request.url,
        headers=request.headers,
        body=request.body,
        retry_safety=RetrySafety.SAFE,
    )


def _is_missing_resource(exc: HttpStatusError) -> bool:
    return exc.status_code in {404, 410}


def _continuation(original: SearchListingInput, request: SourceFetchRequest) -> SearchListingInput:
    return SearchListingInput(
        source_id=original.source_id,
        target_provider_id=original.target_provider_id,
        queries=original.queries,
        target=original.target,
        cursor={"request": _request_payload(request)},
        native_filters=original.native_filters,
        resolved_state=original.resolved_state,
    )


def _listing_output(listing: RawListing, *, target_provider_id: str) -> SearchListingOutput:
    return SearchListingOutput(
        source_id=listing.source,
        target_provider_id=target_provider_id,
        source_listing_id=listing.source_listing_id,
        title=listing.title,
        company=_company_ref(listing, target_provider_id=target_provider_id),
        location=_source_location(listing),
        salary=_salary(listing),
        work_formats=_explicit_work_formats(listing),
        remote_scopes=_explicit_remote_scopes(listing),
        native_grade=listing.native_grade,
        posted_at=_posted_at(listing.posted_at),
        vacancy_url=listing.url,
        apply_url=_optional_raw_url(listing.raw, "apply_url"),
        summary=listing.description,
        relocation=listing.relocation,
        relocation_destinations=_relocation_destinations(listing),
    )


def _detail_output(listing: RawListing, parser_input: VacancyDetailInput) -> VacancyDetailOutput:
    return VacancyDetailOutput(
        target_provider_id=parser_input.target_provider_id,
        source_listing_id=parser_input.source_listing_id,
        canonical_vacancy_url=listing.url,
        title=None if listing.title == (parser_input.source_listing_id or parser_input.vacancy_url) else listing.title,
        company=_company_ref(listing, target_provider_id=parser_input.target_provider_id),
        description=listing.description,
        requirements=_text_tuple(listing.requirements),
        responsibilities=_section_tuple(listing.additional_sections, ("responsibilities", "обязанности", "задачи")),
        conditions=_section_tuple(listing.additional_sections, ("conditions", "условия")),
        skills=listing.skills,
        employment_types=_employment_types(listing.raw),
        salary=_salary(listing),
        work_formats=_explicit_work_formats(listing),
        remote_scopes=_explicit_remote_scopes(listing),
        application_channels=_application_channels(listing.raw),
        native_grade=listing.native_grade,
        location=_source_location(listing),
        relocation=listing.relocation,
        relocation_destinations=_relocation_destinations(listing),
    )


def _company_ref(listing: RawListing, *, target_provider_id: str) -> CompanyRef | None:
    raw_company = listing.raw.get("company")
    company = raw_company if isinstance(raw_company, dict) else {}
    name = listing.company or _optional_text(company.get("visibleName")) or _optional_text(company.get("name"))
    source_company_id = _optional_identifier(company.get("id"))
    profile_url = _first_url(company, ("employerUrl", "companyProfileUrl"))
    official_site_url = _first_url(company, ("companySiteUrl", "officialSiteUrl"))
    vacancies_url = _first_url(company, ("companyVacanciesUrl", "sourceVacanciesUrl"))
    if not any((name, source_company_id, profile_url, official_site_url, vacancies_url)):
        return None
    return CompanyRef(
        name=name,
        target_provider_id=target_provider_id if source_company_id or profile_url else None,
        source_company_id=source_company_id,
        profile_url=profile_url,
        official_site_url=official_site_url,
        source_vacancies_url=vacancies_url,
    )


def _salary(listing: RawListing) -> SalaryRange | None:
    if listing.salary_min is None and listing.salary_max is None:
        return None
    return SalaryRange(
        salary_from=listing.salary_min,
        salary_to=listing.salary_max,
        currency=listing.salary_currency,
        gross=listing.salary_gross,
        period=listing.salary_period,
    )


def _source_location(listing: RawListing) -> SourceLocation | None:
    text = listing.location_text.strip() if listing.location_text else None
    cities = listing.location_cities or ((listing.city,) if listing.city else ())
    normalized_country = (
        normalize_source_geographies(listing.country)
        if listing.country and not listing.location_countries
        else ()
    )
    countries = listing.location_countries or tuple(
        value for value in normalized_country if not is_region_scope(value)
    )
    regions = listing.location_regions or tuple(
        value for value in normalized_country if is_region_scope(value)
    )
    if not any((text, cities, countries, regions)):
        return None
    return SourceLocation(
        text=text,
        cities=cities,
        countries=countries,
        regions=regions,
    )


def _relocation_destinations(listing: RawListing) -> tuple[SourceLocation, ...]:
    destinations: list[SourceLocation] = []
    for raw_destination in listing.relocation_destinations:
        geographies = normalize_source_geographies(raw_destination)
        destinations.append(
            SourceLocation(
                text=raw_destination,
                countries=tuple(
                    value for value in geographies if not is_region_scope(value)
                ),
                regions=tuple(value for value in geographies if is_region_scope(value)),
            )
        )
    return tuple(destinations)


def _explicit_work_formats(listing: RawListing) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("workFormats", "work_format", "work_type", "remote_type", "employment_type"):
        _append_text_values(values, listing.raw.get(key))
    formats: set[str] = set()
    for value in values:
        folded = value.casefold()
        for marker, work_format in _WORK_FORMAT_MARKERS.items():
            if marker in folded:
                formats.add(work_format)
    if listing.remote_global is True or listing.remote_in_country is True:
        formats.add("remote")
    return tuple(value for value in _WORK_FORMAT_ORDER if value in formats)


def _explicit_remote_scopes(listing: RawListing) -> tuple[RemoteScope, ...]:
    if listing.remote_global is True:
        return (RemoteScope(kind="worldwide", code=None),)
    scopes = [
        *(RemoteScope(kind="country", code=code.upper()) for code in listing.remote_scope_countries),
        *(RemoteScope(kind="region", code=code.upper()) for code in listing.remote_scope_regions),
    ]
    return tuple(dict.fromkeys(scopes))


def _posted_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _native_filters(
    intent: SearchRequest,
    native_criteria: tuple[str, ...],
) -> JsonObject:
    payload = to_jsonable(intent)
    if not isinstance(payload, dict):
        raise TypeError("search request must serialize to an object")
    return {
        key: value
        for key, value in payload.items()
        if key in native_criteria
        and value not in (None, [], {})
    }


def _search_outcome(outcome: SourceOutcome) -> SearchResultOutcome:
    mapping = {
        SourceOutcome.SUCCESS: SearchResultOutcome.SUCCESS,
        SourceOutcome.NO_RESULTS: SearchResultOutcome.NO_RESULTS,
        SourceOutcome.PARTIAL_SUCCESS: SearchResultOutcome.PARTIAL_SUCCESS,
    }
    try:
        return mapping[outcome]
    except KeyError as exc:
        raise ValueError(f"source parser returned a non-result outcome: {outcome.value}") from exc


def _section_tuple(sections: dict[str, str], markers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for key, value in sections.items()
        if any(marker in key.casefold() for marker in markers) and value.strip()
    )


def _text_tuple(value: str | None) -> tuple[str, ...]:
    return () if value is None or not value.strip() else (value.strip(),)


def _employment_types(raw: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    _append_text_values(values, raw.get("employment"))
    _append_text_values(values, raw.get("employment_type"))
    folded = " ".join(values).casefold()
    mappings = (
        ("full", "full_time"),
        ("part", "part_time"),
        ("contract", "contract"),
        ("temporary", "temporary"),
        ("intern", "internship"),
    )
    return tuple(output for marker, output in mappings if marker in folded)


def _application_channels(raw: dict[str, object]) -> tuple[ApplicationChannel, ...]:
    value = raw.get("application_channels")
    if not isinstance(value, list):
        return ()
    channels: list[ApplicationChannel] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        label = item.get("label")
        channels.append(
            ApplicationChannel(
                kind="apply_url",
                value=url,
                label=label if isinstance(label, str) else None,
            )
        )
    return tuple(channels)


def _public_contacts(values: tuple[dict[str, str], ...]) -> tuple[PublicContact, ...]:
    return tuple(
        PublicContact(
            kind=value["type"],
            value=value["value"],
            label=value.get("label"),
        )
        for value in values
        if value.get("type") and value.get("value")
    )


def _append_text_values(values: list[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        values.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            _append_text_values(values, nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _append_text_values(values, nested)


def _first_url(payload: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _absolute_http_url(payload.get(key))
        if value is not None:
            return value
    return None


def _optional_raw_url(payload: dict[str, object], key: str) -> str | None:
    return _absolute_http_url(payload.get(key))


def _absolute_http_url(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    parsed = urlsplit(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _optional_identifier(value: object) -> str | None:
    if isinstance(value, str | int) and not isinstance(value, bool):
        text = str(value).strip()
        return text or None
    return None


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value


def _string_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a string list")
    return value


def _string_mapping(payload: dict[str, object], key: str) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"{key} must be a string mapping")
    return value
