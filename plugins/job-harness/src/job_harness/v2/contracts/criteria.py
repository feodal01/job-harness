"""Search criterion metadata used across v2 layers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from job_harness.v2.contracts.enums import ALL_SEARCH_CRITERIA, SearchCriterion, TextField


class TextEnrichmentPolicy(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    ALLOWED = "allowed"
    REQUIRES_STRUCTURED_EVIDENCE = "requires_structured_evidence"


@dataclass(frozen=True)
class SearchCriterionDescriptor:
    criterion: SearchCriterion
    request_field: str
    source_fact_fields: tuple[str, ...]
    text_enrichment: TextEnrichmentPolicy
    text_enrichment_fields: tuple[TextField, ...] = ()

    def __post_init__(self) -> None:
        if not self.request_field:
            raise ValueError("request_field must be non-empty")
        if self.text_enrichment == TextEnrichmentPolicy.ALLOWED and not self.text_enrichment_fields:
            raise ValueError("text-enrichable criteria must declare text_enrichment_fields")
        if self.text_enrichment != TextEnrichmentPolicy.ALLOWED and self.text_enrichment_fields:
            raise ValueError("text_enrichment_fields require ALLOWED text_enrichment policy")


SEARCH_CRITERION_DESCRIPTORS: tuple[SearchCriterionDescriptor, ...] = (
    SearchCriterionDescriptor(
        criterion=SearchCriterion.QUERY,
        request_field="query_variants",
        source_fact_fields=("title", "description", "requirements", "skills", "raw_text"),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.TITLE,
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.SKILLS,
            TextField.RAW_TEXT,
        ),
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.GRADES,
        request_field="grades",
        source_fact_fields=("native_grade",),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.TITLE,
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.SKILLS,
            TextField.RAW_TEXT,
        ),
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.SALARY_FROM,
        request_field="salary_from",
        source_fact_fields=("salary_text", "salary_min", "salary_max", "salary_currency"),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.RAW_TEXT,
        ),
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.PUBLISHED_SINCE,
        request_field="published_since",
        source_fact_fields=("posted_at",),
        text_enrichment=TextEnrichmentPolicy.REQUIRES_STRUCTURED_EVIDENCE,
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.RELOCATION,
        request_field="relocation",
        source_fact_fields=("relocation",),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.RAW_TEXT,
        ),
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.REMOTE_IN_COUNTRY,
        request_field="remote_in_country",
        source_fact_fields=("remote_in_country", "location_text"),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.RAW_TEXT,
        ),
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.REMOTE_GLOBAL,
        request_field="remote_global",
        source_fact_fields=("remote_global", "location_text"),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.RAW_TEXT,
        ),
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.COUNTRIES,
        request_field="countries",
        source_fact_fields=("country", "location_text"),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.RAW_TEXT,
        ),
    ),
    SearchCriterionDescriptor(
        criterion=SearchCriterion.CITIES,
        request_field="cities",
        source_fact_fields=("city", "location_text"),
        text_enrichment=TextEnrichmentPolicy.ALLOWED,
        text_enrichment_fields=(
            TextField.DESCRIPTION,
            TextField.REQUIREMENTS,
            TextField.RAW_TEXT,
        ),
    ),
)

_SEARCH_CRITERION_DESCRIPTOR_INDEX = {
    descriptor.criterion: descriptor
    for descriptor in SEARCH_CRITERION_DESCRIPTORS
}


def all_search_criterion_descriptors() -> tuple[SearchCriterionDescriptor, ...]:
    return SEARCH_CRITERION_DESCRIPTORS


def search_criterion_descriptor(criterion: SearchCriterion) -> SearchCriterionDescriptor:
    return _SEARCH_CRITERION_DESCRIPTOR_INDEX[criterion]


def _validate_descriptors() -> None:
    seen = set(_SEARCH_CRITERION_DESCRIPTOR_INDEX)
    expected = set(ALL_SEARCH_CRITERIA)
    if seen != expected:
        missing = ", ".join(sorted(criterion.value for criterion in expected - seen))
        extra = ", ".join(sorted(criterion.value for criterion in seen - expected))
        raise ValueError(f"criterion descriptors must match SearchCriterion enum: missing={missing}; extra={extra}")
    if len(SEARCH_CRITERION_DESCRIPTORS) != len(seen):
        raise ValueError("criterion descriptors must not contain duplicate criteria")


_validate_descriptors()
