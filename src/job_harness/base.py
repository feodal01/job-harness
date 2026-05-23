"""Abstract base class for all job scrapers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from job_harness.models import JobListing, SearchParams

# Experience level ordering for normalization
_EXP_ORDER = {"junior": 0, "middle": 1, "senior": 2}


class BaseScraper(ABC):
    name: str = ""
    display_name: str = ""

    def __init__(self, context, max_results: int = 20, debug: bool = False):
        self.context = context
        self.max_results = max_results
        self.debug = debug

    @abstractmethod
    def search(self, params: SearchParams) -> list[JobListing]:
        """Search for job listings matching the given parameters."""

    @abstractmethod
    def fetch_detail(self, listing: JobListing) -> JobListing:
        """Fetch full details for a single listing. Returns enriched JobListing."""

    def normalize_experience(self, raw: str | None) -> str | None:
        """Normalize experience string to junior/middle/senior."""
        if not raw:
            return None
        lower = raw.lower()
        for level in ("senior", "middle", "junior"):
            if level in lower:
                return level
        # Try numeric ranges
        if "1" in lower and "3" in lower:
            return "middle"
        if "3" in lower and "6" in lower:
            return "senior"
        if "нет" in lower or "no " in lower or "без" in lower:
            return "junior"
        return None

    def _debug_screenshot(self, page, name: str) -> None:
        if self.debug:
            safe = re.sub(r'[^\w]', '_', name)
            page.screenshot(path=f"debug_{self.name}_{safe}.png")
