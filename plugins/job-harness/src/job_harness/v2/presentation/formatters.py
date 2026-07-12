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

    url = _text(item.get("vacancyUrl"))
    if url:
        lines.append(f"[Open vacancy]({url})")
        lines.append("")

    meta = _listing_meta_lines(item)
    if meta:
        lines.extend(meta)
        lines.append("")

    lines.extend(_listing_application_channel_lines(item))
    lines.extend(_listing_company_contact_lines(item))
    lines.extend(_listing_skills_lines(item))
    lines.extend(_listing_body_lines(item, description_limit=description_limit))
    lines.extend(_listing_diagnostic_lines(item))
    return lines


def _listing_meta_lines(item: dict[str, object]) -> list[str]:
    meta: list[str] = []
    company = item.get("company")
    company_name = _text(company.get("name")) if isinstance(company, dict) else ""
    if company_name:
        meta.append(f"**Company:** {company_name}")
    for key, label in (
        ("sourceId", "**Source:** `{}`"),
        ("nativeGrade", "**Grade:** {}"),
        ("postedAt", "**Posted:** {}"),
    ):
        value = _text(item.get(key))
        if value:
            meta.append(label.format(value))

    salary = _salary_text(item.get("salary"))
    if salary:
        meta.append(f"**Salary:** {salary}")

    raw_location = item.get("location")
    location = _text(raw_location.get("text")) if isinstance(raw_location, dict) else ""
    if location:
        meta.append(f"**Location:** {location}")

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
    channels = item.get("applicationChannels")
    if not isinstance(channels, list) or not channels:
        return []
    lines = ["**Apply channels**", ""]
    has_channel_lines = False
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        label = _text(channel.get("label")) or _text(channel.get("kind"))
        url = _text(channel.get("value"))
        if not label or not url:
            continue
        lines.append(f"- [{label}]({url})")
        has_channel_lines = True
    if not has_channel_lines:
        return []
    lines.append("")
    return lines


def _listing_company_contact_lines(item: dict[str, object]) -> list[str]:
    contacts = item.get("contacts")
    if not isinstance(contacts, list) or not contacts:
        return []
    lines = ["**Company contacts**", ""]
    has_contact_lines = False
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        value = _text(contact.get("value"))
        kind = _text(contact.get("kind"))
        label = _text(contact.get("label")) or kind
        url = _contact_url(kind, value)
        if not label or not value:
            continue
        if url:
            lines.append(f"- {label}: [{value}]({url})")
        else:
            lines.append(f"- {label}: {value}")
        has_contact_lines = True
    if not has_contact_lines:
        return []
    lines.append("")
    return lines


def _listing_body_lines(item: dict[str, object], *, description_limit: int) -> list[str]:
    lines: list[str] = []
    description = _text(item.get("description"))
    _append_body_section(lines, "Description", description, description_limit=description_limit)

    requirements = _body_text(item.get("requirements"))
    if requirements and requirements != description:
        _append_body_section(lines, "Requirements", requirements, description_limit=description_limit)

    for key, heading in (
        ("responsibilities", "Responsibilities"),
        ("conditions", "Conditions"),
    ):
        body = _body_text(item.get(key))
        if body:
            _append_body_section(lines, heading, body, description_limit=description_limit)
    return lines


def _listing_diagnostic_lines(item: dict[str, object]) -> list[str]:
    confidence = _text(item.get("duplicateConfidence"))
    return [] if not confidence else [f"**Duplicate confidence:** `{confidence}`", ""]


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
    formats = item.get("workFormats")
    values = [_text(value) for value in formats] if isinstance(formats, list) else []
    return ", ".join(value for value in values if value) or None


def _salary_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    salary_from = _text(value.get("from"))
    salary_to = _text(value.get("to"))
    currency = _text(value.get("currency"))
    bounds = " - ".join(item for item in (salary_from, salary_to) if item)
    return " ".join(item for item in (bounds, currency) if item)


def _body_text(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value if _text(item))
    return _text(value)


def _contact_url(kind: str, value: str) -> str:
    if kind == "email":
        return f"mailto:{value}"
    if kind == "phone":
        return f"tel:{value}"
    if value.startswith(("http://", "https://")):
        return value
    return ""


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
