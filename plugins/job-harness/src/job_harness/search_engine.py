"""SearchEngine — orchestrates one run from request to journal-backed result.

Composes:
  • Capability matrix (types.ScraperCapabilities) for honest flag policy
  • HttpRunner for HTTP scrapers (concurrent via asyncio + dedicated executor)
  • BrowserPool for browser scrapers (concurrent via asyncio.Semaphore)
  • RunJournal for durable per-source persistence
  • Existing filter / dedupe code from search_runner.py for post-processing

Flow:
  1. Validate the request.
  2. Resolve sources × country × profile × capability policy. Eligible
     sources get journaled with `source_started`; ineligible ones get a
     SKIPPED status with the right failure mode.
  3. Dispatch concurrently: HTTP through HttpRunner, browser sources
     are recorded as SKIPPED (NOT_IN_PROFILE) until they're migrated to
     the async pool in the next phase.
  4. As each source finishes, write its listings then its source_status
     to the journal. The journal is the source of truth — partial runs
     are recoverable.
  5. Final merge: apply the filter plan, dedupe, truncate to max_results,
     build summary.flag_enforcement and summary.result_sanity.
  6. Write run_finished. Return SearchResults.

Cancellation: `asyncio.CancelledError` from the caller propagates through
asyncio.gather; the engine catches it at the outermost layer, writes a
final state=cancelled to the journal, and re-raises.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any

import job_harness.registry as registry
from job_harness.dedupe_filter import (
    apply_filter_plan,
    build_filter_plan,
    dedupe_listings,
)
from job_harness.http_runner import HttpRunner, SourceOutcome
from job_harness.models import JobListing, SearchParams, SearchResults
from job_harness.run_journal import (
    RunJournalReader,
    RunJournalWriter,
    materialize_listings,
)
from job_harness.types import (
    CAPABILITY_FLAGS,
    FailureMode,
    FilterSupport,
    RunState,
    SearchRequest,
    SourceState,
    SourceStatus,
    Transport,
)

ProgressSink = Callable[[str], None] | None


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SourceEntry:
    """A source eligible for dispatch."""

    name: str
    display_name: str
    transport: Transport
    capabilities: dict[str, FilterSupport]


@dataclass(frozen=True)
class _SkipEntry:
    """A source skipped before dispatch (with the reason)."""

    name: str
    display_name: str
    transport: Transport
    capabilities: dict[str, FilterSupport]
    failure_mode: FailureMode
    note: str


def _resolve_sources(
    request: SearchRequest,
) -> tuple[list[_SourceEntry], list[_SkipEntry]]:
    """Partition the registry into eligible sources and skipped sources.

    Country and profile route sources entirely. The capability policy
    drops scrapers whose capability for a requested flag is UNSUPPORTED
    when `strict_flags=True`.
    """
    if request.sources is None or list(request.sources) == ["all"]:
        # Walk the entire registry; supports_country handles None too.
        candidate_names = [n for n, _ in registry.iter_registered()]
    else:
        candidate_names = list(request.sources)

    eligible: list[_SourceEntry] = []
    skipped: list[_SkipEntry] = []
    requested_flags = _requested_flags(request)

    for name in candidate_names:
        try:
            cls = registry.get_scraper_class(name)
        except ValueError as exc:
            skipped.append(
                _SkipEntry(
                    name=name,
                    display_name=name,
                    transport=Transport.HTTP,
                    capabilities={},
                    failure_mode=FailureMode.NOT_IN_PROFILE,
                    note=str(exc),
                )
            )
            continue

        transport = cls.transport()
        capabilities = _capabilities_view(cls.capabilities)

        # Country routing — uses the existing class helper.
        if not cls.supports_country(request.country):
            skipped.append(
                _SkipEntry(
                    name=name,
                    display_name=cls.display_name,
                    transport=transport,
                    capabilities=capabilities,
                    failure_mode=FailureMode.NOT_IN_COUNTRY,
                    note=f"scraper does not support country={request.country!r}",
                )
            )
            continue

        # Profile routing — fast profile skips browser-backed sources.
        if request.profile == "fast" and transport == Transport.BROWSER:
            skipped.append(
                _SkipEntry(
                    name=name,
                    display_name=cls.display_name,
                    transport=transport,
                    capabilities=capabilities,
                    failure_mode=FailureMode.NOT_IN_PROFILE,
                    note="browser scraper skipped under profile=fast",
                )
            )
            continue

        # Strict-flag policy.
        if request.strict_flags:
            unsupported_flag = next(
                (
                    flag
                    for flag in requested_flags
                    if capabilities.get(flag, FilterSupport.UNSUPPORTED) == FilterSupport.UNSUPPORTED
                ),
                None,
            )
            if unsupported_flag is not None:
                skipped.append(
                    _SkipEntry(
                        name=name,
                        display_name=cls.display_name,
                        transport=transport,
                        capabilities=capabilities,
                        failure_mode=FailureMode.UNSUPPORTED_FLAG,
                        note=f"scraper does not enforce {unsupported_flag}",
                    )
                )
                continue

        eligible.append(
            _SourceEntry(
                name=name,
                display_name=cls.display_name,
                transport=transport,
                capabilities=capabilities,
            )
        )

    return eligible, skipped


def _requested_flags(request: SearchRequest) -> tuple[str, ...]:
    """The user-requested filter flags that drive the strict-flag policy."""
    flags: list[str] = []
    if request.remote_only:
        flags.append("remote_only")
    if request.has_salary:
        flags.append("has_salary")
    if request.experience:
        flags.append("experience")
    if request.location:
        flags.append("location")
    # query_match is always requested; we don't enforce strict on it
    # because every scraper declares at least best_effort.
    return tuple(flags)


def _capabilities_view(caps: Any) -> dict[str, FilterSupport]:
    """Materialise the scraper's TypedDict into a plain dict keyed by
    CAPABILITY_FLAGS, filling missing keys with UNSUPPORTED."""
    out: dict[str, FilterSupport] = {}
    for flag in CAPABILITY_FLAGS:
        raw = caps.get(flag) if isinstance(caps, dict) else getattr(caps, flag, None)
        if isinstance(raw, FilterSupport):
            out[flag] = raw
        else:
            try:
                out[flag] = FilterSupport(str(raw)) if raw is not None else FilterSupport.UNSUPPORTED
            except (ValueError, TypeError):
                out[flag] = FilterSupport.UNSUPPORTED
    return out


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SearchEngine:
    """One instance per process. Stateless across runs (per-run state
    lives on RunJournalWriter / scraper instances)."""

    def __init__(
        self,
        *,
        http_runner: HttpRunner | None = None,
        browser_pool: Any | None = None,
        scraper_factory: Callable[[str, int, int, bool], Any] | None = None,
        sanity_baselines_path: Path | None = None,
    ) -> None:
        # http_runner is owned externally so callers can shutdown() its
        # executor explicitly when the process exits.
        self._http_runner = http_runner or HttpRunner()
        # browser_pool is optional: when not provided, browser sources
        # are recorded as SKIPPED (no browser available). The MCP layer
        # injects a real pool at module load; tests that don't exercise
        # browser sources can leave it as None.
        self._browser_pool = browser_pool
        self._scraper_factory = scraper_factory or _default_scraper_factory
        self._sanity_baselines_path = sanity_baselines_path

    @property
    def http_runner(self) -> HttpRunner:
        return self._http_runner

    @property
    def browser_pool(self) -> Any | None:
        return self._browser_pool

    async def execute(
        self,
        request: SearchRequest,
        *,
        journal: RunJournalWriter,
        run_id: str,
        progress: ProgressSink = None,
    ) -> SearchResults:
        """Run one search to completion. Writes everything to `journal`.

        Cancellation: an `asyncio.CancelledError` from the caller cancels
        the gather; the engine writes state=cancelled and re-raises so
        the caller can map it to its own surface.
        """
        _validate_request(request)
        params = _request_to_params(request)
        journal.write_run_started(run_id=run_id, request=request)
        _emit(progress, f"run {run_id} started")

        eligible, skipped = _resolve_sources(request)

        # Journal the skipped sources up-front so the snapshot reflects
        # them even if cancellation hits before any dispatch.
        for skip in skipped:
            self._record_skip(journal, skip)

        # Build the filter plan once; same as today's search_runner.
        filter_plan = build_filter_plan(
            remote_only=request.remote_only,
            has_salary=request.has_salary,
            exclude_companies=",".join(request.exclude_companies) or None,
            experience=request.experience,
            exclude_keywords=",".join(request.exclude_keywords) or None,
            exclude_keywords_context=",".join(request.exclude_keywords_context) or None,
            location=request.location,
        )

        outcomes: list[SourceOutcome] = []
        all_listings: list[JobListing] = []
        errors: list[str] = []
        run_state = RunState.COMPLETED

        try:
            tasks = [
                asyncio.create_task(
                    self._run_one_source(entry, request, params, journal, progress),
                    name=f"engine:source:{entry.name}",
                )
                for entry in eligible
            ]
            # Bound the whole run by total_timeout_ms; sources still in
            # flight at the deadline are cancelled.
            total_timeout_s = max(0.001, request.total_timeout_ms / 1000.0)
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=total_timeout_s,
                )
            except TimeoutError:
                # Cancel anything still pending; they will surface as
                # cancelled outcomes which we map to TOTAL_TIMEOUT below.
                for t in tasks:
                    if not t.done():
                        t.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                run_state = RunState.CANCELLED

            for entry, result in zip(eligible, results, strict=True):
                outcomes.append(self._coerce_outcome(entry, result, run_state))

            for outcome in outcomes:
                # Listings recorded per-source as they complete.
                for listing in outcome.listings:
                    journal.write_listing(source=outcome.status.source, listing=listing.to_dict())
                journal.write_source_status(outcome.status)
                if outcome.status.error_message and outcome.status.state != SourceState.OK:
                    errors.append(f"{outcome.status.source}: {outcome.status.error_message}")
                all_listings.extend(outcome.listings)

        except asyncio.CancelledError:
            # External cancel — flush partial outcomes already recorded
            # to the journal, mark run cancelled, re-raise so the caller
            # (RunRegistry) can map it.
            run_state = RunState.CANCELLED
            journal.write_run_finished(
                state=run_state, final_listings_count=len(all_listings), errors=errors,
            )
            self._rewrite_summary(journal)
            raise

        # Post-processing: apply filters in code (don't trust scrapers),
        # dedupe, truncate.
        filtered = apply_filter_plan(all_listings, filter_plan)
        deduped = dedupe_listings(filtered) if request.dedupe else filtered
        final = deduped[: request.max_results]

        # Build the rich summary.
        flag_enforcement = _build_flag_enforcement_summary(
            request=request, eligible=eligible, skipped=skipped, outcomes=outcomes,
        )
        result_sanity = self._build_result_sanity(
            request=request, outcomes=outcomes,
        )
        summary = {
            "source_statuses": [o.status.to_dict() for o in outcomes]
            + [self._skip_status_dict(s) for s in skipped],
            "flag_enforcement": flag_enforcement,
            "result_sanity": result_sanity,
            "filters": {
                "enabled": [item.name for item in filter_plan],
                "before": len(all_listings),
                "after": len(filtered),
                "removed": len(all_listings) - len(filtered),
            },
            "dedupe": {
                "enabled": request.dedupe,
                "before": len(filtered),
                "after": len(deduped),
                "removed": len(filtered) - len(deduped),
            },
            "max_results": {
                "requested": request.max_results,
                "returned": len(final),
            },
        }

        journal.write_run_finished(
            state=run_state, final_listings_count=len(final), errors=errors,
        )
        self._rewrite_summary(journal)

        return SearchResults(
            params=params,
            listings=final,
            errors=errors,
            summary=summary,
        )

    async def execute_retry(
        self,
        request: SearchRequest,
        *,
        journal: RunJournalWriter,
        run_id: str,
        sources: tuple[str, ...],
        progress: ProgressSink = None,
    ) -> SearchResults:
        """Re-dispatch only the named sources into an existing run journal."""
        _validate_request(request)
        params = _request_to_params(request)
        _emit(progress, f"run {run_id} retry started for {','.join(sources)}")

        eligible, skipped = _resolve_sources(request)

        for skip in skipped:
            self._record_skip(journal, skip)

        filter_plan = build_filter_plan(
            remote_only=request.remote_only,
            has_salary=request.has_salary,
            exclude_companies=",".join(request.exclude_companies) or None,
            experience=request.experience,
            exclude_keywords=",".join(request.exclude_keywords) or None,
            exclude_keywords_context=",".join(request.exclude_keywords_context) or None,
            location=request.location,
        )

        outcomes: list[SourceOutcome] = []
        all_listings: list[JobListing] = []
        errors: list[str] = []
        run_state = RunState.COMPLETED

        try:
            tasks = [
                asyncio.create_task(
                    self._run_one_source(entry, request, params, journal, progress),
                    name=f"engine:source:{entry.name}",
                )
                for entry in eligible
            ]
            total_timeout_s = max(0.001, request.total_timeout_ms / 1000.0)
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=total_timeout_s,
                )
            except TimeoutError:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                run_state = RunState.CANCELLED

            for entry, result in zip(eligible, results, strict=True):
                outcomes.append(self._coerce_outcome(entry, result, run_state))

            for outcome in outcomes:
                for listing in outcome.listings:
                    journal.write_listing(
                        source=outcome.status.source, listing=listing.to_dict()
                    )
                journal.write_source_status(outcome.status)
                if outcome.status.error_message and outcome.status.state != SourceState.OK:
                    errors.append(f"{outcome.status.source}: {outcome.status.error_message}")
                all_listings.extend(outcome.listings)

        except asyncio.CancelledError:
            run_state = RunState.CANCELLED
            snap = RunJournalReader(journal.run_dir).snapshot()
            materialized = materialize_listings(snap)
            journal.write_run_finished(
                state=run_state,
                final_listings_count=len(materialized),
                errors=errors,
            )
            self._rewrite_summary(journal)
            raise

        filtered = apply_filter_plan(all_listings, filter_plan)
        deduped = dedupe_listings(filtered) if request.dedupe else filtered
        final = deduped[: request.max_results]

        flag_enforcement = _build_flag_enforcement_summary(
            request=request, eligible=eligible, skipped=skipped, outcomes=outcomes,
        )
        result_sanity = self._build_result_sanity(
            request=request, outcomes=outcomes,
        )
        summary = {
            "source_statuses": [o.status.to_dict() for o in outcomes]
            + [self._skip_status_dict(s) for s in skipped],
            "flag_enforcement": flag_enforcement,
            "result_sanity": result_sanity,
            "filters": {
                "enabled": [item.name for item in filter_plan],
                "before": len(all_listings),
                "after": len(filtered),
                "removed": len(all_listings) - len(filtered),
            },
            "dedupe": {
                "enabled": request.dedupe,
                "before": len(filtered),
                "after": len(deduped),
                "removed": len(filtered) - len(deduped),
            },
            "max_results": {
                "requested": request.max_results,
                "returned": len(final),
            },
            "retry_sources": list(sources),
        }

        snap = RunJournalReader(journal.run_dir).snapshot()
        materialized = materialize_listings(snap)
        journal.write_run_finished(
            state=run_state,
            final_listings_count=len(materialized),
            errors=errors,
        )
        self._rewrite_summary(journal)

        return SearchResults(
            params=params,
            listings=final,
            errors=errors,
            summary=summary,
        )

    # ----- per-source dispatch ---------------------------------------------

    async def _run_one_source(
        self,
        entry: _SourceEntry,
        request: SearchRequest,
        params: SearchParams,
        journal: RunJournalWriter,
        progress: ProgressSink,
    ) -> SourceOutcome:
        """Build the scraper instance and hand it to the matching runner.

        HTTP sources go through HttpRunner (own executor, per-source
        deadline). Browser sources go through BrowserPool.run_with_page
        (async Playwright, semaphore-capped, anti-bot probe). When no
        browser_pool was injected, browser sources are SKIPPED with a
        clear note instead of silently dropped.
        """
        journal.write_source_started(
            source=entry.name,
            display_name=entry.display_name,
            transport=entry.transport.value,
            deadline_ms=request.source_timeout_ms,
        )
        _emit(progress, f"source {entry.name} started")

        if entry.transport == Transport.HTTP:
            scraper = self._scraper_factory(
                entry.name,
                request.max_results,
                request.source_timeout_ms,
                False,
            )
            return await self._http_runner.run_source(
                scraper, params, deadline_ms=request.source_timeout_ms,
            )

        # Browser source.
        if self._browser_pool is None:
            return SourceOutcome(
                status=SourceStatus(
                    source=entry.name,
                    display_name=entry.display_name,
                    transport=Transport.BROWSER,
                    state=SourceState.SKIPPED,
                    failure_mode=FailureMode.NOT_IN_PROFILE,
                    duration_ms=0,
                    flag_enforcement=entry.capabilities,
                    error_message="no browser_pool configured for this engine",
                ),
                listings=[],
            )
        return await self._run_browser_source(entry, request, params)

    async def _run_browser_source(
        self,
        entry: _SourceEntry,
        request: SearchRequest,
        params: SearchParams,
    ) -> SourceOutcome:
        """Dispatch one browser scraper through the BrowserPool.

        Translates pool outcomes into SourceStatus:
          • normal return     → OK
          • BlockedResult     → BLOCKED with the matched FailureMode
          • TimeoutError      → TIMEOUT / GOTO_TIMEOUT
          • PoolAcquireError  → TIMEOUT / POOL_ACQUIRE_TIMEOUT
          • Other exception   → ERROR / PARSE_ERROR
        """
        # Local imports to keep the engine module load-light. browser_pool
        # is only needed at dispatch time.
        from job_harness.browser_pool import BlockedResult, PoolAcquireTimeout
        from job_harness.types import BLOCK_REASON_TO_FAILURE_MODE

        scraper = self._scraper_factory(
            entry.name,
            request.max_results,
            request.source_timeout_ms,
            False,
        )

        async def _callable(page: Any) -> list[JobListing]:
            return await scraper.search_with_page(page, params)

        started_at = _monotonic()
        listings: list[JobListing] = []
        state = SourceState.OK
        failure_mode: FailureMode | None = None
        anti_bot_signal: str | None = None
        error_class: str | None = None
        error_message: str | None = None

        assert self._browser_pool is not None, "_run_browser_source requires browser_pool"
        try:
            result = await self._browser_pool.run_with_page(
                _callable, timeout_ms=request.source_timeout_ms,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            state, failure_mode = SourceState.TIMEOUT, FailureMode.GOTO_TIMEOUT
            error_class, error_message = type(exc).__name__, "browser page deadline exceeded"
        except PoolAcquireTimeout as exc:
            state, failure_mode = SourceState.TIMEOUT, FailureMode.POOL_ACQUIRE_TIMEOUT
            error_class, error_message = type(exc).__name__, str(exc)
        except Exception as exc:
            state, failure_mode = SourceState.ERROR, FailureMode.PARSE_ERROR
            error_class, error_message = type(exc).__name__, str(exc)
        else:
            if isinstance(result, BlockedResult):
                state = SourceState.BLOCKED
                failure_mode = BLOCK_REASON_TO_FAILURE_MODE[result.block.reason]
                anti_bot_signal = result.block.signal
                error_class = "BrowserBlocked"
                error_message = f"page tripped {result.block.reason.value}"
                # Discard partial listings parsed before the block was
                # detected — we cannot trust their integrity.
            elif isinstance(result, list):
                listings = result
            else:
                state, failure_mode = SourceState.ERROR, FailureMode.PARSE_ERROR
                error_class = "ProtocolError"
                error_message = (
                    f"scraper returned {type(result).__name__}, expected list[JobListing]"
                )

        duration_ms = int((_monotonic() - started_at) * 1000)
        status = SourceStatus(
            source=entry.name,
            display_name=entry.display_name,
            transport=Transport.BROWSER,
            state=state,
            failure_mode=failure_mode,
            duration_ms=duration_ms,
            raw_count=len(listings),
            flag_enforcement=entry.capabilities,
            anti_bot_signal=anti_bot_signal,
            error_class=error_class,
            error_message=error_message,
        )
        return SourceOutcome(status=status, listings=listings)

    def _coerce_outcome(
        self,
        entry: _SourceEntry,
        result: SourceOutcome | BaseException,
        run_state: RunState,
    ) -> SourceOutcome:
        """Map a gather() result into a SourceOutcome.

        gather(return_exceptions=True) hands us either the outcome or the
        raised exception (including CancelledError from total_timeout).
        """
        if isinstance(result, SourceOutcome):
            return result
        if isinstance(result, asyncio.CancelledError):
            failure_mode = (
                FailureMode.TOTAL_TIMEOUT if run_state == RunState.CANCELLED else FailureMode.USER_CANCELLED
            )
            return SourceOutcome(
                status=SourceStatus(
                    source=entry.name,
                    display_name=entry.display_name,
                    transport=entry.transport,
                    state=SourceState.CANCELLED,
                    failure_mode=failure_mode,
                    duration_ms=0,
                    flag_enforcement=entry.capabilities,
                    error_class="CancelledError",
                    error_message="cancelled before completion",
                ),
                listings=[],
            )
        # Generic unexpected exception — most likely a bug in the runner.
        return SourceOutcome(
            status=SourceStatus(
                source=entry.name,
                display_name=entry.display_name,
                transport=entry.transport,
                state=SourceState.ERROR,
                failure_mode=FailureMode.PARSE_ERROR,
                duration_ms=0,
                flag_enforcement=entry.capabilities,
                error_class=type(result).__name__,
                error_message=str(result),
            ),
            listings=[],
        )

    def _record_skip(self, journal: RunJournalWriter, skip: _SkipEntry) -> None:
        state = (
            SourceState.SKIPPED_UNSUPPORTED_FLAG
            if skip.failure_mode == FailureMode.UNSUPPORTED_FLAG
            else SourceState.SKIPPED
        )
        status = SourceStatus(
            source=skip.name,
            display_name=skip.display_name,
            transport=skip.transport,
            state=state,
            failure_mode=skip.failure_mode,
            duration_ms=0,
            flag_enforcement=skip.capabilities,
            error_message=skip.note,
        )
        journal.write_source_status(status)

    def _skip_status_dict(self, skip: _SkipEntry) -> dict[str, Any]:
        state = (
            SourceState.SKIPPED_UNSUPPORTED_FLAG
            if skip.failure_mode == FailureMode.UNSUPPORTED_FLAG
            else SourceState.SKIPPED
        )
        return SourceStatus(
            source=skip.name,
            display_name=skip.display_name,
            transport=skip.transport,
            state=state,
            failure_mode=skip.failure_mode,
            duration_ms=0,
            flag_enforcement=skip.capabilities,
            error_message=skip.note,
        ).to_dict()

    # ----- summary builders ------------------------------------------------

    def _rewrite_summary(self, journal: RunJournalWriter) -> None:
        snapshot = RunJournalReader(journal.run_dir).snapshot()
        journal.rewrite_summary(snapshot)

    def _build_result_sanity(
        self,
        *,
        request: SearchRequest,
        outcomes: list[SourceOutcome],
    ) -> dict[str, Any]:
        baseline = self._load_baseline()
        if not baseline:
            return {}
        view: dict[str, Any] = {}
        query_tokens = _tokenise(request.query)
        country = (request.country or "").upper() or None
        for outcome in outcomes:
            if outcome.status.state != SourceState.OK:
                continue
            min_count = _baseline_min(
                baseline, outcome.status.source, query_tokens, country,
            )
            if min_count is None:
                continue
            verdict = "plausible" if outcome.status.raw_count >= min_count else "suspicious"
            view[outcome.status.source] = {
                "raw_count": outcome.status.raw_count,
                "baseline_min": min_count,
                "verdict": verdict,
            }
            if verdict == "suspicious":
                view[outcome.status.source]["note"] = (
                    f"{outcome.status.raw_count} results for a known query — "
                    "possible parser regression"
                )
        return view

    def _load_baseline(self) -> dict[str, Any] | None:
        if self._sanity_baselines_path is None:
            return None
        try:
            return json.loads(self._sanity_baselines_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(progress: ProgressSink, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:
            pass


def _validate_request(request: SearchRequest) -> None:
    if not request.query.strip():
        raise ValueError("query is required")
    if request.max_results < 1:
        raise ValueError("max_results must be >= 1")
    if request.source_timeout_ms < 1:
        raise ValueError("source_timeout_ms must be >= 1")
    if request.total_timeout_ms < 1:
        raise ValueError("total_timeout_ms must be >= 1")
    if request.profile not in (None, "fast", "full"):
        raise ValueError("profile must be one of None, 'fast', 'full'")


def _request_to_params(request: SearchRequest) -> SearchParams:
    return SearchParams(
        query=request.query,
        country=request.country,
        remote_only=request.remote_only,
        experience=request.experience,
        location=request.location,
        max_results=request.max_results,
        extra=dict(request.extra),
    )


def _default_scraper_factory(
    name: str, max_results: int, timeout_ms: int, debug: bool
) -> Any:
    """Construct a scraper instance for engine dispatch.

    HTTP scrapers keep the legacy `(context, max_results, ...)` ctor;
    we pass None for the context they don't use.

    Browser scrapers extend BaseBrowserScraper, whose `__init__` accepts
    only `(max_results, debug, timeout_ms)` because the engine hands
    them a Page at search time.
    """
    cls = registry.get_scraper_class(name)
    if cls.requires_browser:
        # Browser scrapers don't take a context — engine hands them a Page.
        return cls(max_results=max_results, timeout_ms=timeout_ms, debug=debug)  # type: ignore[call-arg]
    return cls(context=None, max_results=max_results, timeout_ms=timeout_ms, debug=debug)


def _build_flag_enforcement_summary(
    *,
    request: SearchRequest,
    eligible: list[_SourceEntry],
    skipped: list[_SkipEntry],
    outcomes: list[SourceOutcome],
) -> dict[str, Any]:
    """Build the result-level `flag_enforcement` block.

    For each user-requested flag, reports per source whether the support
    is server/client/best_effort/unsupported, and whether the engine
    actually applied it for this run.
    """
    requested = _requested_flags(request)
    if not requested:
        return {}
    by_outcome = {o.status.source: o for o in outcomes}
    block: dict[str, Any] = {}
    for flag in requested:
        by_source: dict[str, Any] = {}
        for entry in eligible:
            support = entry.capabilities.get(flag, FilterSupport.UNSUPPORTED)
            applied = support != FilterSupport.UNSUPPORTED and entry.name in by_outcome
            by_source[entry.name] = {"support": support.value, "applied": applied}
        for skip in skipped:
            if skip.failure_mode != FailureMode.UNSUPPORTED_FLAG:
                continue
            by_source[skip.name] = {
                "support": skip.capabilities.get(flag, FilterSupport.UNSUPPORTED).value,
                "applied": False,
                "action": "skipped",
            }
        block[flag] = {
            "requested": True,
            "policy": "strict" if request.strict_flags else "lenient",
            "by_source": by_source,
        }
    return block


def _tokenise(query: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in (q.casefold() for q in query.split())
        if len(token) > 1
    )


def _baseline_min(
    baseline: dict[str, Any],
    source: str,
    query_tokens: tuple[str, ...],
    country: str | None,
) -> int | None:
    """Look up the baseline minimum for the (source, token, country) triple.

    File shape:
        {"hh_ru": {"RU": {"python": 10, "qa": 5}}, ...}
    Falls back to country="*" if the specific country is missing.
    """
    source_block = baseline.get(source)
    if not isinstance(source_block, dict):
        return None
    candidates: list[Any] = []
    if country and country in source_block:
        candidates.append(source_block[country])
    if "*" in source_block:
        candidates.append(source_block["*"])
    for block in candidates:
        if not isinstance(block, dict):
            continue
        for token in query_tokens:
            value = block.get(token)
            if isinstance(value, int) and value > 0:
                return value
    return None


