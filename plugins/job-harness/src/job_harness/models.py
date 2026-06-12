"""Universal data models for job search harness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class SearchParams:
    query: str
    country: str | None = None
    remote_only: bool = False
    experience_levels: tuple[str, ...] = ()
    location: str | None = None
    salary_from: int | None = None
    freshness_days: int | None = None
    max_results: int = 20
    extra: dict = field(default_factory=dict)  # Platform-specific parameters


JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class RawListing:
    """Source-native listing facts collected by a scraper.

    This is the scraper-facing search-layer model. It intentionally has no
    downstream ranking, dedupe, or experience-assessment fields.
    """

    title: str
    url: str
    company: str | None = None
    country: str | None = None
    salary: str | None = None
    experience: str | None = None
    remote: bool | None = None
    location: str | None = None
    description: str | None = None
    requirements: str | None = None
    skills: tuple[str, ...] = ()
    posted_date: str | None = None
    source: str = ""
    raw: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "company": self.company,
            "country": self.country,
            "salary": self.salary,
            "experience": self.experience,
            "remote": self.remote,
            "location": self.location,
            "description": self.description,
            "requirements": self.requirements,
            "skills": list(self.skills),
            "posted_date": self.posted_date,
            "source": self.source,
            "raw": dict(self.raw),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RawListing:
        return cls(
            title=str(data["title"]),
            url=str(data["url"]),
            company=data.get("company"),
            country=data.get("country"),
            salary=data.get("salary"),
            experience=data.get("experience"),
            remote=data.get("remote"),
            location=data.get("location"),
            description=data.get("description"),
            requirements=data.get("requirements"),
            skills=tuple(str(item) for item in data.get("skills") or ()),
            posted_date=data.get("posted_date"),
            source=str(data.get("source") or ""),
            raw=dict(data.get("raw") or {}),
        )

    def to_job_listing(self) -> JobListing:
        """Project raw facts into the downstream presentation model."""
        return JobListing(
            title=self.title,
            url=self.url,
            company=self.company or "",
            country=self.country,
            salary=self.salary,
            experience=self.experience,
            remote=self.remote is True,
            location=self.location,
            description=self.description,
            requirements=self.requirements,
            skills=list(self.skills),
            posted_date=self.posted_date,
            source=self.source,
            raw=dict(self.raw),
        )


@dataclass(frozen=True)
class RawSearchRecord:
    schema_version: Literal[1]
    type: Literal["raw_listing"]
    run_id: str
    source: str
    collected_at: str
    listing: RawListing

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "type": self.type,
            "run_id": self.run_id,
            "source": self.source,
            "collected_at": self.collected_at,
            "listing": self.listing.to_dict(),
        }


@dataclass
class JobListing:
    title: str
    url: str
    company: str
    country: str | None = None
    salary: str | None = None
    # Internal native structured/server grade input only. Best-effort sources
    # must leave this empty and let the grade engine estimate from text fields.
    experience: str | None = None
    experience_levels: list[str] = field(default_factory=list)
    experience_origin: str = "unknown"
    experience_confidence: str = "none"
    experience_evidence: list[str] = field(default_factory=list)
    remote: bool = False
    location: str | None = None
    description: str | None = None
    requirements: str | None = None
    skills: list[str] = field(default_factory=list)
    posted_date: str | None = None
    source: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "company": self.company,
            "country": self.country,
            "salary": self.salary,
            "experience_levels": self.experience_levels,
            "experience_origin": self.experience_origin,
            "experience_confidence": self.experience_confidence,
            "experience_evidence": self.experience_evidence,
            "remote": self.remote,
            "location": self.location,
            "description": self.description,
            "requirements": self.requirements,
            "skills": self.skills,
            "posted_date": self.posted_date,
            "source": self.source,
            "raw": self.raw,
        }

    def matches(self, predicate: Callable[[JobListing], bool]) -> bool:
        return predicate(self)


@dataclass
class SearchResults:
    params: SearchParams
    listings: list[JobListing]
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    errors: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "params": {
                "query": self.params.query,
                "country": self.params.country,
                "remote_only": self.params.remote_only,
                "experience_levels": list(self.params.experience_levels),
                "location": self.params.location,
                "salary_from": self.params.salary_from,
                "freshness_days": self.params.freshness_days,
                "max_results": self.params.max_results,
            },
            "timestamp": self.timestamp,
            "total": len(self.listings),
            "listings": [listing.to_dict() for listing in self.listings],
            "errors": self.errors,
            "summary": self.summary,
        }
