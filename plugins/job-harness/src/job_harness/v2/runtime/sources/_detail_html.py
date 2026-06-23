"""Shared detail-page extraction helpers."""

from __future__ import annotations

import html
import json
import re

from job_harness.v2.runtime.sources._html import ScriptCollector, html_to_text

_JSON_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def extract_next_data(body: str) -> dict[str, object]:
    collector = ScriptCollector()
    collector.feed(body)
    for attrs, text in collector.scripts:
        if attrs.get("id") != "__NEXT_DATA__":
            continue
        value = json.loads(html.unescape(text))
        if isinstance(value, dict):
            return value
    raise ValueError("response does not contain __NEXT_DATA__")


def json_ld_job_posting_description(body: str) -> str | None:
    for match in _JSON_LD_RE.finditer(body):
        try:
            payload = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "JobPosting":
                continue
            description = item.get("description")
            if isinstance(description, str) and description.strip():
                return description.strip()
    return None


def localized_html_to_text(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("en", "ru", "am", "am_en"):
            text = localized_html_to_text(value.get(key))
            if text:
                return text
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return html_to_text(value)
