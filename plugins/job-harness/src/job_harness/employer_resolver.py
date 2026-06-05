"""Resolve aggregator listings to direct employer career pages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from job_harness.employer_cache import CompanyEntry, EmployerCache


@dataclass
class CareersPageResult:
    """Result of resolving a company's career page."""
    company: str
    careers_url: str | None = None
    page_type: str = "unknown"  # "direct" | "greenhouse" | "lever" | "workday" | "huntflow" | "unknown"
    has_matching_vacancy: bool = False
    direct_vacancy_url: str | None = None
    error: str | None = None


@dataclass
class EnrichedListing:
    """A job listing enriched with employer career page info."""
    original_url: str
    company: str
    title: str
    source: str
    careers_page: CareersPageResult | None = None

    @property
    def best_url(self) -> str:
        """Return the direct employer URL if found, otherwise the aggregator URL."""
        if self.careers_page and self.careers_page.direct_vacancy_url:
            url = self.careers_page.direct_vacancy_url
            if _is_valid_url(url):
                return url
        if self.careers_page and self.careers_page.careers_url:
            url = self.careers_page.careers_url
            if _is_valid_url(url):
                return url
        return self.original_url


# Known ATS URL patterns for classification
ATS_PATTERNS: dict[str, list[str]] = {
    "greenhouse": ["greenhouse.io", "grnh.se"],
    "lever": ["lever.co"],
    "workday": ["workday.com", "wd1.myworkdayjobs.com", "wd5.myworkdayjobs.com"],
    "huntflow": ["huntflow.ru", "huntflow.com"],
}

# Common Russian career page URL patterns to try
CAREERS_PATHS = [
    "/career", "/careers", "/jobs", "/vacancies", "/vakansii",
    "/about/career", "/about/careers", "/about/jobs", "/about/vacancies",
    "/ru/career", "/ru/careers", "/ru/jobs", "/ru/vacancies",
    "/en/career", "/en/careers", "/en/jobs",
    "/company/career", "/company/vacancies",
    "/team/jobs", "/team/careers",
]

# Company name cleanup patterns
_COMPANY_SUFFIXES = re.compile(
    r"\s*(ООО|АО|ЗАО|ПАО|ИП|ФГУП|ГУП)\s*|"
    r"\s*(LLC|Ltd|Inc|Corp|GmbH|AG|SA|NV)\s*\.?",
    re.IGNORECASE,
)


def clean_company_name(name: str) -> str:
    """Strip legal entity suffixes from company name for search."""
    cleaned = _COMPANY_SUFFIXES.sub(" ", name).strip()
    return cleaned if cleaned else name


def _is_valid_url(url: str) -> bool:
    """Check if a URL is absolute and navigable."""
    return url.startswith("http://") or url.startswith("https://")


def classify_careers_url(url: str) -> str:
    """Classify a career page URL by ATS type."""
    url_lower = url.lower()
    for ats_type, patterns in ATS_PATTERNS.items():
        if any(p in url_lower for p in patterns):
            return ats_type
    return "direct"


def build_careers_search_queries(company: str) -> list[str]:
    """Generate search queries for finding a company's career page."""
    clean = clean_company_name(company)
    return [
        f'"{clean}" вакансии сайт',
        f'"{clean}" careers jobs',
        f'"{clean}" career site',
    ]


def try_careers_paths(base_url: str, page) -> str | None:
    """Try common career page URL paths on a base domain.

    Returns the first URL that returns HTTP 200 with meaningful content.
    """
    base = base_url.rstrip("/")
    for path in CAREERS_PATHS:
        url = base + path
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=10000)
            if resp and resp.status == 200:
                content = page.content().lower()
                career_keywords = ["ваканци", "карьер", "job", "career", "vacanc", "position", "открытые позиц"]
                if any(kw in content for kw in career_keywords):
                    return url
        except Exception:
            continue
    return None


def find_matching_vacancy_on_page(page, query: str, company: str) -> str | None:
    """Search a career page for a vacancy matching the query.

    Looks for links containing query-related keywords. Returns the first match.
    """
    query_terms = [t.lower() for t in query.split() if len(t) > 2]
    synonyms = {
        "тестировщик": ["qa", "тест", "test", "quality"],
        "ручной": ["manual", "ручн"],
        "инженер": ["engineer", "инж"],
        "разработчик": ["developer", "dev", "разраб"],
        "qa": ["тестировщик", "quality", "тест"],
    }
    expanded_terms = set(query_terms)
    for term in query_terms:
        if term in synonyms:
            expanded_terms.update(synonyms[term])

    links = page.locator("a")
    candidates: list[tuple[int, str, str]] = []

    for i in range(min(links.count(), 200)):
        try:
            href = links.nth(i).get_attribute("href") or ""
            text = links.nth(i).inner_text().lower()
            if not href or href == "#" or href.startswith("javascript"):
                continue

            score = 0
            for term in expanded_terms:
                if term in text:
                    score += 2
                if term in href.lower():
                    score += 1

            nav_words = ["github", "telegram", "linkedin", "facebook", "twitter", "instagram", "youtube"]
            if any(w in href.lower() for w in nav_words):
                continue

            if score > 0:
                if href.startswith("/"):
                    base = page.url.split("/")[0] + "//" + page.url.split("/")[2]
                    href = base + href
                elif not href.startswith("http"):
                    continue
                candidates.append((score, href, text))
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _try_career_scraper(scraper_name: str, context, query: str) -> list[dict] | None:
    """Try to use a registered per-company career scraper.

    Career scrapers (career:vk, career:ibs) are now async and dispatched
    through the SearchEngine + BrowserPool. The legacy sync `resolve_*`
    path that calls this helper cannot drive them; it returns None so
    the surrounding fallback chain proceeds.
    """
    return None


def resolve_company_careers(
    company: str,
    context,
    query: str | None = None,
    cache: EmployerCache | None = None,
) -> CareersPageResult:
    """Resolve a company name to its career page and optionally find a matching vacancy.

    Strategy:
    1. Check cache for fresh entry
    2. If cached entry has a career scraper, use it for vacancy search
    3. Try web search for "[company] вакансии" / "[company] careers"
    4. Try common career page paths on company's main domain
    5. If career page found, search for matching vacancy
    6. Save result to cache
    """
    result = CareersPageResult(company=company)

    # Step 1: Check cache
    if cache:
        cached = cache.get_fresh(company)
        if cached:
            result.careers_url = cached.careers_url
            result.page_type = cached.ats_type
            if cached.ignored:
                result.error = "Company marked as ignored in cache"
                return result
            # Step 2: Use career scraper if available
            if cached.scraper_name and query:
                vacancies = _try_career_scraper(cached.scraper_name, context, query)
                if vacancies:
                    result.has_matching_vacancy = True
                    result.direct_vacancy_url = vacancies[0].get("url")
                    return result
            if cached.careers_url and not cached.last_found_roles:
                return result  # Cached as "no roles found", skip re-check within freshness window

    clean = clean_company_name(company)
    page = context.new_page()
    try:
        # Step 3: Try search engine to find career page
        try:
            search_url = f"https://www.google.com/search?q={clean.replace(' ', '+')}+vacancies+careers+site&hl=ru"
            page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)

            links = page.locator("a")
            found_careers_url = None
            for i in range(min(links.count(), 50)):
                try:
                    href = links.nth(i).get_attribute("href") or ""
                    text = links.nth(i).inner_text().lower()
                    if not href or "google" in href:
                        continue
                    career_words = ["career", "job", "vacanc", "ваканци", "карьер", "team"]
                    company_in_text = clean.lower().split()[0] in text if clean.split() else False
                    if company_in_text and any(w in text or w in href.lower() for w in career_words):
                        if "/url?q=" in href:
                            href = href.split("/url?q=")[1].split("&")[0]
                        if not _is_valid_url(href):
                            continue
                        skip_domains = ["hh.ru", "career.habr.com", "linkedin.com", "facebook.com", "instagram.com", "twitter.com"]
                        if any(d in href.lower() for d in skip_domains):
                            continue
                        found_careers_url = href
                        break
                except Exception:
                    continue

            if found_careers_url:
                result.careers_url = found_careers_url
                result.page_type = classify_careers_url(found_careers_url)
        except Exception as e:
            result.error = f"Search failed: {e}"

        # Step 4: If no URL from search, try direct domain probing
        if not result.careers_url:
            domain_search_url = f"https://www.google.com/search?q={clean.replace(' ', '+')}&btnI=I%27m+Feeling+Lucky"
            try:
                page.goto(domain_search_url, wait_until="domcontentloaded", timeout=10000)
                page.wait_for_timeout(1000)
                current_url = page.url
                if "google" not in current_url:
                    careers_url = try_careers_paths(current_url, page)
                    if careers_url:
                        result.careers_url = careers_url
                        result.page_type = classify_careers_url(careers_url)
            except Exception:
                pass

        # Step 5: If career page found, look for matching vacancy
        if result.careers_url and query:
            try:
                page.goto(result.careers_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)

                try:
                    search_input = page.locator('input[type="search"], input[name*="search"], input[name*="query"], input[placeholder*="поиск"], input[placeholder*="search"]')
                    if search_input.count() > 0 and search_input.first.is_visible():
                        search_input.first.fill(query)
                        search_input.first.press("Enter")
                        page.wait_for_timeout(2000)
                except Exception:
                    pass

                direct_url = find_matching_vacancy_on_page(page, query, company)
                if direct_url and _is_valid_url(direct_url):
                    result.has_matching_vacancy = True
                    result.direct_vacancy_url = direct_url
            except Exception as e:
                if not result.error:
                    result.error = f"Vacancy search failed: {e}"

    finally:
        page.close()

    if not result.careers_url and not result.error:
        result.error = "No career page found"

    # Step 6: Save to cache
    if cache:
        entry = CompanyEntry(
            company=company,
            careers_url=result.careers_url,
            ats_type=result.page_type,
            last_found_roles=result.has_matching_vacancy,
        )
        # Preserve scraper_name and ignored from existing cache entry
        existing = cache.get(company)
        if existing:
            entry.scraper_name = existing.scraper_name
            entry.ignored = existing.ignored
        cache.upsert(entry)
        cache.save()

    return result


def resolve_listings(
    listings: list[dict],
    context,
    query: str | None = None,
    cache: EmployerCache | None = None,
) -> list[EnrichedListing]:
    """Resolve multiple listings to direct employer career pages.

    Takes a list of listing dicts (as from JSON output) and returns enriched listings.
    Deduplicates by company name to avoid redundant lookups.
    """
    seen_companies: dict[str, CareersPageResult] = {}
    results: list[EnrichedListing] = []

    for listing in listings:
        company = listing.get("company", "")
        if not company:
            results.append(EnrichedListing(
                original_url=listing.get("url", ""),
                company=company,
                title=listing.get("title", ""),
                source=listing.get("source", ""),
                careers_page=CareersPageResult(company=company, error="No company name"),
            ))
            continue

        if company in seen_companies:
            careers = seen_companies[company]
        else:
            print(f"Resolving {company}...", file=__import__("sys").stderr)
            careers = resolve_company_careers(company, context, query, cache=cache)
            seen_companies[company] = careers

        results.append(EnrichedListing(
            original_url=listing.get("url", ""),
            company=company,
            title=listing.get("title", ""),
            source=listing.get("source", ""),
            careers_page=careers,
        ))

    return results
