"""Live vacancy probing across the bundled company directory."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlparse

from job_harness.company_directory import (
    COMPANY_DIRECTORY_PATH,
    CompanyProfile,
    filter_company_directory,
)
from job_harness.scrapers.http_common import extract_anchors, fetch_json, fetch_text

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
    "careerist.com/automation",
    "careerist.com/qa",
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

NO_OPEN_POSITIONS_PATTERNS = (
    "no open positions",
    "no open vacancies",
    "нет открытых вакансий",
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
        if _has_known_no_open_positions(company):
            checked += 1
            checked_companies.append(
                {
                    "company": company.name,
                    "careers_url": company.careers_url,
                    "hits": 0,
                    "status": "ok",
                    "method": "known_no_open_positions",
                }
            )
            continue

        if not company.careers_url:
            if company.linkedin_jobs_url:
                checked += 1
                try:
                    company_hits = _find_matching_links_http(company, query_terms, url=company.linkedin_jobs_url)
                    hits.extend(company_hits)
                    checked_companies.append(
                        {
                            "company": company.name,
                            "careers_url": None,
                            "alternate_url": company.linkedin_jobs_url,
                            "hits": len(company_hits),
                            "status": "ok",
                            "method": "alternate_jobs_http",
                        }
                    )
                    continue
                except Exception as exc:
                    errors.append(
                        {
                            "company": company.name,
                            "careers_url": None,
                            "alternate_url": company.linkedin_jobs_url,
                            "error": str(exc),
                        }
                    )
                    checked_companies.append(
                        {
                            "company": company.name,
                            "careers_url": None,
                            "alternate_url": company.linkedin_jobs_url,
                            "hits": 0,
                            "status": "error",
                            "error": str(exc),
                        }
                    )
                    continue

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
        attempt_errors = []
        try:
            ats_hits = _find_matching_ats_jobs(company, query_terms)
            if ats_hits is not None:
                hits.extend(ats_hits)
                checked_companies.append(
                    {
                        "company": company.name,
                        "careers_url": company.careers_url,
                        "hits": len(ats_hits),
                        "status": "ok",
                        "method": "ats_api",
                    }
                )
                continue
        except Exception as exc:
            attempt_errors.append({"method": "ats_api", "error": str(exc)})

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
                    "method": "browser",
                }
            )
        except Exception as exc:
            attempt_errors.append({"method": "browser", "error": str(exc)})
            try:
                company_hits = _find_matching_links_from_html(
                    html=page.content(),
                    base_url=page.url,
                    careers_url=company.careers_url,
                    company=company,
                    query_terms=query_terms,
                )
                hits.extend(company_hits)
                checked_companies.append(
                    {
                        "company": company.name,
                        "careers_url": company.careers_url,
                        "hits": len(company_hits),
                        "status": "ok",
                        "method": "browser_html",
                        "attempt_errors": attempt_errors,
                    }
                )
                continue
            except Exception as html_exc:
                attempt_errors.append({"method": "browser_html", "error": str(html_exc)})
            try:
                company_hits = _find_matching_links_http(company, query_terms)
                hits.extend(company_hits)
                checked_companies.append(
                    {
                        "company": company.name,
                        "careers_url": company.careers_url,
                        "hits": len(company_hits),
                        "status": "ok",
                        "method": "http",
                        "attempt_errors": attempt_errors,
                    }
                )
            except Exception as http_exc:
                attempt_errors.append({"method": "http", "error": str(http_exc)})
                if company.linkedin_jobs_url:
                    try:
                        company_hits = _find_matching_links_http(company, query_terms, url=company.linkedin_jobs_url)
                        hits.extend(company_hits)
                        checked_companies.append(
                            {
                                "company": company.name,
                                "careers_url": company.careers_url,
                                "alternate_url": company.linkedin_jobs_url,
                                "hits": len(company_hits),
                                "status": "ok",
                                "method": "alternate_jobs_http",
                                "attempt_errors": attempt_errors,
                            }
                        )
                    except Exception as alternate_exc:
                        attempt_errors.append({"method": "alternate_jobs_http", "error": str(alternate_exc)})
                if not checked_companies or checked_companies[-1]["company"] != company.name:
                    error = {
                        "company": company.name,
                        "careers_url": company.careers_url,
                        "error": attempt_errors[-1]["error"],
                        "attempt_errors": attempt_errors,
                    }
                    errors.append(error)
                    checked_companies.append(
                        {
                            "company": company.name,
                            "careers_url": company.careers_url,
                            "hits": 0,
                            "status": "error",
                            "error": error["error"],
                            "attempt_errors": attempt_errors,
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
    return _find_matching_link_snapshot(
        links=_extract_page_links(page),
        base_url=page.url,
        careers_url=company.careers_url or page.url,
        company=company,
        query_terms=query_terms,
    )


def _find_matching_links_http(
    company: CompanyProfile,
    query_terms: list[str],
    *,
    url: str | None = None,
) -> list[CompanyVacancyHit]:
    target_url = url or company.careers_url
    if not target_url:
        return []
    try:
        html = fetch_text(target_url)
    except Exception:
        html = fetch_text(target_url, verify_ssl=False)
    return _find_matching_links_from_html(
        html=html,
        base_url=target_url,
        careers_url=company.careers_url or target_url,
        company=company,
        query_terms=query_terms,
    )


def _find_matching_links_from_html(
    *,
    html: str,
    base_url: str,
    careers_url: str,
    company: CompanyProfile,
    query_terms: list[str],
) -> list[CompanyVacancyHit]:
    links = [{"href": anchor.href, "text": anchor.text} for anchor in extract_anchors(html)]
    return _find_matching_link_snapshot(
        links=links,
        base_url=base_url,
        careers_url=careers_url,
        company=company,
        query_terms=query_terms,
    )


def _find_matching_link_snapshot(
    *,
    links: list[dict[str, str]],
    base_url: str,
    careers_url: str,
    company: CompanyProfile,
    query_terms: list[str],
) -> list[CompanyVacancyHit]:
    hits: list[CompanyVacancyHit] = []
    for link in links:
        href = (link.get("href") or "").strip()
        text = _clean_text(link.get("text") or "")
        if not href or href == "#" or href.startswith("javascript:"):
            continue
        if _is_navigation_or_social(href):
            continue

        absolute_url = urljoin(base_url, href)
        if _is_non_vacancy_link(absolute_url, text):
            continue
        if not _is_vacancy_like_link(absolute_url, text, source_url=base_url):
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
                careers_url=careers_url,
                matched_text=text[:500],
                score=score,
                countries=list(company.countries),
                stack=list(company.stack),
                job_types=list(company.job_types),
            )
        )
    return _dedupe_hits(hits)


def _find_matching_ats_jobs(company: CompanyProfile, query_terms: list[str]) -> list[CompanyVacancyHit] | None:
    if not company.careers_url:
        return None
    lever_account = _lever_account(company.careers_url)
    if lever_account is not None:
        return _find_matching_lever_jobs(company, query_terms, lever_account)

    ashby_board = _ashby_board(company.careers_url)
    if ashby_board is not None:
        return _find_matching_ashby_jobs(company, query_terms, ashby_board)

    return None


def _find_matching_lever_jobs(
    company: CompanyProfile,
    query_terms: list[str],
    lever_account: str,
) -> list[CompanyVacancyHit]:
    careers_url = company.careers_url
    if careers_url is None:
        raise ValueError("Lever company careers_url is required")

    postings = fetch_json(f"https://api.lever.co/v0/postings/{lever_account}?mode=json")
    if not isinstance(postings, list):
        raise ValueError("Lever postings API returned non-list payload")

    hits: list[CompanyVacancyHit] = []
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        title = _clean_text(str(posting.get("text") or ""))
        hosted_url = str(posting.get("hostedUrl") or posting.get("applyUrl") or "")
        if not title or not hosted_url:
            continue
        categories = posting.get("categories") if isinstance(posting.get("categories"), dict) else {}
        category_text = " ".join(str(value) for value in categories.values() if value)
        searchable = f"{title} {category_text} {hosted_url}".casefold()
        score = _score_text(searchable, query_terms)
        if score == 0:
            continue
        location = _clean_text(str(categories.get("location") or posting.get("country") or ""))
        display_title = f"{title} {location}".strip()
        hits.append(
            CompanyVacancyHit(
                company=company.name,
                title=display_title[:200],
                vacancy_url=hosted_url,
                careers_url=careers_url,
                matched_text=f"{title} {category_text}".strip()[:500],
                score=score,
                countries=list(company.countries),
                stack=list(company.stack),
                job_types=list(company.job_types),
            )
        )
    return _dedupe_hits(hits)


def _find_matching_ashby_jobs(
    company: CompanyProfile,
    query_terms: list[str],
    ashby_board: str,
) -> list[CompanyVacancyHit]:
    careers_url = company.careers_url
    if careers_url is None:
        raise ValueError("Ashby company careers_url is required")

    payload = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{ashby_board}")
    postings = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(postings, list):
        raise ValueError("Ashby postings API returned non-list jobs payload")

    hits: list[CompanyVacancyHit] = []
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        title = _clean_text(str(posting.get("title") or ""))
        hosted_url = str(posting.get("jobUrl") or posting.get("applyUrl") or "")
        if not title or not hosted_url:
            continue
        location = _clean_text(str(posting.get("location") or ""))
        metadata = " ".join(
            _clean_text(str(posting.get(field) or ""))
            for field in ("department", "team", "employmentType", "workplaceType")
        )
        searchable = f"{title} {location} {metadata} {hosted_url}".casefold()
        score = _score_text(searchable, query_terms)
        if score == 0:
            continue
        display_title = f"{title} {location}".strip()
        hits.append(
            CompanyVacancyHit(
                company=company.name,
                title=display_title[:200],
                vacancy_url=hosted_url,
                careers_url=careers_url,
                matched_text=f"{title} {location} {metadata}".strip()[:500],
                score=score,
                countries=list(company.countries),
                stack=list(company.stack),
                job_types=list(company.job_types),
            )
        )
    return _dedupe_hits(hits)


def _lever_account(url: str) -> str | None:
    parsed = urlparse(url)
    if "lever.co" not in parsed.netloc.casefold():
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if parts else None


def _ashby_board(url: str) -> str | None:
    parsed = urlparse(url)
    if "ashbyhq.com" not in parsed.netloc.casefold():
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if parts else None




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
        key = (hit.company.casefold(), _canonical_hit_url(hit.vacancy_url))
        existing = unique.get(key)
        if existing is None or _hit_quality(hit) > _hit_quality(existing):
            unique[key] = hit
    return list(unique.values())


def _canonical_hit_url(url: str) -> str:
    return url.rstrip("/")


def _hit_quality(hit: CompanyVacancyHit) -> tuple[int, int, int, int]:
    title = hit.title.casefold()
    is_descriptive_title = int(not title.startswith("http"))
    return (hit.score, is_descriptive_title, len(hit.matched_text), len(hit.title))


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


def _has_known_no_open_positions(company: CompanyProfile) -> bool:
    text = " ".join(company.job_types).casefold()
    return any(pattern in text for pattern in NO_OPEN_POSITIONS_PATTERNS)


def _term_matches(text: str, term: str) -> bool:
    if " " in term:
        pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(
            re.escape(part) for part in term.split()
        ) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    if not term.isalnum():
        return term in text
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
