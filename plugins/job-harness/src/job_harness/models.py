"""Universal data models for job search harness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SearchParams:
    query: str
    country: str | None = None
    remote_only: bool = False
    experience: str | None = None  # "junior" | "middle" | "senior"
    location: str | None = None
    max_results: int = 20
    extra: dict = field(default_factory=dict)  # Platform-specific parameters


@dataclass
class JobListing:
    title: str
    url: str
    company: str
    country: str | None = None
    salary: str | None = None
    experience: str | None = None
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
            "experience": self.experience,
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

    def to_dict(self) -> dict:
        return {
            "params": {
                "query": self.params.query,
                "country": self.params.country,
                "remote_only": self.params.remote_only,
                "experience": self.params.experience,
                "location": self.params.location,
                "max_results": self.params.max_results,
            },
            "timestamp": self.timestamp,
            "total": len(self.listings),
            "listings": [listing.to_dict() for listing in self.listings],
            "errors": self.errors,
        }
