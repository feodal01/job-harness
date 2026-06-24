"""Self-contained HTML report renderer for v2 processed search artifacts."""

from __future__ import annotations

import json
from html import escape
from importlib.resources import files
from pathlib import Path
from typing import Any

from job_harness.v2.runtime.serialization import to_jsonable

_REPORT_TEMPLATE_FILENAME = "report_template.html"
_TITLE_TOKEN = "__TITLE__"
_PAYLOAD_TOKEN = "__PAYLOAD__"


def render_processed_results_html(payload: dict[str, object]) -> str:
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


def render_processed_results_html_file(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"processed results file is not a JSON object: {path}")
    return render_processed_results_html(payload)


def write_processed_results_html_file(payload: dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_processed_results_html(payload), encoding="utf-8")


def _report_template() -> str:
    return files("job_harness.v2.postprocessing").joinpath(_REPORT_TEMPLATE_FILENAME).read_text(encoding="utf-8")


def _script_json(payload: dict[str, object]) -> str:
    encoded = json.dumps(to_jsonable(payload), ensure_ascii=False, sort_keys=True)
    return encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
