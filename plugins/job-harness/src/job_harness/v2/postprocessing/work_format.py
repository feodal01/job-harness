"""Work-format policy for processed result rows."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts import RemoteMode, SearchRequest
from job_harness.v2.geography import geography_matches_any, geography_text_keys

HYBRID_FORMAT = "hybrid"
OFFICE_FORMAT = "office"
REMOTE_FORMAT = "remote"

_HH_WORK_FORMATS = {
    "HYBRID": HYBRID_FORMAT,
    "ON_SITE": OFFICE_FORMAT,
    "REMOTE": REMOTE_FORMAT,
}
_HYBRID_MARKERS = frozenset({"hybrid", "гибрид", "гибкий", "комбинированный"})
_OFFICE_MARKERS = frozenset(
    {
        "office",
        "on site",
        "on-site",
        "onsite",
        "офис",
        "на месте работодателя",
    }
)
_REMOTE_MARKERS = frozenset({"remote", "удаленно", "удалённо"})
_LINKEDIN_WORKPLACE_TAGS = {
    "#li-hybrid": HYBRID_FORMAT,
    "#li-onsite": OFFICE_FORMAT,
    "#li-remote": REMOTE_FORMAT,
}
_WORK_FORMAT_RESTRICTIVENESS = {
    REMOTE_FORMAT: 0,
    HYBRID_FORMAT: 1,
    OFFICE_FORMAT: 2,
}


@dataclass(frozen=True)
class WorkFormatPolicyOutcome:
    handles_remote_filter: bool
    reasons: tuple[str, ...]


def listing_work_formats(listing: dict[str, object]) -> tuple[str, ...]:
    formats: list[str] = []
    raw = listing.get("raw")
    if isinstance(raw, dict):
        _append_hh_work_formats(formats, raw.get("workFormats"))
        for key in ("work_format", "remote_type", "employment_type"):
            _append_work_format_text(formats, raw.get(key))
        if formats:
            return _most_restrictive_formats(formats)
        _append_linkedin_workplace_tags(formats, raw.get("linkedin_workplace_tags"))
        if formats:
            return _most_restrictive_formats(formats)

    remote_global = _optional_bool(listing.get("remote_global"))
    remote_in_country = _optional_bool(listing.get("remote_in_country"))
    if remote_global is True or remote_in_country is True:
        _append_unique(formats, REMOTE_FORMAT)
    if remote_global is False and remote_in_country is False:
        _append_unique(formats, OFFICE_FORMAT)
    return _most_restrictive_formats(formats)


def work_format_policy_outcome(
    *,
    request: SearchRequest,
    work_formats: tuple[str, ...],
    countries: tuple[str, ...],
) -> WorkFormatPolicyOutcome:
    if request.remote_mode != RemoteMode.COMPATIBLE_REMOTE:
        return WorkFormatPolicyOutcome(handles_remote_filter=False, reasons=())

    accepted_formats: list[str] = []
    if request.hybrid_ok and HYBRID_FORMAT in work_formats:
        accepted_formats.append(HYBRID_FORMAT)
    if request.office_ok and OFFICE_FORMAT in work_formats:
        accepted_formats.append(OFFICE_FORMAT)
    if not accepted_formats:
        return WorkFormatPolicyOutcome(handles_remote_filter=False, reasons=())

    if not countries:
        return WorkFormatPolicyOutcome(
            handles_remote_filter=True,
            reasons=tuple(f"{work_format}_geography_unknown" for work_format in accepted_formats),
        )
    if request.vacancy_geographies and not _geography_sets_intersect(
        request.work_from_geographies,
        request.vacancy_geographies,
    ):
        return WorkFormatPolicyOutcome(
            handles_remote_filter=True,
            reasons=tuple(f"{work_format}_geography_mismatch" for work_format in accepted_formats),
        )
    if any(geography_matches_any(country, request.work_from_geographies) for country in countries):
        return WorkFormatPolicyOutcome(handles_remote_filter=True, reasons=())
    return WorkFormatPolicyOutcome(
        handles_remote_filter=True,
        reasons=tuple(f"{work_format}_geography_mismatch" for work_format in accepted_formats),
    )


def row_work_formats(row: dict[str, object]) -> tuple[str, ...]:
    value = row.get("work_formats")
    if isinstance(value, tuple):
        return tuple(_text(item) for item in value if _text(item))
    if isinstance(value, list):
        return tuple(_text(item) for item in value if _text(item))
    return ()


def _geography_sets_intersect(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return any(geography_matches_any(geography, right) for geography in left)


def _append_hh_work_formats(formats: list[str], value: object) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        work_format = _HH_WORK_FORMATS.get(_text(item))
        if work_format:
            _append_unique(formats, work_format)


def _append_work_format_text(formats: list[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        tokens = _format_tokens(value)
        if _HYBRID_MARKERS & tokens:
            _append_unique(formats, HYBRID_FORMAT)
        if _OFFICE_MARKERS & tokens:
            _append_unique(formats, OFFICE_FORMAT)
        if _REMOTE_MARKERS & tokens:
            _append_unique(formats, REMOTE_FORMAT)
    if isinstance(value, list):
        for item in value:
            _append_work_format_text(formats, item)
    if isinstance(value, tuple):
        for item in value:
            _append_work_format_text(formats, item)


def _append_linkedin_workplace_tags(formats: list[str], value: object) -> None:
    if isinstance(value, str) and value.strip():
        work_format = _LINKEDIN_WORKPLACE_TAGS.get(value.casefold())
        if work_format:
            _append_unique(formats, work_format)
    if isinstance(value, list):
        for item in value:
            _append_linkedin_workplace_tags(formats, item)
    if isinstance(value, tuple):
        for item in value:
            _append_linkedin_workplace_tags(formats, item)


def _format_tokens(value: str) -> frozenset[str]:
    tokens: list[str] = []
    for key in geography_text_keys(value):
        tokens.append(key)
        tokens.extend(part for part in key.split() if part)
    return frozenset(tokens)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _most_restrictive_formats(values: list[str]) -> tuple[str, ...]:
    if not values:
        return ()
    return (
        max(
            values,
            key=lambda value: _WORK_FORMAT_RESTRICTIVENESS.get(value, -1),
        ),
    )


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("expected optional boolean field")
    return value


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
