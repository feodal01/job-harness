"""Shared enums and dataclasses for the resilient scraping architecture.

These types are the contract surface between the engine, runners, scrapers,
and the run journal. They are intentionally separate from `models.py` so the
older `SearchParams` / `JobListing` / `SearchResults` types can keep working
during the migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class Transport(StrEnum):
    """Which runner a scraper goes through."""

    HTTP = "http"
    BROWSER = "browser"


# ---------------------------------------------------------------------------
# Flag enforcement
# ---------------------------------------------------------------------------


class FilterSupport(StrEnum):
    """How honestly a scraper can enforce a request flag.

    SERVER       — flag is passed as a URL/API parameter; the site filters
                   before returning. Highest confidence.
    CLIENT       — we can read the relevant attribute from the structured
                   response (JSON field, well-defined DOM marker).
    BEST_EFFORT  — we sniff free-form text; false positives possible.
    UNSUPPORTED  — the scraper cannot enforce this flag at all.
    """

    SERVER = "server"
    CLIENT = "client"
    BEST_EFFORT = "best_effort"
    UNSUPPORTED = "unsupported"


class ScraperCapabilities(TypedDict):
    """Per-scraper, per-flag enforcement declaration.

    A scraper must declare every key; the registry tests fail CI otherwise.
    """

    remote_only: FilterSupport
    country: FilterSupport
    experience: FilterSupport
    location: FilterSupport
    has_salary: FilterSupport
    query_match: FilterSupport


# Stable list of flag names for matrix-driven tests and engine policy.
CAPABILITY_FLAGS: tuple[str, ...] = (
    "remote_only",
    "country",
    "experience",
    "location",
    "has_salary",
    "query_match",
)


# ---------------------------------------------------------------------------
# Source state + closed failure-mode taxonomy
# ---------------------------------------------------------------------------


class SourceState(StrEnum):
    """Terminal state of a source within a run."""

    OK = "ok"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    SKIPPED_UNSUPPORTED_FLAG = "skipped_unsupported_flag"


class FailureMode(StrEnum):
    """Closed taxonomy of why a source did not return OK.

    Every (state != OK) outcome must carry one of these. The CI check in
    test_run_journal walks recorded statuses and fails if any state != OK
    has no matching failure_mode.
    """

    # PARTIAL
    SLOW_PAGINATION = "slow_pagination"
    MULTI_STEP_PARTIAL = "multi_step_partial"
    # TIMEOUT
    GOTO_TIMEOUT = "goto_timeout"
    HTTP_TIMEOUT = "http_timeout"
    POOL_ACQUIRE_TIMEOUT = "pool_acquire_timeout"
    # ERROR
    POOL_RECYCLED = "pool_recycled"
    PARSE_ERROR = "parse_error"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    NETWORK_ERROR = "network_error"
    GLOBAL_NETWORK_OUTAGE = "global_network_outage"
    BROWSER_DISCONNECTED = "browser_disconnected"
    DISK_FULL = "disk_full"
    SERVER_RESTART = "server_restart"
    # RATE_LIMITED
    HTTP_429 = "http_429"
    HTTP_503_RETRY_AFTER = "http_503_retry_after"
    # BLOCKED
    ANTI_BOT_PAGE = "anti_bot_page"
    CAPTCHA_PAGE = "captcha_page"
    LOGIN_REDIRECT = "login_redirect"
    # CANCELLED
    USER_CANCELLED = "user_cancelled"
    TOTAL_TIMEOUT = "total_timeout"
    IDLE_TIMEOUT = "idle_timeout"
    # SKIPPED
    NOT_IN_COUNTRY = "not_in_country"
    NOT_IN_PROFILE = "not_in_profile"
    UNSUPPORTED_FLAG = "unsupported_flag"


# Map from FailureMode to the SourceState it belongs under. Used by the
# engine when constructing a SourceStatus and by tests asserting the
# (state, failure_mode) pair lands in the right bucket.
FAILURE_MODE_TO_STATE: dict[FailureMode, SourceState] = {
    FailureMode.SLOW_PAGINATION: SourceState.PARTIAL,
    FailureMode.MULTI_STEP_PARTIAL: SourceState.PARTIAL,
    FailureMode.GOTO_TIMEOUT: SourceState.TIMEOUT,
    FailureMode.HTTP_TIMEOUT: SourceState.TIMEOUT,
    FailureMode.POOL_ACQUIRE_TIMEOUT: SourceState.TIMEOUT,
    FailureMode.POOL_RECYCLED: SourceState.ERROR,
    FailureMode.PARSE_ERROR: SourceState.ERROR,
    FailureMode.HTTP_4XX: SourceState.ERROR,
    FailureMode.HTTP_5XX: SourceState.ERROR,
    FailureMode.NETWORK_ERROR: SourceState.ERROR,
    FailureMode.GLOBAL_NETWORK_OUTAGE: SourceState.ERROR,
    FailureMode.BROWSER_DISCONNECTED: SourceState.ERROR,
    FailureMode.DISK_FULL: SourceState.ERROR,
    FailureMode.SERVER_RESTART: SourceState.ERROR,
    FailureMode.HTTP_429: SourceState.RATE_LIMITED,
    FailureMode.HTTP_503_RETRY_AFTER: SourceState.RATE_LIMITED,
    FailureMode.ANTI_BOT_PAGE: SourceState.BLOCKED,
    FailureMode.CAPTCHA_PAGE: SourceState.BLOCKED,
    FailureMode.LOGIN_REDIRECT: SourceState.BLOCKED,
    FailureMode.USER_CANCELLED: SourceState.CANCELLED,
    FailureMode.TOTAL_TIMEOUT: SourceState.CANCELLED,
    FailureMode.IDLE_TIMEOUT: SourceState.CANCELLED,
    FailureMode.NOT_IN_COUNTRY: SourceState.SKIPPED,
    FailureMode.NOT_IN_PROFILE: SourceState.SKIPPED,
    FailureMode.UNSUPPORTED_FLAG: SourceState.SKIPPED_UNSUPPORTED_FLAG,
}


class BlockReason(StrEnum):
    """Pre-flight block detection result from the BrowserPool / HTTP probe."""

    ANTI_BOT_PAGE = "anti_bot_page"
    CAPTCHA_PAGE = "captcha_page"
    LOGIN_REDIRECT = "login_redirect"


# Block reasons map onto the BLOCKED state's failure modes.
BLOCK_REASON_TO_FAILURE_MODE: dict[BlockReason, FailureMode] = {
    BlockReason.ANTI_BOT_PAGE: FailureMode.ANTI_BOT_PAGE,
    BlockReason.CAPTCHA_PAGE: FailureMode.CAPTCHA_PAGE,
    BlockReason.LOGIN_REDIRECT: FailureMode.LOGIN_REDIRECT,
}


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


class RunState(StrEnum):
    """Lifecycle of a single search run."""

    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Engine request / status records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchRequest:
    """Validated, normalised request handed to the engine.

    Built by SearchRequest.from_mcp(...) (added when the engine lands).
    Kept frozen so the run journal can record it once and the engine can
    pass it around without worrying about mutation.
    """

    query: str
    country: str | None = None
    remote_only: bool = False
    experience_levels: tuple[str, ...] = ()
    location: str | None = None
    max_results: int = 20

    sources: tuple[str, ...] | None = None        # None == "all"
    profile: str | None = None                    # "fast" | "full" | None

    detail: bool = False
    resolve: bool = False
    cache: bool = True

    exclude_keywords: tuple[str, ...] = ()
    exclude_keywords_context: tuple[str, ...] = ()
    exclude_companies: tuple[str, ...] = ()
    has_salary: bool = False

    strict_flags: bool = True
    dedupe: bool = True

    source_timeout_ms: int = 30_000
    total_timeout_ms: int = 90_000
    resolve_timeout_ms_per_company: int = 8_000

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "country": self.country,
            "remote_only": self.remote_only,
            "experience_levels": list(self.experience_levels),
            "location": self.location,
            "max_results": self.max_results,
            "sources": list(self.sources) if self.sources is not None else None,
            "profile": self.profile,
            "detail": self.detail,
            "resolve": self.resolve,
            "cache": self.cache,
            "exclude_keywords": list(self.exclude_keywords),
            "exclude_keywords_context": list(self.exclude_keywords_context),
            "exclude_companies": list(self.exclude_companies),
            "has_salary": self.has_salary,
            "strict_flags": self.strict_flags,
            "dedupe": self.dedupe,
            "source_timeout_ms": self.source_timeout_ms,
            "total_timeout_ms": self.total_timeout_ms,
            "resolve_timeout_ms_per_company": self.resolve_timeout_ms_per_company,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchRequest:
        """Reconstruct a frozen request from a journal snapshot."""
        raw_sources = data.get("sources")
        sources: tuple[str, ...] | None
        if raw_sources is None:
            sources = None
        elif isinstance(raw_sources, list):
            sources = tuple(str(item) for item in raw_sources)
        else:
            sources = (str(raw_sources),)

        def _tuple_field(name: str) -> tuple[str, ...]:
            value = data.get(name) or ()
            if isinstance(value, list):
                return tuple(str(item) for item in value)
            if isinstance(value, str):
                return (value,)
            return ()

        return cls(
            query=str(data.get("query", "")),
            country=data.get("country"),
            remote_only=bool(data.get("remote_only", False)),
            experience_levels=tuple(str(item) for item in data.get("experience_levels") or ()),
            location=data.get("location"),
            max_results=int(data.get("max_results", 20)),
            sources=sources,
            profile=data.get("profile"),
            detail=bool(data.get("detail", False)),
            resolve=bool(data.get("resolve", False)),
            cache=bool(data.get("cache", True)),
            exclude_keywords=_tuple_field("exclude_keywords"),
            exclude_keywords_context=_tuple_field("exclude_keywords_context"),
            exclude_companies=_tuple_field("exclude_companies"),
            has_salary=bool(data.get("has_salary", False)),
            strict_flags=bool(data.get("strict_flags", True)),
            dedupe=bool(data.get("dedupe", True)),
            source_timeout_ms=int(data.get("source_timeout_ms", 30_000)),
            total_timeout_ms=int(data.get("total_timeout_ms", 90_000)),
            resolve_timeout_ms_per_company=int(
                data.get("resolve_timeout_ms_per_company", 8_000)
            ),
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class SourceStatus:
    """Per-source observation recorded in the journal and result summary.

    `state != OK` MUST be paired with a populated `failure_mode`.
    `state == OK` MUST have `failure_mode is None`.
    The RunJournal writer enforces this; tests in test_run_journal.py
    walk the closed enum to lock it down.
    """

    source: str
    display_name: str
    transport: Transport
    state: SourceState
    failure_mode: FailureMode | None
    duration_ms: int
    raw_count: int = 0
    after_filter_count: int = 0
    after_dedupe_count: int = 0
    company_missing_count: int = 0
    retries: int = 0
    flag_enforcement: dict[str, FilterSupport] = field(default_factory=dict)
    anti_bot_signal: str | None = None
    error_class: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.state == SourceState.OK and self.failure_mode is not None:
            raise ValueError(
                f"SourceStatus for {self.source}: state=OK but failure_mode={self.failure_mode!r}"
            )
        if self.state != SourceState.OK and self.failure_mode is None:
            raise ValueError(
                f"SourceStatus for {self.source}: state={self.state} requires a failure_mode"
            )
        if self.failure_mode is not None:
            expected = FAILURE_MODE_TO_STATE.get(self.failure_mode)
            if expected is None:
                raise ValueError(
                    f"SourceStatus for {self.source}: unknown failure_mode {self.failure_mode!r}"
                )
            if expected != self.state:
                raise ValueError(
                    f"SourceStatus for {self.source}: failure_mode={self.failure_mode} "
                    f"belongs under state={expected}, not {self.state}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "display_name": self.display_name,
            "transport": self.transport.value,
            "state": self.state.value,
            "failure_mode": self.failure_mode.value if self.failure_mode is not None else None,
            "duration_ms": self.duration_ms,
            "raw_count": self.raw_count,
            "after_filter_count": self.after_filter_count,
            "after_dedupe_count": self.after_dedupe_count,
            "company_missing_count": self.company_missing_count,
            "retries": self.retries,
            "flag_enforcement": {k: v.value for k, v in self.flag_enforcement.items()},
            "anti_bot_signal": self.anti_bot_signal,
            "error_class": self.error_class,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceStatus:
        failure_mode_raw = data.get("failure_mode")
        return cls(
            source=data["source"],
            display_name=data.get("display_name", data["source"]),
            transport=Transport(data["transport"]),
            state=SourceState(data["state"]),
            failure_mode=FailureMode(failure_mode_raw) if failure_mode_raw else None,
            duration_ms=int(data.get("duration_ms", 0)),
            raw_count=int(data.get("raw_count", 0)),
            after_filter_count=int(data.get("after_filter_count", 0)),
            after_dedupe_count=int(data.get("after_dedupe_count", 0)),
            company_missing_count=int(data.get("company_missing_count", 0)),
            retries=int(data.get("retries", 0)),
            flag_enforcement={
                k: FilterSupport(v) for k, v in (data.get("flag_enforcement") or {}).items()
            },
            anti_bot_signal=data.get("anti_bot_signal"),
            error_class=data.get("error_class"),
            error_message=data.get("error_message"),
        )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string with seconds precision."""
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
