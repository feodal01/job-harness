"""Typed state passed between engine components. No I/O here."""

import time
from dataclasses import asdict, dataclass, field
from typing import Literal

from src.models.brief import BriefInput
from src.models.filters import SearchFilters

SourceStatus = Literal[
    "queued", "running", "complete", "partial", "blocked", "failed", "cancelled", "interrupted", "skipped"
]
# Failures that mean the source refuses us; nothing else is sent to it for the rest of the run.
BLOCKING_KINDS = frozenset({"blocked", "captcha", "vpn_blocked", "circuit_open"})
HINTS = {
    "vpn_blocked": "Отключите VPN или исключите домен источника из VPN и запустите поиск снова",
    "captcha": "Источник показал капчу; запустите поиск позже",
    "blocked": "Источник отказал в доступе; запустите поиск позже или из другой сети",
    "circuit_open": "Источник не отвечает; запустите поиск позже",
}


@dataclass(frozen=True)
class Limits:
    max_items_per_query: int
    max_pages_per_query: int


@dataclass(frozen=True)
class RunSpec:
    """Immutable snapshot of what one run does."""

    run_id: str
    brief_id: str
    brief: BriefInput
    filters: SearchFilters
    sources: tuple[str, ...]
    limits: dict[str, Limits]
    company_profiles: dict[str, bool]
    second_chance: bool = False


@dataclass
class SourceMetrics:
    pages: int = 0
    preparation_requests: int = 0
    raw_seen: int = 0
    unique_seen: int = 0
    details_ok: int = 0
    details_reused: int = 0
    details_not_found: int = 0
    detail_errors: int = 0
    companies_ok: int = 0
    companies_reused: int = 0
    company_errors: int = 0
    listing_errors: int = 0
    skipped_blocked: int = 0
    accepted: int = 0
    rejected: int = 0
    maybe: int = 0
    prefiltered: int = 0
    descriptions_ready: int = 0
    source_total: int | None = None
    first_listing_ms: float | None = None
    first_detail_ms: float | None = None
    first_description_ms: float | None = None
    elapsed_ms: float = 0
    queries: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def failures(self) -> int:
        return self.detail_errors + self.company_errors + self.listing_errors

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class SourceState:
    status: SourceStatus = "queued"
    error: str | None = None
    blocked_reason: str | None = None
    hint: str | None = None
    limit_reached: bool = False
    metrics: SourceMetrics = field(default_factory=SourceMetrics)

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "error": self.error,
            "blocked_reason": self.blocked_reason,
            "hint": self.hint,
            "limit_reached": self.limit_reached,
            "metrics": self.metrics.to_json(),
        }


class Clock:
    """Milliseconds since the run started."""

    def __init__(self) -> None:
        self.started = time.monotonic()

    def ms(self) -> float:
        return round((time.monotonic() - self.started) * 1000, 2)
