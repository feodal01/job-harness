"""Self-contained HTML report renderer for v2 processed search artifacts."""

from __future__ import annotations

import json
from html import escape
from importlib.resources import files
from typing import Any

from job_harness.v2.serialization import JsonObject, to_jsonable

_REPORT_TEMPLATE_FILENAME = "report_template.html"
_TITLE_TOKEN = "__TITLE__"
_PAYLOAD_TOKEN = "__PAYLOAD__"


def render_processed_results_html(payload: JsonObject) -> str:
    if payload.get("record_type") != "processed_results":
        raise ValueError("expected processed_results payload")

    run_id = _text(payload.get("run_id")) or "unknown"
    return _report_template().replace(
        _TITLE_TOKEN,
        escape(f"Job search results {run_id}"),
    ).replace(
        _PAYLOAD_TOKEN,
        _script_json(payload),
    )


def _report_template() -> str:
    return files("job_harness.v2.presentation").joinpath(_REPORT_TEMPLATE_FILENAME).read_text(encoding="utf-8")


def _script_json(payload: JsonObject) -> str:
    encoded = json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True)
    return encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
