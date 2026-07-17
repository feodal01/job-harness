"""Canonical facts used by hard selection and public projection."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts.enums import CompensationPeriod


@dataclass(frozen=True)
class LocationFact:
    raw_text: str | None
    cities: tuple[str, ...]
    countries: tuple[str, ...]
    regions: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class WorkplaceFact:
    formats: tuple[str, ...]
    remote_scopes: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class GradeFact:
    title_evidence: tuple[str, ...]
    source_evidence: tuple[str, ...]
    resolved: tuple[str, ...]
    conflict: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CompensationFact:
    minimum: int | None
    maximum: int | None
    currency: str | None
    period: CompensationPeriod | None
    gross: bool | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class BooleanEvidenceFact:
    supported: bool | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RelocationFact:
    supported: bool | None
    destinations: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalSelectionFacts:
    location: LocationFact
    workplace: WorkplaceFact
    grade: GradeFact
    compensation: CompensationFact
    relocation: RelocationFact
    visa_sponsorship: BooleanEvidenceFact
    employer_geographies: tuple[str, ...]
