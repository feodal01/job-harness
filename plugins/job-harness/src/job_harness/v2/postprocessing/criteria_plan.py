"""Criteria action planning for post-processing."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import (
    SearchCriterion,
    TextEnrichmentPolicy,
    TextField,
    search_criterion_descriptor,
)
from job_harness.v2.serialization import JsonObject


@dataclass(frozen=True)
class CriteriaProcessingPlanner:
    """Derive downstream actions from source attempt criteria diagnostics."""

    def build_plan(
        self,
        *,
        source_attempts: tuple[JsonObject, ...],
        rows: tuple[dict[str, object], ...],
    ) -> tuple[dict[str, object], ...]:
        text_evidence = _text_evidence_by_source_query_field(rows)
        return tuple(
            _source_attempt_plan(attempt, text_evidence)
            for attempt in source_attempts
        )


def _source_attempt_plan(
    attempt: dict[str, object],
    text_evidence: dict[tuple[str, str, TextField], bool],
) -> dict[str, object]:
    source = _text(attempt.get("source"))
    query_variant = _text(attempt.get("query_variant"))
    criteria = attempt.get("criteria")
    if not isinstance(criteria, dict):
        raise ValueError("source attempt record is missing criteria object")

    requested = _criterion_set(criteria.get("requested"), "requested")
    native_applied = _criterion_set(criteria.get("native_applied"), "native_applied")
    structured = _criterion_set(criteria.get("structured_evidence_available"), "structured_evidence_available")
    unsupported = _criterion_set(criteria.get("unsupported"), "unsupported")
    postprocess = _criterion_set(criteria.get("postprocess"), "postprocess")

    return {
        "source": source,
        "query_variant": query_variant,
        "outcome": _text(attempt.get("outcome")),
        "criteria": {
            "requested": _criterion_values(requested),
            "native_applied": _criterion_values(native_applied),
            "structured_evidence_available": _criterion_values(structured),
            "unsupported": _criterion_values(unsupported),
            "postprocess": _criterion_values(postprocess),
        },
        "actions": tuple(
            _criterion_action(
                criterion,
                source=source,
                query_variant=query_variant,
                native_applied=native_applied,
                structured=structured,
                unsupported=unsupported,
                postprocess=postprocess,
                text_evidence=text_evidence,
            )
            for criterion in sorted(requested, key=lambda item: item.value)
        ),
    }


def _criterion_action(
    criterion: SearchCriterion,
    *,
    source: str,
    query_variant: str,
    native_applied: frozenset[SearchCriterion],
    structured: frozenset[SearchCriterion],
    unsupported: frozenset[SearchCriterion],
    postprocess: frozenset[SearchCriterion],
    text_evidence: dict[tuple[str, str, TextField], bool],
) -> dict[str, object]:
    if criterion in native_applied:
        return {
            "criterion": criterion.value,
            "action": "none_native_request",
            "requires_enrichment": False,
            "reason": "source applied this criterion before returning listings",
        }
    if criterion in structured:
        return {
            "criterion": criterion.value,
            "action": "structured_postprocess",
            "requires_enrichment": False,
            "reason": "source emitted a stable structured field for downstream filtering",
        }
    if criterion in unsupported:
        return _unsupported_action(
            criterion,
            source=source,
            query_variant=query_variant,
            text_evidence=text_evidence,
        )
    if criterion in postprocess:
        return {
            "criterion": criterion.value,
            "action": "postprocess_without_source_diagnostic",
            "requires_enrichment": False,
            "reason": "criterion is marked for postprocessing but lacks a capability bucket",
        }
    return {
        "criterion": criterion.value,
        "action": "no_action",
        "requires_enrichment": False,
        "reason": "criterion was requested but no source action was declared",
    }


def _unsupported_action(
    criterion: SearchCriterion,
    *,
    source: str,
    query_variant: str,
    text_evidence: dict[tuple[str, str, TextField], bool],
) -> dict[str, object]:
    descriptor = search_criterion_descriptor(criterion)
    if descriptor.text_enrichment == TextEnrichmentPolicy.ALLOWED:
        has_text = _has_text_evidence(
            source=source,
            query_variant=query_variant,
            fields=descriptor.text_enrichment_fields,
            text_evidence=text_evidence,
        )
        return _text_enrichment_action(criterion, has_text)
    if descriptor.text_enrichment == TextEnrichmentPolicy.REQUIRES_STRUCTURED_EVIDENCE:
        return {
            "criterion": criterion.value,
            "action": "unsupported_requires_structured_evidence",
            "requires_enrichment": False,
            "reason": "criterion can only be processed from source structured fields",
        }
    return {
        "criterion": criterion.value,
        "action": "unsupported_not_applicable",
        "requires_enrichment": False,
        "reason": "criterion is not a vacancy fact that can be enriched downstream",
    }


def _text_enrichment_action(criterion: SearchCriterion, has_text: bool) -> dict[str, object]:
    if has_text:
        return {
            "criterion": criterion.value,
            "action": "text_enrichment_required",
            "requires_enrichment": True,
            "reason": "source does not expose a structured field, but collected vacancy text can be analyzed",
        }
    return {
        "criterion": criterion.value,
        "action": "missing_text_for_enrichment",
        "requires_enrichment": False,
        "reason": "source does not expose a structured field and no vacancy text was collected",
    }


def _has_text_evidence(
    *,
    source: str,
    query_variant: str,
    fields: tuple[TextField, ...],
    text_evidence: dict[tuple[str, str, TextField], bool],
) -> bool:
    return any(text_evidence.get((source, query_variant, field), False) for field in fields)


def _text_evidence_by_source_query_field(
    rows: tuple[dict[str, object], ...],
) -> dict[tuple[str, str, TextField], bool]:
    evidence: dict[tuple[str, str, TextField], bool] = {}
    for row in rows:
        source = _text(row["source"])
        query_variant = _text(row["query_variant"])
        for field in TextField:
            key = (source, query_variant, field)
            if evidence.get(key):
                continue
            evidence[key] = _field_has_text(row, field)
    return evidence


def _field_has_text(row: dict[str, object], field: TextField) -> bool:
    value = row[field.value]
    if isinstance(value, tuple):
        return any(bool(_text(item)) for item in value)
    return bool(_text(value))


def _criterion_set(value: object, field_name: str) -> frozenset[SearchCriterion]:
    if not isinstance(value, list):
        raise ValueError(f"criteria.{field_name} must be a list")
    result: set[SearchCriterion] = set()
    for item in value:
        text = _text(item).strip()
        if not text:
            raise ValueError(f"criteria.{field_name} contains a non-string or empty value")
        result.add(SearchCriterion(text))
    return frozenset(result)


def _criterion_values(criteria: frozenset[SearchCriterion]) -> tuple[str, ...]:
    return tuple(sorted(criterion.value for criterion in criteria))


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
