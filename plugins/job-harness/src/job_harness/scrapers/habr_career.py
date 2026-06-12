"""Habr Career scraper."""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from typing import ClassVar
from urllib.error import URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from job_harness.base import BaseScraper
from job_harness.models import RawListing, SearchParams
from job_harness.registry import register_scraper
from job_harness.types import FilterSupport, ScraperCapabilities, SearchCriterion, SourceGroup

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
_FETCH_ATTEMPTS = 3
_FETCH_TIMEOUT_SECONDS = 10


class _HabrVacancyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self.next_href: str | None = None
        self._card: dict | None = None
        self._card_depth = 0
        self._company_depth: int | None = None
        self._skills_depth: int | None = None
        self._capture: tuple[str, int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())

        if tag == "a" and self.next_href is None:
            rel = set((attr.get("rel") or "").split())
            if "next" in rel or "with-pagination__side-button--next" in classes:
                self.next_href = attr.get("href")

        if self._card is None:
            if tag == "div" and "vacancy-card" in classes:
                self._card = {"skills": [], "chips": [], "text": []}
                self._card_depth = 1
            return

        if tag not in _VOID_TAGS:
            self._card_depth += 1

        if tag == "div" and "vacancy-card__company" in classes:
            self._company_depth = self._card_depth
        elif tag == "a" and "vacancy-card__title-link" in classes:
            self._card["href"] = attr.get("href") or ""
            self._capture = ("title", self._card_depth)
        elif tag == "a" and self._company_depth is not None and not self._card.get("company"):
            self._capture = ("company", self._card_depth)
        elif "basic-salary" in classes:
            self._capture = ("salary", self._card_depth)
        elif "vacancy-card__skills-chip" in classes:
            self._skills_depth = self._card_depth
        elif "basic-chip__text" in classes and self._skills_depth is not None:
            self._capture = ("skill", self._card_depth)
        elif "chip-with-icon__text" in classes:
            self._capture = ("chip", self._card_depth)

    def handle_data(self, data: str) -> None:
        if self._card is None:
            return

        text = data.strip()
        if not text:
            return

        self._card["text"].append(text)
        if self._capture is None:
            return

        target, _ = self._capture
        if target == "skill":
            self._card["skills"].append(text)
        elif target == "chip":
            self._card["chips"].append(text)
        else:
            current = self._card.get(target, "")
            self._card[target] = f"{current} {text}".strip() if current else text

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return

        if self._capture and self._capture[1] == self._card_depth:
            self._capture = None
        if self._company_depth == self._card_depth:
            self._company_depth = None
        if self._skills_depth == self._card_depth:
            self._skills_depth = None

        if tag not in _VOID_TAGS:
            self._card_depth -= 1

        if self._card_depth == 0:
            self.cards.append(self._card)
            self._card = None


class _HabrDetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.description_parts: list[str] = []
        self._description_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if self._description_depth is None:
            if tag == "div" and "vacancy-description__text" in classes:
                self._description_depth = 1
            return
        if tag not in _VOID_TAGS:
            self._description_depth += 1

    def handle_data(self, data: str) -> None:
        if self._description_depth is None:
            return
        text = data.strip()
        if text:
            self.description_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._description_depth is None:
            return
        if tag not in _VOID_TAGS:
            self._description_depth -= 1
        if self._description_depth == 0:
            self._description_depth = None

    @property
    def description(self) -> str | None:
        if not self.description_parts:
            return None
        return "\n".join(self.description_parts)[:3000]


@register_scraper("habr_career")
class HabrCareerScraper(BaseScraper):
    display_name = "Habr Career"
    BASE_URL = "https://career.habr.com/vacancies"
    countries = ("RU",)
    requires_browser = False
    detail_requires_browser = False
    source_group = SourceGroup.AGGREGATOR
    source_limit = 100
    server_criteria = frozenset(
        {
            SearchCriterion.QUERY,
            SearchCriterion.COUNTRY,
            SearchCriterion.REMOTE_ONLY,
            SearchCriterion.EXPERIENCE_LEVELS,
            SearchCriterion.SALARY_FROM,
        }
    )

    capabilities: ClassVar[ScraperCapabilities] = {
        "remote_only": FilterSupport.SERVER,           # remote=true URL param
        "country": FilterSupport.CLIENT,               # RU-only by design
        "experience": FilterSupport.SERVER,            # qualification=
        "location": FilterSupport.BEST_EFFORT,
        "has_salary": FilterSupport.UNSUPPORTED,
        "query_match": FilterSupport.SERVER,           # q=
    }

    def search(self, params: SearchParams) -> list[RawListing]:
        listings: list[RawListing] = []
        url: str | None = self._build_search_url(params)

        try:
            while url and len(listings) < self.max_results:
                html = self._fetch_html(url)
                page_listings, next_url = self._parse_search_results(html, url)
                if not page_listings:
                    break
                listings.extend(page_listings)
                url = next_url

        except Exception as e:
            print(f"HabrCareerScraper error: {e}", file=sys.stderr)
            raise

        return listings[:self.max_results]

    def fetch_detail(self, listing: RawListing) -> RawListing:
        try:
            html = self._fetch_html(listing.url)
            parser = _HabrDetailParser()
            parser.feed(html)
            return RawListing(
                title=listing.title,
                url=listing.url,
                company=listing.company,
                country=listing.country,
                salary=listing.salary,
                experience=listing.experience,
                remote=listing.remote,
                location=listing.location,
                description=parser.description,
                requirements=listing.requirements,
                skills=tuple(listing.skills),
                posted_date=listing.posted_date,
                source=listing.source,
                raw=listing.raw,
            )
        except Exception as e:
            print(f"Error fetching detail for {listing.url}: {e}", file=sys.stderr)
            return listing

    def _build_search_url(self, params: SearchParams) -> str:
        query_params = {"q": params.query, "type": "all"}
        if params.remote_only:
            query_params["remote"] = "true"
        if len(params.experience_levels) == 1:
            query_params["qualification"] = params.experience_levels[0]
        if params.salary_from is not None:
            query_params["salary"] = str(params.salary_from)
        query_params.update(params.extra)
        return self.BASE_URL + "?" + urlencode(query_params)

    def _fetch_html(self, url: str) -> str:
        last_error: Exception | None = None
        for _ in range(_FETCH_ATTEMPTS):
            try:
                request = Request(url, headers={"User-Agent": _USER_AGENT})
                timeout_seconds = self.fetch_timeout_seconds or _FETCH_TIMEOUT_SECONDS
                with urlopen(request, timeout=timeout_seconds) as response:
                    return response.read().decode("utf-8", errors="replace")
            except (OSError, TimeoutError, URLError) as e:
                last_error = e
        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to fetch {url}")

    def _parse_search_results(self, html: str, page_url: str) -> tuple[list[RawListing], str | None]:
        parser = _HabrVacancyParser()
        parser.feed(html)
        listings = []

        for card in parser.cards:
            title = card.get("title", "").strip()
            href = card.get("href", "")
            if not title or not href:
                continue
            url = "https://career.habr.com" + href if href.startswith("/") else href
            experience = None
            for chip_text in card.get("chips", []):
                experience = self.normalize_experience(chip_text)
                if experience:
                    break

            salary = card.get("salary", "").strip()
            card_text = " ".join(card.get("text", []))
            listings.append(RawListing(
                title=title,
                url=url,
                company=card.get("company", "").strip(),
                country="RU",
                salary=salary or None,
                experience=experience,
                remote="Можно удалённо" in card_text or "Можно из дома" in card_text,
                source=self.name,
                skills=tuple(card.get("skills", [])),
            ))

        next_url = urljoin(page_url, parser.next_href) if parser.next_href else None
        return listings, next_url
