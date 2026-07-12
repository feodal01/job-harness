"""Independent typed bundles backed by source-specific parser implementations."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime

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
    ParserType,
    PublicContact,
    RawListing,
    RemoteScope,
    SalaryRange,
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
from job_harness.v2.ports import HttpAction, ParserRuntime
from job_harness.v2.runtime.application_channel_profiles import official_site_url_from_profile
from job_harness.v2.runtime.application_channel_resolver import best_career_link
from job_harness.v2.runtime.application_channel_sources import (
    HH_APPLICATION_CHANNEL_POLICY,
    ApplicationChannelResolutionPolicy,
)
from job_harness.v2.runtime.company_contacts import profile_contacts_from_html, site_contacts_from_html
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


@dataclass(frozen=True)
class SearchSourceBundle:
    source: SourceScraper
    manifest: ParserManifest
    input_type = SearchListingInput
    result_type = SearchListingResult

    def plan_initial(self, intent: SearchRequest, target: JsonObject) -> tuple[SearchListingInput, ...]:
        requests = self.source.build_search_requests(intent)
        native_filters = _native_filters(intent)
        return tuple(
            SearchListingInput(
                source_id=self.source.descriptor.source_id,
                target_provider_id=self.source.descriptor.source_id,
                queries=request.query_variants,
                target=target,
                cursor={"request": _request_payload(request)},
                native_filters=native_filters,
                resolved_state=None,
            )
            for request in requests
        )

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
class DetailSourceBundle:
    source: DetailEnrichmentScraper
    manifest: ParserManifest
    input_type = VacancyDetailInput
    result_type = VacancyDetailResult

    async def execute(self, parser_input: VacancyDetailInput, runtime: ParserRuntime) -> VacancyDetailResult:
        anchor = RawListing(
            source_listing_id=parser_input.source_listing_id,
            title=parser_input.source_listing_id or parser_input.vacancy_url,
            url=parser_input.vacancy_url,
            source=self.source.descriptor.source_id,
        )
        request = self.source.build_detail_request(anchor)
        response = await runtime.http(_http_action(request))
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


@dataclass(frozen=True)
class CompanyProfileSourceBundle:
    policy: ApplicationChannelResolutionPolicy
    manifest: ParserManifest
    input_type = CompanyProfileInput
    result_type = CompanyProfileResult

    async def execute(self, parser_input: CompanyProfileInput, runtime: ParserRuntime) -> CompanyProfileResult:
        response = await runtime.http(HttpAction(method="GET", url=parser_input.company_profile_url))
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
                locations=(),
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

    async def execute(self, parser_input: CompanySiteInput, runtime: ParserRuntime) -> CompanySiteResult:
        response = await runtime.http(HttpAction(method="GET", url=parser_input.site_url))
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
                    provider_hint=None,
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


def search_bundle(source: SourceScraper) -> SearchSourceBundle:
    descriptor = source.descriptor
    manifest = ParserManifest(
        parser_id=f"{descriptor.source_id}.search",
        parser_type=ParserType.SEARCH_LISTING,
        implementation_version="1.0",
        input_schema_id=f"{descriptor.source_id}.search.input.v1",
        output_schema_id="search-listing-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=(descriptor.source_id,),
        supported_url_patterns=(),
        output_facts=(
            "title",
            "company",
            "location",
            "salary",
            "workFormats",
            "remoteScopes",
            "nativeGrade",
            "postedAt",
            "vacancyUrl",
            "applyUrl",
            "summary",
        ),
        invocation_scope=InvocationScope.STATELESS_UNIT,
        source_kinds=(descriptor.source_type.value,),
        query_mode="per_query",
        collection_unit="page",
        native_criteria=tuple(sorted(item.value for item in descriptor.native_request_criteria)),
        default_unit_budget=max(1, descriptor.source_limit),
        default_item_budget=descriptor.source_limit,
        default_invocation_budget=max(2, descriptor.source_limit + 1),
        max_units_per_invocation=1,
    )
    return SearchSourceBundle(source=source, manifest=manifest)


def detail_bundle(source: SourceScraper) -> DetailSourceBundle:
    if not isinstance(source, DetailEnrichmentScraper):
        raise ValueError(f"source does not implement vacancy detail parsing: {source.descriptor.source_id}")
    descriptor = source.descriptor
    manifest = ParserManifest(
        parser_id=f"{descriptor.source_id}.detail",
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id=f"{descriptor.source_id}.detail.input.v1",
        output_schema_id="vacancy-detail-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=(descriptor.source_id,),
        supported_url_patterns=(),
        output_facts=(
            "description",
            "requirements",
            "responsibilities",
            "conditions",
            "skills",
            "employmentTypes",
            "salary",
            "workFormats",
            "remoteScopes",
            "applicationChannels",
        ),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )
    return DetailSourceBundle(source=source, manifest=manifest)


def hh_company_profile_bundle() -> CompanyProfileSourceBundle:
    return CompanyProfileSourceBundle(
        policy=HH_APPLICATION_CHANNEL_POLICY,
        manifest=ParserManifest(
            parser_id="hh_ru.company-profile",
            parser_type=ParserType.COMPANY_PROFILE,
            implementation_version="1.0",
            input_schema_id="hh_ru.company-profile.input.v1",
            output_schema_id="company-profile-output.v1",
            transport=TransportKind.HTTP,
            provider_ids=("hh_ru",),
            supported_url_patterns=(r"https://(?:[^/]+\.)?hh\.ru/employer/[^/?#]+",),
            output_facts=("officialSiteUrl", "contacts"),
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
            output_facts=("contacts", "careerEndpoints"),
            invocation_scope=InvocationScope.STATELESS_UNIT,
        ),
    )


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
    )


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
        location=SourceLocation(listing.location_text) if listing.location_text else None,
        salary=_salary(listing),
        work_formats=_explicit_work_formats(listing),
        remote_scopes=_explicit_remote_scopes(listing),
        native_grade=listing.native_grade,
        posted_at=_posted_at(listing.posted_at),
        vacancy_url=listing.url,
        apply_url=_optional_raw_url(listing.raw, "apply_url"),
        summary=listing.description,
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
    gross = None
    compensation = listing.raw.get("compensation")
    if isinstance(compensation, dict) and isinstance(compensation.get("gross"), bool):
        gross = compensation["gross"]
    return SalaryRange(
        salary_from=listing.salary_min,
        salary_to=listing.salary_max,
        currency=listing.salary_currency,
        gross=gross,
        period=None,
    )


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
    return ()


def _posted_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _native_filters(intent: SearchRequest) -> JsonObject:
    payload = to_jsonable(intent)
    if not isinstance(payload, dict):
        raise TypeError("search request must serialize to an object")
    return {
        key: value
        for key, value in payload.items()
        if key
        in {
            "country",
            "city",
            "salary_from",
            "grades",
            "work_formats",
            "remote_scope",
            "vacancy_geography",
            "posted_within_days",
        }
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
        value = _optional_text(payload.get(key))
        if value and value.startswith(("http://", "https://")):
            return value
    return None


def _optional_raw_url(payload: dict[str, object], key: str) -> str | None:
    value = _optional_text(payload.get(key))
    return value if value and value.startswith(("http://", "https://")) else None


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
