"""Universal data models for job search harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable


@dataclass
class SearchParams:
    query: str
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
                "remote_only": self.params.remote_only,
                "experience": self.params.experience,
                "location": self.params.location,
                "max_results": self.params.max_results,
            },
            "timestamp": self.timestamp,
            "total": len(self.listings),
            "listings": [l.to_dict() for l in self.listings],
            "errors": self.errors,
        }
