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
# Strict search-layer catalog
# ---------------------------------------------------------------------------


class SourceGroup(StrEnum):
    AGGREGATOR = "aggregator"
    COMPANY_CAREER = "company_career"
    DIRECTORY = "directory"
    OTHER = "other"


class SearchCriterion(StrEnum):
    QUERY = "query"
    COUNTRY = "country"
    REMOTE_ONLY = "remote_only"
    EXPERIENCE_LEVELS = "experience_levels"
    LOCATION = "location"
    SALARY_FROM = "salary_from"
    FRESHNESS = "freshness"


@dataclass(frozen=True)
class SearchCriteriaRequest:
    query: str
    country: str | None = None
    remote_only: bool = False
    experience_levels: tuple[str, ...] = ()
    location: str | None = None
    salary_from: int | None = None
    freshness_days: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "country": self.country,
            "remote_only": self.remote_only,
            "experience_levels": list(self.experience_levels),
            "location": self.location,
            "salary_from": self.salary_from,
            "freshness_days": self.freshness_days,
        }


@dataclass(frozen=True)
class SourceDescriptor:
    group: SourceGroup
    countries: tuple[str, ...]
    server_criteria: frozenset[SearchCriterion]
    source_limit: int

    def __post_init__(self) -> None:
        if self.source_limit < 1:
            raise ValueError("source_limit must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group.value,
            "countries": list(self.countries),
            "server_criteria": sorted(criterion.value for criterion in self.server_criteria),
            "source_limit": self.source_limit,
        }


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
class SearchRequest(SearchCriteriaRequest):
    """Validated, normalised request handed to the engine.

    Built by SearchRequest.from_mcp(...) (added when the engine lands).
    Kept frozen so the run journal can record it once and the engine can
    pass it around without worrying about mutation.
    """

    max_results: int = 20

    sources: tuple[str, ...] | None = None        # None == "all"
    source_groups: tuple[SourceGroup, ...] = ()
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

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "country": self.country,
            "remote_only": self.remote_only,
            "experience_levels": list(self.experience_levels),
            "location": self.location,
            "salary_from": self.salary_from,
            "freshness_days": self.freshness_days,
            "max_results": self.max_results,
            "sources": list(self.sources) if self.sources is not None else None,
            "source_groups": [group.value for group in self.source_groups],
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

        def _source_groups() -> tuple[SourceGroup, ...]:
            value = data.get("source_groups") or ()
            raw_items: tuple[str, ...]
            if isinstance(value, list):
                raw_items = tuple(str(item) for item in value)
            elif isinstance(value, str):
                raw_items = tuple(item.strip() for item in value.split(",") if item.strip())
            else:
                raw_items = ()
            return tuple(SourceGroup(item) for item in raw_items)

        return cls(
            query=str(data.get("query", "")),
            country=data.get("country"),
            remote_only=bool(data.get("remote_only", False)),
            experience_levels=tuple(str(item) for item in data.get("experience_levels") or ()),
            location=data.get("location"),
            salary_from=(
                int(data["salary_from"]) if data.get("salary_from") is not None else None
            ),
            freshness_days=(
                int(data["freshness_days"]) if data.get("freshness_days") is not None else None
            ),
            max_results=int(data.get("max_results", 20)),
            sources=sources,
            source_groups=_source_groups(),
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
            extra=dict(data.get("extra") or {}),
        )


@dataclass(frozen=True)
class SourceStatus:
    """Per-source raw search summary recorded in the journal.

    `state != OK` MUST be paired with a populated `failure_mode`.
    `state == OK` MUST have `failure_mode is None`.
    The RunJournal writer enforces this; tests in test_run_journal.py
    walk the closed enum to lock it down.
    """

    source: str
    group: SourceGroup
    state: SourceState
    failure_mode: FailureMode | None
    source_limit: int
    deadline_ms: int
    elapsed_ms: int | None = None
    requested_criteria: dict[str, Any] = field(default_factory=dict)
    supported_server_criteria: tuple[SearchCriterion, ...] = ()
    server_criteria_used: tuple[SearchCriterion, ...] = ()
    unsupported_requested_criteria: tuple[SearchCriterion, ...] = ()
    pages_visited: int | None = None
    listings_written: int = 0
    attempts: int = 1
    retries: int = 0
    limit_reached: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        if self.source_limit < 1:
            raise ValueError(f"SourceStatus for {self.source}: source_limit must be >= 1")
        if self.deadline_ms < 1:
            raise ValueError(f"SourceStatus for {self.source}: deadline_ms must be >= 1")
        if self.attempts < 0:
            raise ValueError(f"SourceStatus for {self.source}: attempts must be >= 0")
        if self.retries < 0:
            raise ValueError(f"SourceStatus for {self.source}: retries must be >= 0")
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
            "group": self.group.value,
            "state": self.state.value,
            "failure_mode": self.failure_mode.value if self.failure_mode is not None else None,
            "source_limit": self.source_limit,
            "deadline_ms": self.deadline_ms,
            "elapsed_ms": self.elapsed_ms,
            "requested_criteria": dict(self.requested_criteria),
            "supported_server_criteria": [
                criterion.value for criterion in self.supported_server_criteria
            ],
            "server_criteria_used": [
                criterion.value for criterion in self.server_criteria_used
            ],
            "unsupported_requested_criteria": [
                criterion.value for criterion in self.unsupported_requested_criteria
            ],
            "pages_visited": self.pages_visited,
            "listings_written": self.listings_written,
            "attempts": self.attempts,
            "retries": self.retries,
            "limit_reached": self.limit_reached,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceStatus:
        failure_mode_raw = data.get("failure_mode")
        def _criteria(name: str) -> tuple[SearchCriterion, ...]:
            return tuple(SearchCriterion(item) for item in data.get(name) or ())

        return cls(
            source=data["source"],
            group=SourceGroup(data.get("group", SourceGroup.OTHER.value)),
            state=SourceState(data["state"]),
            failure_mode=FailureMode(failure_mode_raw) if failure_mode_raw else None,
            source_limit=int(data.get("source_limit", 1)),
            deadline_ms=int(data.get("deadline_ms", 1)),
            elapsed_ms=(
                int(data["elapsed_ms"]) if data.get("elapsed_ms") is not None else None
            ),
            requested_criteria=dict(data.get("requested_criteria") or {}),
            supported_server_criteria=_criteria("supported_server_criteria"),
            server_criteria_used=_criteria("server_criteria_used"),
            unsupported_requested_criteria=_criteria("unsupported_requested_criteria"),
            pages_visited=(
                int(data["pages_visited"]) if data.get("pages_visited") is not None else None
            ),
            listings_written=int(data.get("listings_written", data.get("raw_count", 0))),
            attempts=int(data.get("attempts", 1)),
            retries=int(data.get("retries", 0)),
            limit_reached=bool(data.get("limit_reached", False)),
            error=data.get("error") or data.get("error_message"),
        )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string with seconds precision."""
    return datetime.now(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
