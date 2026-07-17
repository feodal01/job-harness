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
_INTERNAL_KEYS = {
    "derived_facts",
    "target_provider_id",
    "location",
    "salary",
    "work_formats",
    "remote_scopes",
    "native_grade",
    "relocation",
    "relocation_destinations",
    "visa_sponsorship",
    "raw",
    "raw_text",
}


def public_vacancy_projection(facts: JsonObject) -> JsonObject:
    projected = _project_object(facts)
    derived = facts.get("derived_facts")
    selection = derived.get("structured-selection-facts") if isinstance(derived, dict) else None
    if isinstance(selection, dict):
        _project_selection_facts(projected, selection)
    canonical_url = projected.pop("canonicalVacancyUrl", None)
    if isinstance(canonical_url, str):
        projected["vacancyUrl"] = canonical_url
    return projected


def _project_selection_facts(projected: JsonObject, selection: JsonObject) -> None:
    location = _public_fact(selection.get("location"), drop=("evidence",))
    if location:
        projected["location"] = _camelize_fact(location)
    workplace = _public_fact(selection.get("workplace"), drop=("evidence",))
    if workplace:
        projected["workplace"] = _camelize_fact(workplace)
    grade = _public_fact(
        selection.get("grade"),
        keep=("resolved", "conflict"),
    )
    if grade:
        projected["grade"] = grade
    compensation = _public_fact(selection.get("compensation"), drop=("evidence",))
    if compensation and any(value is not None for value in compensation.values()):
        projected["compensation"] = compensation
    relocation = _public_fact(selection.get("relocation"), drop=("evidence",))
    if relocation and isinstance(relocation.get("supported"), bool):
        projected["relocation"] = relocation
    visa_sponsorship = _object(selection.get("visa_sponsorship"))
    if visa_sponsorship and isinstance(visa_sponsorship.get("supported"), bool):
        projected["visaSponsorshipAvailable"] = visa_sponsorship["supported"]
    employer_geographies = selection.get("employer_geographies")
    if isinstance(employer_geographies, list) and employer_geographies:
        projected["employerGeographies"] = list(employer_geographies)


def _public_fact(
    value: object,
    *,
    keep: tuple[str, ...] | None = None,
    drop: tuple[str, ...] = (),
) -> JsonObject:
    fact = _object(value)
    if fact is None:
        return {}
    allowed = set(keep) if keep is not None else None
    return {
        key: _project_value(item)
        for key, item in fact.items()
        if key not in drop
        and (allowed is None or key in allowed)
        and not _is_empty(item)
    }


def _camelize_fact(value: JsonObject) -> JsonObject:
    aliases = {
        "raw_text": "rawText",
        "remote_scopes": "remoteScopes",
    }
    return {aliases.get(key, key): item for key, item in value.items()}


def _object(value: object) -> JsonObject | None:
    return value if isinstance(value, dict) else None


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
