"""Abstract base class for all job scrapers."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from typing import ClassVar

from job_harness.models import JobListing, SearchParams
from job_harness.types import (
    CAPABILITY_FLAGS,
    FilterSupport,
    ScraperCapabilities,
    Transport,
)

# Experience level ordering for normalization
_EXP_ORDER = {"junior": 0, "middle": 1, "senior": 2}

# Default capability matrix — every flag UNSUPPORTED. Subclasses MUST
# override with an honest declaration; the capability-matrix test fails
# CI if a class leaves the default.
_DEFAULT_CAPABILITIES: ScraperCapabilities = {
    "remote_only": FilterSupport.UNSUPPORTED,
    "country": FilterSupport.UNSUPPORTED,
    "experience": FilterSupport.UNSUPPORTED,
    "location": FilterSupport.UNSUPPORTED,
    "has_salary": FilterSupport.UNSUPPORTED,
    "query_match": FilterSupport.UNSUPPORTED,
}


class BaseScraper(ABC):
    name: str = ""
    display_name: str = ""
    countries: tuple[str, ...] = ()
    requires_browser: bool = True
    detail_requires_browser: bool = True

    # New capability surface — see plans/resilient-scraping.md §3.
    # Subclasses declare per-flag enforcement honesty. The capability
    # matrix test (tests/test_capability_matrix.py) asserts every
    # registered scraper overrides this with an explicit declaration.
    capabilities: ClassVar[ScraperCapabilities] = _DEFAULT_CAPABILITIES

    def __init__(self, context, max_results: int = 20, debug: bool = False, timeout_ms: int | None = None):
        self.context = context
        self.max_results = max_results
        self.debug = debug
        self.timeout_ms = timeout_ms
        self._started_at = time.monotonic()
        self.timed_out = False
        self.runtime_error: Exception | None = None

    @classmethod
    def transport(cls) -> Transport:
        """Which runner the engine will use to dispatch this scraper."""
        return Transport.BROWSER if cls.requires_browser else Transport.HTTP

    @classmethod
    def declares_full_capabilities(cls) -> bool:
        """True iff this class explicitly set its `capabilities` ClassVar.

        Used by the capability-matrix test to fail loudly if a scraper
        is added without an honest declaration.
        """
        # Identity check against the module-level default sentinel —
        # subclasses that override with their own dict are not == it.
        return cls.capabilities is not _DEFAULT_CAPABILITIES and all(
            key in cls.capabilities for key in CAPABILITY_FLAGS
        )

    @property
    def fetch_timeout_seconds(self) -> float | None:
        if self.timeout_ms is None:
            return None
        return max(self.remaining_timeout_ms(), 1) / 1000

    def remaining_timeout_ms(self) -> int:
        if self.timeout_ms is None:
            return 30_000
        elapsed_ms = int((time.monotonic() - self._started_at) * 1000)
        return max(self.timeout_ms - elapsed_ms, 0)

    def is_deadline_expired(self) -> bool:
        return self.timeout_ms is not None and self.remaining_timeout_ms() <= 0

    def enforce_deadline(self) -> None:
        if self.is_deadline_expired():
            self.timed_out = True
            raise TimeoutError(f"{self.name} timed out after {self.timeout_ms}ms")

    def mark_timed_out(self) -> None:
        self.timed_out = True

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

    @classmethod
    def supports_country(cls, country: str | None) -> bool:
        return country is None or not cls.countries or country in cls.countries

    def _debug_screenshot(self, page, name: str) -> None:
        if self.debug:
            safe = re.sub(r'[^\w]', '_', name)
            page.screenshot(path=f"debug_{self.name}_{safe}.png")
