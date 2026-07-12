"""Stable public projection for final vacancy rows."""

from __future__ import annotations

from job_harness.v2.serialization import JsonObject

_ALIASES = {
    "source_id": "sourceId",
    "source_listing_id": "sourceListingId",
    "work_formats": "workFormats",
    "remote_scopes": "remoteScopes",
    "native_grade": "nativeGrade",
    "posted_at": "postedAt",
    "vacancy_url": "vacancyUrl",
    "apply_url": "applyUrl",
    "salary_from": "from",
    "salary_to": "to",
    "target_provider_id": "targetProviderId",
    "profile_url": "profileUrl",
    "official_site_url": "officialSiteUrl",
    "source_vacancies_url": "sourceVacanciesUrl",
    "source_company_id": "sourceCompanyId",
    "canonical_vacancy_url": "canonicalVacancyUrl",
    "employment_types": "employmentTypes",
    "application_channels": "applicationChannels",
    "company_name": "companyName",
    "size_text": "sizeText",
    "career_endpoints": "careerEndpoints",
    "social_links": "socialLinks",
    "canonical_site_url": "canonicalSiteUrl",
    "provider_hint": "providerHint",
    "discovery_method": "discoveryMethod",
}
_INTERNAL_KEYS = {"derived_facts", "target_provider_id"}


def public_vacancy_projection(facts: JsonObject) -> JsonObject:
    projected = _project_object(facts)
    canonical_url = projected.pop("canonicalVacancyUrl", None)
    if isinstance(canonical_url, str):
        projected["vacancyUrl"] = canonical_url
    return projected


def _project_object(value: JsonObject) -> JsonObject:
    projected: JsonObject = {}
    for key, item in value.items():
        if key in _INTERNAL_KEYS or _is_empty(item):
            continue
        projected[_ALIASES.get(key, key)] = _project_value(item)
    return projected


def _project_value(value: object) -> object:
    if isinstance(value, dict):
        return _project_object(value)
    if isinstance(value, list | tuple):
        return [_project_value(item) for item in value]
    return value


def _is_empty(value: object) -> bool:
    return value is None or value in ("", [], (), {})
