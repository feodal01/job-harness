"""Contract-first IT-Jobs.uz aggregator source."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from job_harness.v2.contracts import (
    AttemptEvidence,
    RawListing,
    RequiredParserFixtures,
    SearchRequest,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceResponseArtifact,
    SourceScraper,
    SourceSearchParseResult,
)
from job_harness.v2.source_catalog import source_descriptor, source_required_fixture_kinds

_API_URL = "https://www.it-jobs.uz/api/jobs"
_PUBLIC_BASE_URL = "https://www.it-jobs.uz/en/jobs"
_EXPERIENCE_GRADE_MAP = {
    "JUNIOR": "junior",
    "MIDDLE": "middle",
    "SENIOR": "senior",
    "ANY": "any",
}
_CAREER_URL_MARKERS = (
    "career",
    "careers",
    "jobs",
    "job",
    "vacanc",
    "rabota",
    "ваканс",
)
_NON_COMPANY_CAREER_HOSTS = (
    "career.habr.com",
    "getmatch.ru",
    "hh.ru",
    "it-market.uz",
    "rabota.ru",
    "superjob.ru",
    "t.me",
    "telegram.me",
    "worklicant.com",
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CATEGORY_MARKERS: dict[str, frozenset[str]] = {
    "backend": frozenset(
        {"backend", "бэкенд", "server", "node", "nodejs", "php", "golang", "go", "java", "python", "fastapi", "django"}
    ),
    "frontend": frozenset({"frontend", "фронтенд", "react", "angular", "vue", "javascript", "typescript", "next"}),
    "mobile": frozenset({"mobile", "мобиль", "android", "ios", "flutter", "reactnative", "kotlin", "swift"}),
    "qa": frozenset({"qa", "test", "testing", "тест", "quality", "aqa", "sdet"}),
    "data": frozenset({"data", "ml", "machine", "learning", "ai", "аналитик", "analyst", "bi"}),
    "design": frozenset({"design", "designer", "ux", "ui", "дизайн"}),
    "pm": frozenset({"pm", "product", "project", "manager", "продакт", "проект"}),
    "devops": frozenset({"devops", "sre", "infrastructure", "infra", "cloud", "kubernetes", "docker"}),
    "security": frozenset({"security", "cyber", "pentest", "infosec", "безопас"}),
}


class ItJobsUzSource(SourceScraper):
    @property
    def descriptor(self) -> SourceDescriptor:
        return source_descriptor("it_jobs_uz")

    @property
    def required_fixture_kinds(self) -> RequiredParserFixtures:
        return source_required_fixture_kinds("it_jobs_uz")

    def build_search_requests(self, request: SearchRequest) -> tuple[SourceFetchRequest, ...]:
        source_limit = self.descriptor.source_limit
        return tuple(
            SourceFetchRequest(
                source_id=self.descriptor.source_id,
                query_variant=query_variant,
                url=_build_api_url(
                    _search_params(query_variant, request, limit=source_limit, page=1),
                ),
            )
            for query_variant in request.query_variants
        )

    def parse_search_response(
        self,
        response: SourceResponseArtifact,
        request: SourceFetchRequest,
    ) -> SourceSearchParseResult:
        payload = _json_object(response.body)
        items = payload.get("data")
        if not isinstance(items, list):
            raise ValueError("IT-Jobs.uz payload is malformed")

        page = _positive_int(payload.get("page")) or 1
        total_pages = _positive_int(payload.get("totalPages")) or 1

        query_tokens = _query_tokens(request.query_variant)
        listings = tuple(
            listing
            for item in items
            if isinstance(item, dict)
            for listing in (_listing_from_item(item),)
            if listing is not None and _listing_matches_query(listing, request.query_variant)
        )

        next_request = _next_page_request(request, page=page + 1) if page < total_pages else None

        if not listings:
            if next_request is not None and not (items and query_tokens):
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(),
                    next_request=next_request,
                )
            if page > 1:
                return SourceSearchParseResult(
                    outcome=SourceOutcome.SUCCESS,
                    listings=(),
                    evidence=AttemptEvidence(multi_step_terminal=True),
                )
            return SourceSearchParseResult(
                outcome=SourceOutcome.NO_RESULTS,
                listings=(),
                evidence=AttemptEvidence(no_results=True),
            )

        return SourceSearchParseResult(
            outcome=SourceOutcome.SUCCESS,
            listings=listings,
            next_request=next_request,
        )


def _build_api_url(params: dict[str, str]) -> str:
    return f"{_API_URL}?{urlencode(params)}"


def _search_params(
    query_variant: str,
    request: SearchRequest,
    *,
    limit: int,
    page: int,
) -> dict[str, str]:
    params = {
        "search": query_variant,
        "limit": str(limit),
        "page": str(page),
    }
    category_slug = _category_slug_for_query(query_variant)
    if category_slug:
        params["category"] = category_slug
    if request.salary_from is not None:
        params["salaryMin"] = str(request.salary_from)
    return params


def _next_page_request(request: SourceFetchRequest, *, page: int) -> SourceFetchRequest:
    parsed = urlparse(request.url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["page"] = str(page)
    return SourceFetchRequest(
        source_id=request.source_id,
        query_variant=request.query_variant,
        url=urlunparse(parsed._replace(query=urlencode(params))),
    )


def _category_slug_for_query(query: str) -> str | None:
    tokens = _query_tokens(query)
    for slug, markers in _CATEGORY_MARKERS.items():
        if tokens & markers:
            return slug
    return None


def _listing_from_item(item: dict[str, Any]) -> RawListing | None:
    title = _text(item.get("title")).strip()
    slug = _text(item.get("slug")).strip()
    item_id = item.get("id")
    if not title or not slug or item_id is None:
        return None

    apply_url = _text(item.get("applyUrl")).strip()
    source_url = _text(item.get("sourceUrl")).strip()
    category = item.get("category")
    category_slug = _text(category.get("slug")).strip() if isinstance(category, dict) else ""
    category_name = _text(category.get("nameEn")).strip() if isinstance(category, dict) else ""
    salary_min = _positive_number(item.get("salaryMin"))
    salary_max = _positive_number(item.get("salaryMax"))
    currency = _text(item.get("salaryCurrency")).strip() or None
    period = _text(item.get("salaryPeriod")).strip() or None
    location = _text(item.get("location")).strip() or None
    work_type = _text(item.get("workType")).strip().upper()
    remote = work_type == "REMOTE" or None
    description = _combined_description(item)
    requirements = _text(item.get("requirements")).strip() or None
    responsibilities = _text(item.get("responsibilities")).strip() or None
    benefits = _text(item.get("benefits")).strip() or None
    company_website = _text(item.get("companyWebsite")).strip()
    company_logo = _text(item.get("companyLogo")).strip()
    source_name = _text(item.get("sourceName")).strip()
    expires_at = _text(item.get("expiresAt")).strip()
    tags = item.get("tags")
    skills = tuple(_text(tag).strip() for tag in tags if _text(tag).strip()) if isinstance(tags, list) else ()

    raw = _raw_facts(
        item_id=item_id,
        apply_url=apply_url,
        source_url=source_url,
        source_name=source_name,
        category_slug=category_slug,
        category_name=category_name,
        work_type=work_type,
        company_website=company_website,
        company_logo=company_logo,
        responsibilities=responsibilities,
        benefits=benefits,
        expires_at=expires_at,
    )
    additional_sections = {
        key: value
        for key, value in (
            ("responsibilities", responsibilities),
            ("benefits", benefits),
        )
        if value
    }

    return RawListing(
        source_listing_id=str(item_id),
        title=title,
        url=f"{_PUBLIC_BASE_URL}/{slug}",
        source="it_jobs_uz",
        company=_text(item.get("companyName")).strip() or None,
        country="Uzbekistan",
        city=location,
        location_text=location,
        salary_text=_salary_text(salary_min, salary_max, currency, period),
        salary_min=_positive_int(salary_min),
        salary_max=_positive_int(salary_max),
        salary_currency=currency,
        posted_at=_text(item.get("publishedAt") or item.get("createdAt")).strip() or None,
        remote_in_country=remote,
        remote_global=False if remote else None,
        relocation=None,
        native_grade=_native_grade(_text(item.get("experienceLevel")).strip()),
        description=description,
        requirements=requirements,
        additional_sections=additional_sections,
        skills=skills,
        raw_text=_join_text(
            title,
            location,
            description,
            requirements,
            responsibilities,
            benefits,
            work_type.casefold(),
            source_name,
            company_website,
            " ".join(skills),
        ),
        raw=raw,
    )


def _combined_description(item: dict[str, Any]) -> str | None:
    sections: list[str] = []
    for field in ("description", "requirements", "responsibilities", "benefits"):
        text = _text(item.get(field)).strip()
        if text:
            sections.append(text)
    if not sections:
        return None
    return "\n\n".join(sections)


def _raw_facts(
    *,
    item_id: object,
    apply_url: str,
    source_url: str,
    source_name: str,
    category_slug: str,
    category_name: str,
    work_type: str,
    company_website: str,
    company_logo: str,
    responsibilities: str | None,
    benefits: str | None,
    expires_at: str,
) -> dict[str, object]:
    raw: dict[str, object] = {"id": item_id}
    for key, value in (
        ("apply_url", apply_url),
        ("external_source_url", source_url),
        ("external_source", source_name),
        ("category", category_slug),
        ("category_name", category_name),
        ("company_website", company_website),
        ("company_logo", company_logo),
        ("responsibilities", responsibilities),
        ("benefits", benefits),
        ("expires_at", expires_at),
    ):
        if value:
            raw[key] = value
    if work_type:
        raw.update({"work_type": work_type.casefold(), "work_format": work_type.casefold()})
    company_facts = _company_facts(
        company_website=company_website,
        apply_url=apply_url,
        source_url=source_url,
    )
    if company_facts:
        raw["company"] = company_facts
    company_contacts = _company_contacts_from_apply_url(apply_url)
    if company_contacts:
        raw["company_contacts"] = list(company_contacts)
    return raw


def _listing_matches_query(listing: RawListing, query: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True
    searchable = " ".join(
        str(value or "")
        for value in (
            listing.title,
            listing.company,
            listing.url,
            listing.city,
            listing.location_text,
            listing.description,
            listing.requirements,
            " ".join(listing.skills),
            " ".join(str(value) for value in listing.raw.values()),
        )
    ).casefold()
    return any(token in searchable for token in tokens)


def _json_object(body: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("IT-Jobs.uz response is not a JSON object")
    return value


def _query_tokens(query: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9+#.]+", query.casefold())
        if len(token) > 1
    }


def _native_grade(value: str) -> str | None:
    if not value:
        return None
    return _EXPERIENCE_GRADE_MAP.get(value.upper(), value.casefold())


def _company_facts(*, company_website: str, apply_url: str, source_url: str) -> dict[str, object]:
    facts: dict[str, object] = {}
    site_url = _http_url(company_website)
    if site_url is not None:
        facts["companySiteUrl"] = site_url
    career_url = _company_career_url(apply_url) or _company_career_url(source_url)
    if career_url is not None:
        facts["companyVacanciesUrl"] = career_url
    return facts


def _company_contacts_from_apply_url(apply_url: str) -> tuple[dict[str, str], ...]:
    telegram_contact = _telegram_contact(apply_url)
    if telegram_contact is not None:
        return (telegram_contact,)
    email = _email_value(apply_url)
    if email:
        return (
            {
                "type": "email",
                "label": "Email",
                "value": email,
                "url": f"mailto:{email}",
                "source": "it_jobs_uz.apply_url",
            },
        )
    return ()


def _company_career_url(value: str) -> str | None:
    url = _http_url(value)
    if url is None:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    if _is_non_company_career_host(host):
        return None
    haystack = f"{host} {parsed.path} {parsed.query}".casefold()
    if not any(marker in haystack for marker in _CAREER_URL_MARKERS):
        return None
    return url


def _http_url(value: str) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    parsed = urlparse(stripped if "://" in stripped else f"https://{stripped}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def _telegram_contact(value: str) -> dict[str, str] | None:
    stripped = value.strip()
    if not stripped:
        return None
    candidate = stripped.removeprefix("telegram:").strip()
    parsed = urlparse(candidate if "://" in candidate else "")
    handle = ""
    url = ""
    if parsed.netloc.casefold() in {"t.me", "telegram.me"}:
        handle = parsed.path.strip("/").split("/", 1)[0]
        url = candidate
    elif candidate.startswith("@"):
        handle = candidate[1:].split()[0].strip("/")
        url = f"https://t.me/{handle}"
    elif candidate.casefold().startswith("t.me/"):
        handle = candidate.split("/", 1)[1].split("?", 1)[0].strip("/")
        url = f"https://{candidate}"
    if not handle:
        return None
    return {
        "type": "telegram",
        "label": "Telegram",
        "value": f"@{handle}",
        "url": url,
        "source": "it_jobs_uz.apply_url",
    }


def _email_value(value: str) -> str | None:
    match = _EMAIL_RE.search(value)
    if match is None:
        return None
    return match.group(0).casefold()


def _is_non_company_career_host(host: str) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in _NON_COMPANY_CAREER_HOSTS)


def _salary_text(
    salary_min: float | None,
    salary_max: float | None,
    currency: str | None,
    period: str | None,
) -> str | None:
    suffix = "/".join(part for part in (currency, period) if part)
    suffix = f" {suffix}".strip()
    if salary_min is not None and salary_max is not None:
        return f"{_format_number(salary_min)} - {_format_number(salary_max)}{suffix}".strip()
    if salary_min is not None:
        return f"from {_format_number(salary_min)}{suffix}".strip()
    if salary_max is not None:
        return f"to {_format_number(salary_max)}{suffix}".strip()
    return None


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return float(value)
    if isinstance(value, float) and value > 0:
        return value
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0 and value.is_integer():
        return int(value)
    return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _join_text(*parts: str | None) -> str | None:
    text = " ".join(part.strip() for part in parts if part and part.strip())
    return text or None
