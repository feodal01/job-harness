"""Bundled company directory used for employer-first job search."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

COMPANY_DIRECTORY_PATH = Path("data/company-directory.json")


@dataclass(frozen=True)
class CompanyProfile:
    name: str
    careers_url: str | None = None
    linkedin_url: str | None = None
    linkedin_jobs_url: str | None = None
    description: str | None = None
    industry: str | None = None
    headcount: str | None = None
    remote: bool = False
    job_types: tuple[str, ...] = ()
    stack: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    ats_type: str = "unknown"
    scraper_name: str | None = None
    last_checked: str | None = None
    last_found_roles: bool = False
    sources: tuple[str, ...] = ()

    @property
    def best_jobs_url(self) -> str | None:
        return self.careers_url or self.linkedin_jobs_url or self.linkedin_url

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "careers_url": self.careers_url,
            "linkedin_url": self.linkedin_url,
            "linkedin_jobs_url": self.linkedin_jobs_url,
            "description": self.description,
            "industry": self.industry,
            "headcount": self.headcount,
            "remote": self.remote,
            "job_types": list(self.job_types),
            "stack": list(self.stack),
            "countries": list(self.countries),
            "ats_type": self.ats_type,
            "scraper_name": self.scraper_name,
            "last_checked": self.last_checked,
            "last_found_roles": self.last_found_roles,
            "sources": list(self.sources),
        }


def normalize_company_key(name: str) -> str:
    """Normalize company names for deterministic deduplication."""
    return re.sub(r"\s+", " ", name.strip().casefold())


def load_company_directory(path: Path | str = COMPANY_DIRECTORY_PATH) -> list[CompanyProfile]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Company directory must be a JSON list")
    return [_parse_company_profile(item) for item in raw]


def search_company_directory(
    query: str,
    *,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_results: int = 20,
    path: Path | str = COMPANY_DIRECTORY_PATH,
) -> list[CompanyProfile]:
    """Find companies whose known hiring profile matches the requested role."""
    profiles = filter_company_directory(
        country=country,
        stack=stack,
        job_type=job_type,
        industry=industry,
        remote_only=remote_only,
        path=path,
    )
    results: list[tuple[int, CompanyProfile]] = []

    query_terms = _terms(query)
    structured_profile_filter = bool(stack or job_type or industry)
    for profile in profiles:
        score = _score_profile(profile, query_terms)
        if query_terms and score == 0 and not structured_profile_filter:
            continue
        results.append((score, profile))

    results.sort(key=lambda item: (-item[0], item[1].name.casefold()))
    return [profile for _, profile in results[:max_results]]


def filter_company_directory(
    *,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_results: int | None = None,
    path: Path | str = COMPANY_DIRECTORY_PATH,
) -> list[CompanyProfile]:
    """Return directory companies matching structured filters only."""
    profiles = []
    for profile in load_company_directory(path):
        if remote_only and not profile.remote:
            continue
        if country and not _contains(profile.countries, country):
            continue
        if stack and not _contains(profile.stack, stack):
            continue
        if job_type and not _contains(profile.job_types, job_type):
            continue
        if industry and not _text_contains(profile.industry, industry):
            continue
        profiles.append(profile)

    profiles.sort(key=lambda profile: profile.name.casefold())
    return profiles if max_results is None else profiles[:max_results]


def _parse_company_profile(item: object) -> CompanyProfile:
    if not isinstance(item, dict):
        raise ValueError("Company directory entries must be objects")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Company directory entry is missing a name")
    return CompanyProfile(
        name=name.strip(),
        careers_url=_optional_str(item.get("careers_url")),
        linkedin_url=_optional_str(item.get("linkedin_url")),
        linkedin_jobs_url=_optional_str(item.get("linkedin_jobs_url")),
        description=_optional_str(item.get("description")),
        industry=_optional_str(item.get("industry")),
        headcount=_optional_str(item.get("headcount")),
        remote=bool(item.get("remote")),
        job_types=tuple(_str_list(item.get("job_types"))),
        stack=tuple(_str_list(item.get("stack"))),
        countries=tuple(_str_list(item.get("countries"))),
        ats_type=_optional_str(item.get("ats_type")) or "unknown",
        scraper_name=_optional_str(item.get("scraper_name")),
        last_checked=_optional_str(item.get("last_checked")),
        last_found_roles=bool(item.get("last_found_roles")),
        sources=tuple(_str_list(item.get("sources"))),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected string or null, got {type(value).__name__}")
    stripped = value.strip()
    return stripped or None


def _str_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Expected list, got {type(value).__name__}")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"Expected list of strings, got {type(item).__name__}")
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return result


def _terms(text: str) -> list[str]:
    return [term for term in re.split(r"[\s,;/|]+", text.casefold()) if term]


def _contains(values: tuple[str, ...], needle: str) -> bool:
    normalized = needle.casefold().strip()
    return any(normalized in value.casefold() for value in values)


def _text_contains(value: str | None, needle: str) -> bool:
    return value is not None and needle.casefold().strip() in value.casefold()


def _score_profile(profile: CompanyProfile, query_terms: list[str]) -> int:
    if not query_terms:
        return 1

    searchable_parts = [
        profile.name,
        profile.description or "",
        profile.industry or "",
        " ".join(profile.job_types),
        " ".join(profile.stack),
        " ".join(profile.countries),
    ]
    searchable = " ".join(searchable_parts).casefold()

    score = 0
    for term in query_terms:
        if term in profile.name.casefold():
            score += 5
        if any(term in item.casefold() for item in profile.job_types):
            score += 4
        if any(term in item.casefold() for item in profile.stack):
            score += 3
        if profile.industry and term in profile.industry.casefold():
            score += 2
        if term in searchable:
            score += 1
    return score
