"""Live vacancy probing across the bundled company directory."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin

from job_harness.company_directory import (
    COMPANY_DIRECTORY_PATH,
    CompanyProfile,
    filter_company_directory,
)

SOCIAL_OR_NAV_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "t.me",
    "telegram.me",
    "twitter.com",
    "x.com",
    "youtube.com",
)

NON_VACANCY_PATTERNS = (
    "/about",
    "/blog",
    "/bundle",
    "/data-quality-platform",
    "/event",
    "/free-tools",
    "/app/",
    "/news",
    "/press",
    "/product",
    "/privacy",
    "/reviews",
    "/services",
    "/success-stories",
    "/terms",
    "/training",
    "?category=",
    "ab-testing",
    "checklist",
    "course",
    "jobs?keyword",
    "salary calculator",
    "services_",
    "-services",
    "testimonial",
    "webinar",
)

VACANCY_URL_PATTERNS = (
    "/career",
    "/careers",
    "/job",
    "/jobs",
    "/opening",
    "/position",
    "/vacanc",
    "ashbyhq.com",
    "boards.greenhouse.io",
    "jobs.lever.co",
    "myworkdayjobs.com",
    "workable.com",
)

ROLE_TEXT_PATTERNS = (
    "analyst",
    "developer",
    "engineer",
    "lead",
    "manager",
    "qa",
    "specialist",
    "tester",
)

LINK_EXTRACTION_SCRIPT = """
() => Array.from(document.links).slice(0, 400).map((link) => ({
    href: link.getAttribute("href") || "",
    text: link.innerText || link.textContent || "",
}))
"""


@dataclass(frozen=True)
class CompanyVacancyHit:
    company: str
    title: str
    vacancy_url: str
    careers_url: str
    matched_text: str
    score: int
    countries: list[str]
    stack: list[str]
    job_types: list[str]


@dataclass(frozen=True)
class CompanyCareerSearchResult:
    query: str
    companies_considered: int
    companies_checked: int
    companies_skipped: int
    checked_companies: list[dict]
    skipped_companies: list[dict]
    errors: list[dict]
    hits: list[CompanyVacancyHit]

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "companies_considered": self.companies_considered,
            "companies_checked": self.companies_checked,
            "companies_skipped": self.companies_skipped,
            "checked_companies": self.checked_companies,
            "skipped_companies": self.skipped_companies,
            "errors": self.errors,
            "total": len(self.hits),
            "hits": [asdict(hit) for hit in self.hits],
        }


def search_company_careers(
    query: str,
    context,
    *,
    country: str | None = None,
    stack: str | None = None,
    job_type: str | None = None,
    industry: str | None = None,
    remote_only: bool = False,
    max_companies: int | None = None,
    max_results: int = 20,
    timeout_ms: int = 8000,
    directory_path=COMPANY_DIRECTORY_PATH,
    progress=None,
) -> CompanyCareerSearchResult:
    """Open company career pages and collect links matching a role query."""
    if not query.strip():
        raise ValueError("query is required for live company career search")

    companies = filter_company_directory(
        country=country,
        stack=stack,
        job_type=job_type,
        industry=industry,
        remote_only=remote_only,
        max_results=max_companies,
        path=directory_path,
    )
    query_terms = _query_terms(query)
    hits: list[CompanyVacancyHit] = []
    errors: list[dict] = []
    checked_companies: list[dict] = []
    skipped_companies: list[dict] = []
    checked = 0
    skipped = 0

    for index, company in enumerate(companies, start=1):
        if not company.careers_url:
            skipped += 1
            skipped_companies.append(
                {
                    "company": company.name,
                    "reason": "missing careers_url",
                    "linkedin_jobs_url": company.linkedin_jobs_url,
                }
            )
            if progress:
                progress(index, len(companies), company, "skipped")
            continue

        checked += 1
        if progress:
            progress(index, len(companies), company, "checking")
        page = context.new_page()
        try:
            page.goto(company.careers_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1000)
            company_hits = _find_matching_links(page, company, query_terms)
            hits.extend(company_hits)
            checked_companies.append(
                {
                    "company": company.name,
                    "careers_url": company.careers_url,
                    "hits": len(company_hits),
                    "status": "ok",
                }
            )
        except Exception as exc:
            error = {
                "company": company.name,
                "careers_url": company.careers_url,
                "error": str(exc),
            }
            errors.append(error)
            checked_companies.append(
                {
                    "company": company.name,
                    "careers_url": company.careers_url,
                    "hits": 0,
                    "status": "error",
                    "error": error["error"],
                }
            )
        finally:
            page.close()

    hits = _dedupe_hits(hits)
    hits.sort(key=lambda hit: (-hit.score, hit.company.casefold(), hit.title.casefold()))
    return CompanyCareerSearchResult(
        query=query,
        companies_considered=len(companies),
        companies_checked=checked,
        companies_skipped=skipped,
        checked_companies=checked_companies,
        skipped_companies=skipped_companies,
        errors=errors,
        hits=hits[:max_results],
    )


def _find_matching_links(page, company: CompanyProfile, query_terms: list[str]) -> list[CompanyVacancyHit]:
    hits: list[CompanyVacancyHit] = []
    for link in _extract_page_links(page):
        href = (link.get("href") or "").strip()
        text = _clean_text(link.get("text") or "")
        if not href or href == "#" or href.startswith("javascript:"):
            continue
        if _is_navigation_or_social(href):
            continue

        absolute_url = urljoin(page.url, href)
        if _is_non_vacancy_link(absolute_url, text):
            continue
        if not _is_vacancy_like_link(absolute_url, text, source_url=page.url):
            continue
        searchable = f"{text} {absolute_url}".casefold()
        score = _score_text(searchable, query_terms)
        if score == 0:
            continue

        title = text or absolute_url
        hits.append(
            CompanyVacancyHit(
                company=company.name,
                title=title[:200],
                vacancy_url=absolute_url,
                careers_url=company.careers_url or page.url,
                matched_text=text[:500],
                score=score,
                countries=list(company.countries),
                stack=list(company.stack),
                job_types=list(company.job_types),
            )
        )
    return hits


def _extract_page_links(page) -> list[dict[str, str]]:
    if hasattr(page, "evaluate"):
        links = page.evaluate(LINK_EXTRACTION_SCRIPT)
        return [link for link in links if isinstance(link, dict)]

    locator = page.locator("a")
    links = []
    for index in range(min(locator.count(), 400)):
        link = locator.nth(index)
        links.append(
            {
                "href": link.get_attribute("href") or "",
                "text": link.inner_text(),
            }
        )
    return links


def _dedupe_hits(hits: list[CompanyVacancyHit]) -> list[CompanyVacancyHit]:
    unique: dict[tuple[str, str], CompanyVacancyHit] = {}
    for hit in hits:
        key = (hit.company.casefold(), hit.vacancy_url)
        existing = unique.get(key)
        if existing is None or hit.score > existing.score:
            unique[key] = hit
    return list(unique.values())


def _query_terms(query: str) -> list[str]:
    terms = [term for term in re.split(r"[\s,;/|()]+", query.casefold()) if len(term) >= 2]
    if not terms:
        raise ValueError("query must contain searchable terms")
    expanded = set(terms)
    synonyms = {
        "backend": {"back-end", "server", "python", "java", "go"},
        "frontend": {"front-end", "react", "typescript", "javascript"},
        "qa": {
            "manual tester",
            "qe",
            "quality assurance engineer",
            "software test",
            "software tester",
            "software quality assurance",
            "test automation",
            "test engineer",
            "test engineering",
            "testing engineer",
        },
        "devops": {"sre", "platform", "infrastructure", "kubernetes"},
        "python": {"django", "fastapi"},
    }
    for term in terms:
        expanded.update(synonyms.get(term, set()))
    return sorted(expanded)


def _score_text(text: str, query_terms: list[str]) -> int:
    return sum(3 if _term_matches(text, term) else 0 for term in query_terms)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _is_navigation_or_social(href: str) -> bool:
    lowered = href.casefold()
    return any(domain in lowered for domain in SOCIAL_OR_NAV_DOMAINS)


def _is_non_vacancy_link(url: str, text: str) -> bool:
    lowered = f"{url} {text}".casefold()
    return any(pattern in lowered for pattern in NON_VACANCY_PATTERNS)


def _is_vacancy_like_link(url: str, text: str, *, source_url: str = "") -> bool:
    lowered_url = url.casefold()
    source_lowered = source_url.casefold()
    if any(pattern in lowered_url for pattern in VACANCY_URL_PATTERNS):
        return True
    if any(pattern in source_lowered for pattern in VACANCY_URL_PATTERNS):
        return any(pattern in text.casefold() for pattern in ROLE_TEXT_PATTERNS)
    return False


def _term_matches(text: str, term: str) -> bool:
    if " " in term:
        pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(
            re.escape(part) for part in term.split()
        ) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    if not term.isalnum():
        return term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
