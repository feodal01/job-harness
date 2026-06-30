"""Human-readable export formatters for v2 processed search artifacts."""

from __future__ import annotations

from typing import Any

from job_harness.v2.serialization import JsonObject


def render_processed_results_markdown(
    payload: JsonObject,
    *,
    description_limit: int = 0,
    listing_limit: int = 0,
) -> str:
    if payload.get("record_type") != "processed_results":
        raise ValueError("expected processed_results payload")

    lines: list[str] = []
    run_id = _text(payload.get("run_id")) or "unknown"
    lines.append(f"# Job search results — `{run_id}`")
    lines.append("")
    lines.append(
        " | ".join(
            part
            for part in (
                f"**Shown:** {_shown_count(payload, listing_limit)}",
                f"**Processed:** {payload.get('result_count', 0)}",
                f"**Raw read:** {payload.get('raw_records_read', 0)}",
                f"**Append:** {payload.get('append_sequence', 0)}",
            )
            if part
        )
    )

    removed_counts = payload.get("removed_counts")
    if isinstance(removed_counts, dict) and removed_counts:
        removed = ", ".join(f"{key}: {value}" for key, value in sorted(removed_counts.items()))
        lines.append(f"**Filtered out:** {removed}")
    if listing_limit > 0 and _result_count(payload) > listing_limit:
        lines.append(f"**Note:** markdown preview limited to first `{listing_limit}` listings")
    lines.append("")
    lines.append("---")
    lines.append("")

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        lines.append("_No listings matched the processed filters._")
        return "\n".join(lines)

    visible_results = results if listing_limit <= 0 else results[:listing_limit]
    for index, item in enumerate(visible_results, start=1):
        if not isinstance(item, dict):
            continue
        lines.extend(_render_listing(index, item, description_limit=description_limit))
        lines.append("---")
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    if lines and lines[-1] == "---":
        lines.pop()
    return "\n".join(lines)


def _result_count(payload: JsonObject) -> int:
    results = payload.get("results")
    if isinstance(results, list):
        return len(results)
    raw_count = payload.get("result_count")
    if isinstance(raw_count, int):
        return raw_count
    return 0


def _shown_count(payload: JsonObject, listing_limit: int) -> int:
    total = _result_count(payload)
    if listing_limit <= 0:
        return total
    return min(total, listing_limit)


def _render_listing(index: int, item: dict[str, object], *, description_limit: int) -> list[str]:
    title = _text(item.get("title")) or "Untitled"
    lines = [f"## {index}. {title}", ""]

    url = _text(item.get("url"))
    if url:
        lines.append(f"[Open vacancy]({url})")
        lines.append("")

    meta = _listing_meta_lines(item)
    if meta:
        lines.extend(meta)
        lines.append("")

    lines.extend(_listing_application_channel_lines(item))
    lines.extend(_listing_skills_lines(item))
    lines.extend(_listing_body_lines(item, description_limit=description_limit))
    lines.extend(_listing_diagnostic_lines(item))
    return lines


def _listing_meta_lines(item: dict[str, object]) -> list[str]:
    meta: list[str] = []
    for key, label in (
        ("company", "**Company:** {}"),
        ("source", "**Source:** `{}`"),
        ("native_grade", "**Grade:** {}"),
        ("salary_text", "**Salary:** {}"),
        ("posted_at", "**Posted:** {}"),
        ("query_variant", "**Query variant:** `{}`"),
    ):
        value = _text(item.get(key))
        if value:
            meta.append(label.format(value))

    location = _text(item.get("location_text")) or _text(item.get("city"))
    if location:
        meta.append(f"**Location:** {location}")

    country = _text(item.get("country"))
    if country:
        meta.append(f"**Country:** {country}")

    work_mode = _work_mode(item)
    if work_mode:
        meta.append(f"**Format:** {work_mode}")
    return meta


def _listing_skills_lines(item: dict[str, object]) -> list[str]:
    skills = item.get("skills")
    if not isinstance(skills, list) or not skills:
        return []
    skill_text = ", ".join(_text(skill) for skill in skills if _text(skill))
    if not skill_text:
        return []
    return [f"**Skills:** {skill_text}", ""]


def _listing_application_channel_lines(item: dict[str, object]) -> list[str]:
    channels = item.get("application_channels")
    if not isinstance(channels, list) or not channels:
        return []
    lines = ["**Apply channels**", ""]
    has_channel_lines = False
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        label = _text(channel.get("label"))
        url = _text(channel.get("url"))
        if not label or not url:
            continue
        lines.append(f"- [{label}]({url})")
        has_channel_lines = True
    if not has_channel_lines:
        return []
    lines.append("")
    return lines


def _listing_body_lines(item: dict[str, object], *, description_limit: int) -> list[str]:
    lines: list[str] = []
    description = _text(item.get("description"))
    _append_body_section(lines, "Description", description, description_limit=description_limit)

    requirements = _text(item.get("requirements"))
    if requirements and requirements != description:
        _append_body_section(lines, "Requirements", requirements, description_limit=description_limit)

    additional_sections = item.get("additional_sections")
    if isinstance(additional_sections, dict):
        for section_title, section_body in additional_sections.items():
            body = _text(section_body)
            if body:
                _append_body_section(lines, _text(section_title), body, description_limit=description_limit)
    return lines


def _listing_diagnostic_lines(item: dict[str, object]) -> list[str]:
    diagnostics: list[str] = []
    for key, label in (
        ("description_availability", "**Description status:** `{}`"),
        ("detail_parse_error", "**Detail parse error:** {}"),
    ):
        value = _text(item.get(key))
        if value:
            diagnostics.append(label.format(value))
    if not diagnostics:
        return []
    return ["**Diagnostics**", "", *diagnostics, ""]


def _append_body_section(
    lines: list[str],
    heading: str,
    body: str,
    *,
    description_limit: int,
) -> None:
    if not body:
        return
    lines.append(f"**{heading}**")
    lines.append("")
    lines.append(_truncate(body, description_limit))
    lines.append("")


def _work_mode(item: dict[str, object]) -> str | None:
    display_work_format = _text(item.get("display_work_format"))
    if display_work_format:
        return display_work_format
    remote_scope = _text(item.get("remote_scope"))
    if remote_scope:
        return remote_scope
    if item.get("relocation") is True:
        return "relocation"
    return None


def _truncate(text: str, limit: int) -> str:
    cleaned = text.strip()
    if limit <= 0 or len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()
