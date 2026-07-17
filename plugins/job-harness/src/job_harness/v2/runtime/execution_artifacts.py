"""Payload builders for durable graph execution artifacts."""

from __future__ import annotations

from job_harness.v2.contracts import SearchRequest
from job_harness.v2.runtime.run_layout import RunPaths
from job_harness.v2.serialization import JsonObject, to_jsonable


def processed_payload(
    *,
    run_id: str,
    execution_id: str,
    append_sequence: int,
    request: SearchRequest,
    final_items: tuple[JsonObject, ...],
    filtered_items: tuple[JsonObject, ...],
    raw_records_read: int,
    execution_quality: str,
    source_coverage: JsonObject,
) -> JsonObject:
    return {
        "schema_version": 2,
        "record_type": "processed_results",
        "phase": "final",
        "run_id": run_id,
        "execution_id": execution_id,
        "append_sequence": append_sequence,
        "execution_quality": execution_quality,
        "source_coverage": source_coverage,
        "search_request": _json_object(request),
        "raw_records_read": raw_records_read,
        "result_count": len(final_items),
        "results": list(final_items),
        "filtered_out_results": list(filtered_items),
    }


def execution_receipt(
    *,
    paths: RunPaths,
    execution_id: str,
    enrichment_execution_id: str,
    discovered_search_execution_id: str,
    append_sequence: int,
    diagnostics: JsonObject,
    enrichment_diagnostics: JsonObject,
    discovered_search_diagnostics: JsonObject,
) -> JsonObject:
    return {
        "schema_version": 2,
        "record_type": "v2_search_execution",
        "run_id": paths.run_id,
        "execution_id": execution_id,
        "append_sequence": append_sequence,
        "execution_quality": required_text(diagnostics, "execution_quality"),
        "run_dir": str(paths.run_dir),
        "artifacts": {
            "database": str(paths.database_path),
            "execution_json": str(paths.execution_json_path),
            "report_html": str(paths.report_html_path),
            "search_results_json": str(paths.search_results_json_path),
            "enrichment_results_json": str(paths.enrichment_results_json_path),
            "discovered_search_results_json": str(
                paths.discovered_search_results_json_path
            ),
        },
        "result_count": required_int(diagnostics, "result_count"),
        "diagnostics": diagnostics,
        "enrichment": {
            "execution_id": enrichment_execution_id,
            "diagnostics": enrichment_diagnostics,
        },
        "discovered_search": {
            "execution_id": discovered_search_execution_id,
            "diagnostics": discovered_search_diagnostics,
        },
    }


def execution_snapshot_payload(
    *,
    run_id: str,
    execution_id: str,
    execution_kind: str,
    append_sequence: int,
    items: tuple[JsonObject, ...],
    diagnostics: JsonObject,
) -> JsonObject:
    return {
        "schema_version": 2,
        "record_type": "execution_results",
        "execution_kind": execution_kind,
        "run_id": run_id,
        "execution_id": execution_id,
        "append_sequence": append_sequence,
        "execution_quality": required_text(diagnostics, "execution_quality"),
        "result_count": len(items),
        "results": list(items),
        "diagnostics": diagnostics,
    }


def required_int(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    return value


def required_text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be non-empty text")
    return value


def required_object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return value


def _json_object(value: object) -> JsonObject:
    payload = to_jsonable(value)
    if not isinstance(payload, dict):
        raise TypeError("value must serialize to a JSON object")
    return payload
