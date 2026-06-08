"""Deterministic grade assessment for job listings."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from job_harness.models import JobListing
from job_harness.types import FilterSupport

ExperienceLevel = Literal["junior", "middle", "senior"]
ExperienceOrigin = Literal["native", "estimated", "unknown"]
ExperienceConfidence = Literal["high", "medium", "low", "none"]

VALID_EXPERIENCE_LEVELS: tuple[ExperienceLevel, ...] = ("junior", "middle", "senior")
_VALID_LEVEL_SET = set(VALID_EXPERIENCE_LEVELS)

_DIRECT_MARKERS: tuple[tuple[ExperienceLevel, tuple[str, ...]], ...] = (
    ("senior", ("senior", "сеньор", "синьор", "lead", "ведущий")),
    ("middle", ("middle", "мидл", "mid-level", "mid level")),
    ("junior", ("junior", "джун", "стажер", "стажёр", "intern", "trainee")),
)

_NO_EXPERIENCE_RE = re.compile(r"\b(no experience|without experience)\b|без\s+опыта|нет\s+опыта", re.I)
_RANGE_1_3_RE = re.compile(r"(?:1\s*[-–]\s*3|от\s+1[^\d]{0,20}до\s+3)", re.I)
_RANGE_3_6_RE = re.compile(r"(?:3\s*[-–]\s*6|от\s+3[^\d]{0,20}до\s+6)", re.I)
_RANGE_6_PLUS_RE = re.compile(r"(?:6\s*\+|от\s+6|\b6[^\d]{0,10}(?:years?|лет))", re.I)
_EXPLICIT_MULTI_RE = re.compile(
    r"(junior|middle|senior|intern|lead|джун|мидл|сеньор|синьор)"
    r"\s*(?:/|\\|\+|,|\bor\b|\band\b|\bили\b|\bи\b|-|–|to)\s*"
    r"(junior|middle|senior|intern|lead|джун|мидл|сеньор|синьор)",
    re.I,
)


@dataclass(frozen=True)
class ExperienceAssessment:
    levels: tuple[ExperienceLevel, ...]
    origin: ExperienceOrigin
    confidence: ExperienceConfidence
    evidence: tuple[str, ...]


def parse_experience_levels(
    value: Iterable[str] | None,
    *,
    allow_empty: bool = False,
) -> tuple[ExperienceLevel, ...]:
    """Validate and normalize an exact grade list."""
    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError("experience_levels must be a list of junior, middle, senior")

    levels: list[ExperienceLevel] = []
    invalid: list[str] = []
    for raw in value:
        item = str(raw).strip().lower()
        if not item:
            continue
        if item not in _VALID_LEVEL_SET:
            invalid.append(str(raw))
            continue
        level = item
        if level not in levels:
            levels.append(level)

    if invalid:
        allowed = ", ".join(VALID_EXPERIENCE_LEVELS)
        raise ValueError(f"invalid experience_levels: {', '.join(invalid)}; allowed: {allowed}")
    if not levels and not allow_empty:
        raise ValueError("experience_levels must contain at least one level")
    return tuple(levels)


def parse_experience_levels_csv(value: str | None) -> tuple[ExperienceLevel, ...]:
    if value is None:
        return ()
    return parse_experience_levels(part.strip() for part in value.split(","))


def assess_listing_experience(
    listing: JobListing,
    source: str,
    support: FilterSupport | str,
) -> ExperienceAssessment:
    """Return a deterministic grade assessment for one listing."""
    support = _coerce_support(support)
    native = _native_level(listing, support)
    if native is not None:
        return ExperienceAssessment(
            levels=(native,),
            origin="native",
            confidence="high",
            evidence=(f"{source}: native {native}",),
        )

    return _estimate_listing_experience(
        listing,
        include_parsed_experience=support in (FilterSupport.SERVER, FilterSupport.CLIENT),
    )


def annotate_listing_experience(
    listing: JobListing,
    source: str,
    support: FilterSupport | str,
) -> JobListing:
    assessment = assess_listing_experience(listing, source, support)
    listing.experience_levels = list(assessment.levels)
    listing.experience_origin = assessment.origin
    listing.experience_confidence = assessment.confidence
    listing.experience_evidence = list(assessment.evidence)
    return listing


def experience_matches(listing: JobListing, requested: Iterable[str]) -> bool:
    requested_levels = set(parse_experience_levels(requested))
    if listing.experience_origin == "unknown":
        return True
    return bool(requested_levels.intersection(listing.experience_levels))


def experience_match_rank(listing: JobListing, requested: Iterable[str]) -> int:
    requested_levels = set(parse_experience_levels(requested))
    if bool(requested_levels.intersection(listing.experience_levels)):
        return 0
    if listing.experience_origin == "unknown":
        return 1
    return 2


def _native_level(listing: JobListing, support: FilterSupport) -> ExperienceLevel | None:
    if support not in (FilterSupport.SERVER, FilterSupport.CLIENT):
        return None
    raw = (listing.experience or "").strip().lower()
    return raw if raw in _VALID_LEVEL_SET else None


def _coerce_support(value: FilterSupport | str) -> FilterSupport:
    if isinstance(value, FilterSupport):
        return value
    return FilterSupport(str(value))


def _estimate_listing_experience(
    listing: JobListing,
    *,
    include_parsed_experience: bool,
) -> ExperienceAssessment:
    chunks = _evidence_chunks(
        listing,
        include_parsed_experience=include_parsed_experience,
    )
    scores: dict[ExperienceLevel, int] = {level: 0 for level in VALID_EXPERIENCE_LEVELS}
    evidence: dict[ExperienceLevel, list[str]] = {level: [] for level in VALID_EXPERIENCE_LEVELS}

    for label, text, weight in chunks:
        signals = _signals_from_text(text)
        if not signals:
            continue
        if len(signals) > 1 and _has_explicit_multi(text):
            found = tuple(level for level in VALID_EXPERIENCE_LEVELS if level in signals)
            return ExperienceAssessment(
                levels=found,
                origin="estimated",
                confidence="high" if weight >= 3 else "medium",
                evidence=tuple(f"{label}: explicit multi-grade" for _ in found)[:3],
            )
        for level in signals:
            scores[level] += weight
            if len(evidence[level]) < 3:
                evidence[level].append(f"{label}: {level}")

    winners = _winning_levels(scores)
    if not winners:
        return ExperienceAssessment((), "unknown", "none", ())
    if len(winners) > 1:
        return ExperienceAssessment(
            (),
            "unknown",
            "none",
            tuple(f"conflict: {level}={scores[level]}" for level in winners),
        )

    winner = winners[0]
    score = scores[winner]
    confidence: ExperienceConfidence = "high" if score >= 4 else "medium"
    return ExperienceAssessment(
        levels=(winner,),
        origin="estimated",
        confidence=confidence,
        evidence=tuple(evidence[winner]),
    )


def _evidence_chunks(
    listing: JobListing,
    *,
    include_parsed_experience: bool,
) -> list[tuple[str, str, int]]:
    chunks: list[tuple[str, str, int]] = []
    if include_parsed_experience and listing.experience:
        chunks.append(("parsed_experience", listing.experience, 4))
    if listing.title:
        chunks.append(("title", listing.title, 3))
    raw_values = " ".join(str(value) for value in listing.raw.values() if value is not None)
    if raw_values:
        chunks.append(("raw", raw_values, 2))
    if listing.requirements:
        chunks.append(("requirements", listing.requirements, 2))
    if listing.description:
        chunks.append(("description", listing.description, 1))
    if listing.skills:
        chunks.append(("skills", " ".join(listing.skills), 1))
    return chunks


def _signals_from_text(text: str) -> set[ExperienceLevel]:
    lower = text.casefold()
    signals: set[ExperienceLevel] = set()
    if _NO_EXPERIENCE_RE.search(lower):
        signals.add("junior")
    if _RANGE_1_3_RE.search(lower):
        signals.add("middle")
    if _RANGE_3_6_RE.search(lower) or _RANGE_6_PLUS_RE.search(lower):
        signals.add("senior")

    for level, markers in _DIRECT_MARKERS:
        if any(_contains_marker(lower, marker) for marker in markers):
            signals.add(level)
    return signals


def _has_explicit_multi(text: str) -> bool:
    return bool(_EXPLICIT_MULTI_RE.search(text.casefold()))


def _winning_levels(scores: dict[ExperienceLevel, int]) -> list[ExperienceLevel]:
    top_score = max(scores.values())
    if top_score < 2:
        return []
    leaders = [level for level, score in scores.items() if score == top_score]
    if len(leaders) > 1:
        return leaders
    runner_up = max(score for level, score in scores.items() if level != leaders[0])
    if runner_up and top_score - runner_up < 2:
        return [leaders[0], *[level for level, score in scores.items() if score == runner_up]]
    return leaders


def _contains_marker(text: str, marker: str) -> bool:
    escaped = re.escape(marker.casefold())
    if re.search(r"[a-zа-яё0-9]", marker, re.I):
        return bool(re.search(rf"(?<![a-zа-яё0-9]){escaped}(?![a-zа-яё0-9])", text, re.I))
    return marker in text
