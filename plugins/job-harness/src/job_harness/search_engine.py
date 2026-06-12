"""SearchEngine — strict raw search layer plus downstream result pipeline."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any

import job_harness.registry as registry
from job_harness.experience_engine import parse_experience_levels
from job_harness.http_runner import HttpRunner, SourceOutcome
from job_harness.models import RawListing, RawSearchRecord, SearchParams, SearchResults
from job_harness.result_pipeline import raw_listings_from_dicts, run_result_pipeline
from job_harness.run_journal import RunJournalReader, RunJournalWriter
from job_harness.source_runtime import (
    SOURCE_LEVEL_RETRYABLE_FAILURES,
    SourceRuntimeConfig,
)
from job_harness.types import (
    FailureMode,
    RunState,
    SearchCriterion,
    SearchRequest,
    SourceDescriptor,
    SourceState,
    SourceStatus,
    Transport,
    utc_now_iso,
)

ProgressSink = Callable[[str], None] | None
ScraperFactory = Callable[[str, int, int, bool, int], Any]


@dataclass(frozen=True)
class _SourceEntry:
    name: str
    display_name: str
    transport: Transport
    descriptor: SourceDescriptor


@dataclass(frozen=True)
class _SkipEntry:
    name: str
    descriptor: SourceDescriptor
    failure_mode: FailureMode
    note: str


class SearchEngine:
    """Runs selected sources and writes raw listings before post-processing."""

    def __init__(
        self,
        *,
        http_runner: HttpRunner | None = None,
        browser_pool: Any | None = None,
        scraper_factory: ScraperFactory | None = None,
        runtime_config: SourceRuntimeConfig | None = None,
        sanity_baselines_path: Path | None = None,
    ) -> None:
        self._http_runner = http_runner or HttpRunner()
        self._browser_pool = browser_pool
        self._scraper_factory = scraper_factory or _default_scraper_factory
        self._runtime_config = runtime_config or SourceRuntimeConfig()
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
        return await self._execute_impl(
            request,
            journal=journal,
            run_id=run_id,
            progress=progress,
            write_started=True,
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
        retry_request = replace(request, sources=sources, source_groups=())
        _emit(progress, f"run {run_id} retry started for {','.join(sources)}")
        return await self._execute_impl(
            retry_request,
            journal=journal,
            run_id=run_id,
            progress=progress,
            write_started=False,
        )

    async def _execute_impl(
        self,
        request: SearchRequest,
        *,
        journal: RunJournalWriter,
        run_id: str,
        progress: ProgressSink,
        write_started: bool,
    ) -> SearchResults:
        _validate_request(request)
        params = _request_to_params(request)
        if write_started:
            journal.write_run_started(run_id=run_id, request=request)
        _emit(progress, f"run {run_id} started")

        eligible, skipped = _resolve_sources(request)
        for skip in skipped:
            journal.write_source_status(_status_for_skip(skip, request, self._runtime_config))

        outcomes: list[SourceOutcome] = []
        errors: list[str] = []
        run_state = RunState.COMPLETED
        total_timeout_s = max(0.001, self._runtime_config.total_run_timeout_ms / 1000.0)

        try:
            tasks = [
                asyncio.create_task(
                    self._run_one_source(entry, request, params, progress),
                    name=f"engine:source:{entry.name}",
                )
                for entry in eligible
            ]
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=total_timeout_s,
                )
            except TimeoutError:
                run_state = RunState.CANCELLED
                for task in tasks:
                    if not task.done():
                        task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)

            for entry, result in zip(eligible, results, strict=True):
                outcomes.append(
                    self._coerce_outcome(
                        entry,
                        result,
                        request,
                        run_state,
                    )
                )

            for outcome in outcomes:
                listings = outcome.listings[: outcome.status.source_limit]
                for listing in listings:
                    journal.write_raw_listing(
                        RawSearchRecord(
                            schema_version=1,
                            type="raw_listing",
                            run_id=run_id,
                            source=outcome.status.source,
                            collected_at=utc_now_iso(),
                            listing=listing,
                        )
                    )
                status = (
                    replace(
                        outcome.status,
                        listings_written=len(listings),
                        limit_reached=len(listings) >= outcome.status.source_limit,
                    )
                    if outcome.status.state == SourceState.OK
                    else replace(outcome.status, listings_written=len(listings))
                )
                journal.write_source_status(status)
                if status.error and status.state != SourceState.OK:
                    errors.append(f"{status.source}: {status.error}")

        except asyncio.CancelledError:
            run_state = RunState.CANCELLED
            snap = RunJournalReader(journal.run_dir).snapshot()
            journal.write_run_finished(
                state=run_state,
                final_listings_count=len(snap.listings),
                errors=errors,
            )
            self._rewrite_summary(journal)
            raise

        snap = RunJournalReader(journal.run_dir).snapshot()
        raw_listings = raw_listings_from_dicts(snap.listings)
        pipeline_result = run_result_pipeline(
            raw_listings=raw_listings,
            request=request.to_dict(),
            sources=snap.sources,
        )
        result_sanity = self._build_result_sanity(request=request, sources=snap.sources)
        summary = {
            "source_statuses": [status.to_dict() for status in snap.sources.values()],
            "result_sanity": result_sanity,
            "raw_search": {
                "path": str(journal.raw_search_path.resolve()),
                "listings_written": len(raw_listings),
                "global_truncation": False,
            },
            "results": {
                "path": str((journal.run_dir / "results.json").resolve()),
            },
        }
        summary.update(pipeline_result.summary)

        journal.write_run_finished(
            state=run_state,
            final_listings_count=len(pipeline_result.listings),
            errors=errors,
        )
        self._rewrite_summary(journal)

        return SearchResults(
            params=params,
            listings=pipeline_result.listings,
            errors=errors,
            summary=summary,
        )

    async def _run_one_source(
        self,
        entry: _SourceEntry,
        request: SearchRequest,
        params: SearchParams,
        progress: ProgressSink,
    ) -> SourceOutcome:
        attempts = 0
        retries = 0
        last_outcome: SourceOutcome | None = None

        while attempts < self._runtime_config.source_max_attempts:
            attempts += 1
            _emit(progress, f"source {entry.name} attempt {attempts} started")
            outcome = await self._run_source_attempt(entry, params)
            outcome = self._with_source_summary(
                outcome,
                entry=entry,
                request=request,
                attempts=attempts,
                retries=retries,
            )
            last_outcome = outcome
            if not self._should_retry(outcome, attempts):
                return outcome

            retries += 1
            backoff_ms = self._runtime_config.retry_backoff_ms(
                retries,
                self._runtime_config.total_run_timeout_ms,
            )
            if backoff_ms <= 0:
                return outcome
            await asyncio.sleep(backoff_ms / 1000)

        assert last_outcome is not None
        return last_outcome

    async def _run_source_attempt(
        self,
        entry: _SourceEntry,
        params: SearchParams,
    ) -> SourceOutcome:
        timeout_ms = self._runtime_config.source_attempt_timeout_ms
        if entry.transport == Transport.HTTP:
            scraper = self._scraper_factory(
                entry.name,
                entry.descriptor.source_limit,
                timeout_ms,
                False,
                self._runtime_config.company_probe_timeout_ms,
            )
            return await self._http_runner.run_source(scraper, params, deadline_ms=timeout_ms)

        if self._browser_pool is None:
            return SourceOutcome(
                status=SourceStatus(
                    source=entry.name,
                    group=entry.descriptor.group,
                    state=SourceState.SKIPPED,
                    failure_mode=FailureMode.NOT_IN_PROFILE,
                    source_limit=entry.descriptor.source_limit,
                    deadline_ms=timeout_ms,
                    elapsed_ms=0,
                    supported_server_criteria=tuple(entry.descriptor.server_criteria),
                    attempts=0,
                    error="no browser_pool configured for this engine",
                ),
                listings=[],
            )
        return await self._run_browser_source(entry, params, timeout_ms)

    async def _run_browser_source(
        self,
        entry: _SourceEntry,
        params: SearchParams,
        timeout_ms: int,
    ) -> SourceOutcome:
        from job_harness.browser_pool import BlockedResult, PoolAcquireTimeout
        from job_harness.types import BLOCK_REASON_TO_FAILURE_MODE

        scraper = self._scraper_factory(
            entry.name,
            entry.descriptor.source_limit,
            timeout_ms,
            False,
            self._runtime_config.company_probe_timeout_ms,
        )

        async def _callable(page: Any) -> list[RawListing]:
            return await scraper.search_with_page(page, params)

        started_at = _monotonic()
        listings: list[RawListing] = []
        state = SourceState.OK
        failure_mode: FailureMode | None = None
        error: str | None = None

        assert self._browser_pool is not None
        try:
            result = await self._browser_pool.run_with_page(_callable, timeout_ms=timeout_ms)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            state, failure_mode = SourceState.TIMEOUT, FailureMode.GOTO_TIMEOUT
            error = "browser page deadline exceeded"
        except PoolAcquireTimeout as exc:
            state, failure_mode = SourceState.TIMEOUT, FailureMode.POOL_ACQUIRE_TIMEOUT
            error = str(exc)
        except Exception as exc:
            state, failure_mode = SourceState.ERROR, FailureMode.PARSE_ERROR
            error = str(exc)
        else:
            if isinstance(result, BlockedResult):
                state = SourceState.BLOCKED
                failure_mode = BLOCK_REASON_TO_FAILURE_MODE[result.block.reason]
                error = f"page tripped {result.block.reason.value}: {result.block.signal}"
            elif isinstance(result, list):
                listings = result
                if getattr(scraper, "timed_out", False):
                    if listings:
                        state = SourceState.PARTIAL
                        failure_mode = FailureMode.SLOW_PAGINATION
                    else:
                        state = SourceState.TIMEOUT
                        failure_mode = FailureMode.GOTO_TIMEOUT
                        error = "browser source deadline reached before completion"
            else:
                state, failure_mode = SourceState.ERROR, FailureMode.PARSE_ERROR
                error = f"scraper returned {type(result).__name__}, expected list[RawListing]"

        elapsed_ms = int((_monotonic() - started_at) * 1000)
        return SourceOutcome(
            status=SourceStatus(
                source=entry.name,
                group=entry.descriptor.group,
                state=state,
                failure_mode=failure_mode,
                source_limit=entry.descriptor.source_limit,
                deadline_ms=timeout_ms,
                elapsed_ms=elapsed_ms,
                supported_server_criteria=tuple(entry.descriptor.server_criteria),
                listings_written=len(listings),
                error=error,
            ),
            listings=listings,
        )

    def _coerce_outcome(
        self,
        entry: _SourceEntry,
        result: SourceOutcome | BaseException,
        request: SearchRequest,
        run_state: RunState,
    ) -> SourceOutcome:
        if isinstance(result, SourceOutcome):
            return self._with_source_summary(
                result,
                entry=entry,
                request=request,
                attempts=result.status.attempts,
                retries=result.status.retries,
            )
        if isinstance(result, asyncio.CancelledError):
            failure_mode = (
                FailureMode.TOTAL_TIMEOUT
                if run_state == RunState.CANCELLED
                else FailureMode.USER_CANCELLED
            )
            status = SourceStatus(
                source=entry.name,
                group=entry.descriptor.group,
                state=SourceState.CANCELLED,
                failure_mode=failure_mode,
                source_limit=entry.descriptor.source_limit,
                deadline_ms=self._runtime_config.source_attempt_timeout_ms,
                elapsed_ms=0,
                attempts=0,
                error="cancelled before completion",
            )
            return self._with_source_summary(
                SourceOutcome(status=status, listings=[]),
                entry=entry,
                request=request,
                attempts=0,
                retries=0,
            )
        status = SourceStatus(
            source=entry.name,
            group=entry.descriptor.group,
            state=SourceState.ERROR,
            failure_mode=FailureMode.PARSE_ERROR,
            source_limit=entry.descriptor.source_limit,
            deadline_ms=self._runtime_config.source_attempt_timeout_ms,
            elapsed_ms=0,
            attempts=1,
            error=str(result),
        )
        return self._with_source_summary(
            SourceOutcome(status=status, listings=[]),
            entry=entry,
            request=request,
            attempts=1,
            retries=0,
        )

    def _with_source_summary(
        self,
        outcome: SourceOutcome,
        *,
        entry: _SourceEntry,
        request: SearchRequest,
        attempts: int,
        retries: int,
    ) -> SourceOutcome:
        requested = _requested_criteria(request)
        supported = tuple(sorted(entry.descriptor.server_criteria, key=lambda item: item.value))
        used = tuple(item for item in supported if item in requested)
        unsupported = tuple(item for item in requested if item not in entry.descriptor.server_criteria)
        status = replace(
            outcome.status,
            group=entry.descriptor.group,
            source_limit=entry.descriptor.source_limit,
            requested_criteria=_criteria_request_dict(request),
            supported_server_criteria=supported,
            server_criteria_used=used,
            unsupported_requested_criteria=unsupported,
            attempts=attempts,
            retries=retries,
            listings_written=len(outcome.listings),
            limit_reached=(
                outcome.status.state == SourceState.OK
                and len(outcome.listings) >= entry.descriptor.source_limit
            ),
        )
        return SourceOutcome(status=status, listings=outcome.listings)

    def _should_retry(self, outcome: SourceOutcome, attempts: int) -> bool:
        status = outcome.status
        return (
            not outcome.listings
            and attempts < self._runtime_config.source_max_attempts
            and status.failure_mode in SOURCE_LEVEL_RETRYABLE_FAILURES
        )

    def _rewrite_summary(self, journal: RunJournalWriter) -> None:
        snapshot = RunJournalReader(journal.run_dir).snapshot()
        journal.rewrite_summary(snapshot)

    def _build_result_sanity(
        self,
        *,
        request: SearchRequest,
        sources: dict[str, SourceStatus],
    ) -> dict[str, Any]:
        baseline = self._load_baseline()
        if not baseline:
            return {}
        view: dict[str, Any] = {}
        query_tokens = _tokenise(request.query)
        country = (request.country or "").upper() or None
        for source, status in sources.items():
            if status.state != SourceState.OK:
                continue
            min_count = _baseline_min(baseline, source, query_tokens, country)
            if min_count is None:
                continue
            verdict = "plausible" if status.listings_written >= min_count else "suspicious"
            view[source] = {
                "raw_count": status.listings_written,
                "baseline_min": min_count,
                "verdict": verdict,
            }
            if verdict == "suspicious":
                view[source]["note"] = (
                    f"{status.listings_written} results for a known query - "
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


def _resolve_sources(request: SearchRequest) -> tuple[list[_SourceEntry], list[_SkipEntry]]:
    candidate_names = _candidate_source_names(request)
    eligible: list[_SourceEntry] = []
    skipped: list[_SkipEntry] = []
    for name in candidate_names:
        cls = registry.get_scraper_class(name)
        descriptor = registry.get_source_descriptor(name)
        if not cls.supports_country(request.country):
            skipped.append(
                _SkipEntry(
                    name=name,
                    descriptor=descriptor,
                    failure_mode=FailureMode.NOT_IN_COUNTRY,
                    note=f"scraper does not support country={request.country!r}",
                )
            )
            continue
        if request.profile == "fast" and cls.transport() == Transport.BROWSER:
            skipped.append(
                _SkipEntry(
                    name=name,
                    descriptor=descriptor,
                    failure_mode=FailureMode.NOT_IN_PROFILE,
                    note="browser scraper skipped under profile=fast",
                )
            )
            continue
        eligible.append(
            _SourceEntry(
                name=name,
                display_name=cls.display_name,
                transport=cls.transport(),
                descriptor=descriptor,
            )
        )
    return eligible, skipped


def _candidate_source_names(request: SearchRequest) -> list[str]:
    all_names = [name for name, _ in registry.iter_registered()]
    if request.sources is None or list(request.sources) == ["all"]:
        source_names: list[str] = [] if request.source_groups else list(all_names)
    else:
        source_names = list(request.sources)
        unknown = [name for name in source_names if name not in all_names]
        if unknown:
            raise ValueError(f"unknown sources: {', '.join(unknown)}")

    selected = set(source_names)
    if request.source_groups:
        wanted = set(request.source_groups)
        for name in all_names:
            if registry.get_source_descriptor(name).group in wanted:
                selected.add(name)

    return [name for name in all_names if name in selected]


def _status_for_skip(
    skip: _SkipEntry,
    request: SearchRequest,
    runtime_config: SourceRuntimeConfig,
) -> SourceStatus:
    state = (
        SourceState.SKIPPED_UNSUPPORTED_FLAG
        if skip.failure_mode == FailureMode.UNSUPPORTED_FLAG
        else SourceState.SKIPPED
    )
    requested = _requested_criteria(request)
    supported = tuple(sorted(skip.descriptor.server_criteria, key=lambda item: item.value))
    return SourceStatus(
        source=skip.name,
        group=skip.descriptor.group,
        state=state,
        failure_mode=skip.failure_mode,
        source_limit=skip.descriptor.source_limit,
        deadline_ms=runtime_config.source_attempt_timeout_ms,
        elapsed_ms=0,
        requested_criteria=_criteria_request_dict(request),
        supported_server_criteria=supported,
        server_criteria_used=(),
        unsupported_requested_criteria=tuple(
            item for item in requested if item not in skip.descriptor.server_criteria
        ),
        attempts=0,
        retries=0,
        error=skip.note,
    )


def _validate_request(request: SearchRequest) -> None:
    if not request.query.strip():
        raise ValueError("query is required")
    if request.max_results < 1:
        raise ValueError("max_results must be >= 1")
    if request.salary_from is not None and request.salary_from < 1:
        raise ValueError("salary_from must be >= 1")
    if request.freshness_days is not None and request.freshness_days < 1:
        raise ValueError("freshness_days must be >= 1")
    if request.profile not in (None, "fast", "full"):
        raise ValueError("profile must be one of None, 'fast', 'full'")
    parse_experience_levels(request.experience_levels, allow_empty=True)


def _request_to_params(request: SearchRequest) -> SearchParams:
    return SearchParams(
        query=request.query,
        country=request.country,
        remote_only=request.remote_only,
        experience_levels=request.experience_levels,
        location=request.location,
        salary_from=request.salary_from,
        freshness_days=request.freshness_days,
        max_results=request.max_results,
        extra=dict(request.extra),
    )


def _criteria_request_dict(request: SearchRequest) -> dict[str, Any]:
    return {
        "query": request.query,
        "country": request.country,
        "remote_only": request.remote_only,
        "experience_levels": list(request.experience_levels),
        "location": request.location,
        "salary_from": request.salary_from,
        "freshness_days": request.freshness_days,
    }


def _requested_criteria(request: SearchRequest) -> tuple[SearchCriterion, ...]:
    criteria = [SearchCriterion.QUERY]
    if request.country:
        criteria.append(SearchCriterion.COUNTRY)
    if request.remote_only:
        criteria.append(SearchCriterion.REMOTE_ONLY)
    if request.experience_levels:
        criteria.append(SearchCriterion.EXPERIENCE_LEVELS)
    if request.location:
        criteria.append(SearchCriterion.LOCATION)
    if request.salary_from is not None:
        criteria.append(SearchCriterion.SALARY_FROM)
    if request.freshness_days is not None:
        criteria.append(SearchCriterion.FRESHNESS)
    return tuple(criteria)


def _default_scraper_factory(
    name: str,
    source_limit: int,
    timeout_ms: int,
    debug: bool,
    company_probe_timeout_ms: int,
) -> Any:
    cls: Any = registry.get_scraper_class(name)
    if cls.requires_browser:
        return cls(
            max_results=source_limit,
            timeout_ms=timeout_ms,
            debug=debug,
            company_probe_timeout_ms=company_probe_timeout_ms,
        )
    return cls(
        context=None,
        max_results=source_limit,
        timeout_ms=timeout_ms,
        debug=debug,
        company_probe_timeout_ms=company_probe_timeout_ms,
    )


def _emit(progress: ProgressSink, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:
            pass


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
