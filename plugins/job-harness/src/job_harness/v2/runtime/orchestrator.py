"""Async source orchestrator built on the strict contracts."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic

from job_harness.v2.contracts import (
    AttemptCounts,
    AttemptEvidence,
    CriteriaDiagnostics,
    DescriptionAvailability,
    RawListing,
    RawSearchRecord,
    RetryInfo,
    RetryNextAction,
    SearchRequest,
    SourceAttemptRecord,
    SourceDescriptor,
    SourceFetchRequest,
    SourceOutcome,
    SourceScraper,
    SourceSearchParseResult,
)
from job_harness.v2.ports import ArtifactFetcher, CorpusWriter
from job_harness.v2.runtime.catalog import SourceCatalog
from job_harness.v2.runtime.errors import ClassifiedSourceError
from job_harness.v2.runtime.retry import RetryPolicy


@dataclass(frozen=True)
class OrchestratorConfig:
    source_attempt_timeout_seconds: float = 180.0
    run_timeout_seconds: float = 360.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if self.source_attempt_timeout_seconds <= 0:
            raise ValueError("source_attempt_timeout_seconds must be > 0")
        if self.run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be > 0")


@dataclass(frozen=True)
class SearchRunResult:
    run_id: str
    append_sequence: int
    attempts: tuple[SourceAttemptRecord, ...]

    @property
    def raw_records_written(self) -> int:
        return sum(attempt.counts.raw_listings_written for attempt in self.attempts)


@dataclass(frozen=True)
class _SearchJob:
    scraper: SourceScraper
    request: SourceFetchRequest


@dataclass(frozen=True)
class _CollectedListing:
    listing: RawListing
    description_availability: DescriptionAvailability
    detail_fetched: bool
    detail_parse_error: str | None = None


@dataclass(frozen=True)
class _ParsedAttempt:
    outcome: SourceOutcome
    listings: tuple[_CollectedListing, ...]
    evidence: AttemptEvidence
    pages_visited: int


class SearchOrchestrator:
    """Runs source jobs independently and writes raw evidence before summaries."""

    def __init__(
        self,
        *,
        catalog: SourceCatalog,
        fetcher: ArtifactFetcher,
        writer: CorpusWriter,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self._catalog = catalog
        self._fetcher = fetcher
        self._writer = writer
        self._config = config or OrchestratorConfig()

    async def run(
        self,
        request: SearchRequest,
        *,
        run_id: str | None = None,
        append_sequence: int = 0,
    ) -> SearchRunResult:
        if append_sequence < 0:
            raise ValueError("append_sequence must be >= 0")
        effective_run_id = _resolve_run_id(request, run_id)
        jobs = self._build_jobs(request)
        tasks = {
            asyncio.create_task(
                self._run_job(
                    job,
                    search_request=request,
                    run_id=effective_run_id,
                    append_sequence=append_sequence,
                )
            ): job
            for job in jobs
        }
        if not tasks:
            result = SearchRunResult(
                run_id=effective_run_id,
                append_sequence=append_sequence,
                attempts=(),
            )
            self._write_run_manifest(result)
            return result

        done, pending = await asyncio.wait(
            tasks,
            timeout=self._config.run_timeout_seconds,
        )
        attempts = [task.result() for task in done]
        attempts.extend(await self._cancel_pending_as_run_timeout(pending, tasks, request))
        attempts.sort(key=lambda attempt: (attempt.source, attempt.query_variant, attempt.attempt))

        result = SearchRunResult(
            run_id=effective_run_id,
            append_sequence=append_sequence,
            attempts=tuple(attempts),
        )
        self._write_run_manifest(result)
        return result

    def _write_run_manifest(self, result: SearchRunResult) -> None:
        self._writer.replace_run_manifest(
            {
                "schema_version": 1,
                "record_type": "run_manifest",
                "run_id": result.run_id,
                "latest_append_sequence": result.append_sequence,
                "raw_records_written_this_call": result.raw_records_written,
                "source_attempts": [
                    {
                        "source": attempt.source,
                        "query_variant": attempt.query_variant,
                        "attempt": attempt.attempt,
                        "outcome": attempt.outcome,
                        "raw_listings_written": attempt.counts.raw_listings_written,
                    }
                    for attempt in result.attempts
                ],
            }
        )

    def _build_jobs(self, request: SearchRequest) -> tuple[_SearchJob, ...]:
        jobs: list[_SearchJob] = []
        for scraper in self._catalog.select(request):
            fetch_requests = scraper.build_search_requests(request)
            if not fetch_requests:
                raise ValueError(f"{scraper.descriptor.source_id} returned no fetch requests")
            jobs.extend(_SearchJob(scraper=scraper, request=fetch_request) for fetch_request in fetch_requests)
        return tuple(jobs)

    async def _run_job(
        self,
        job: _SearchJob,
        *,
        search_request: SearchRequest,
        run_id: str,
        append_sequence: int,
    ) -> SourceAttemptRecord:
        policy = self._config.retry_policy
        for attempt in range(1, policy.max_attempts + 1):
            record = await self._run_attempt(
                job,
                search_request=search_request,
                run_id=run_id,
                append_sequence=append_sequence,
                attempt=attempt,
            )
            next_action = policy.next_action(
                outcome=record.outcome,
                attempt=attempt,
                raw_listings_written=record.counts.raw_listings_written,
            )
            record = _with_retry(record, next_action=next_action, max_attempts=policy.max_attempts)
            self._writer.append_attempt_record(record)
            if next_action != RetryNextAction.RETRY:
                return record
            if policy.backoff_seconds:
                await asyncio.sleep(policy.backoff_seconds)
        raise RuntimeError("retry loop exhausted without returning a source attempt record")

    async def _run_attempt(
        self,
        job: _SearchJob,
        *,
        search_request: SearchRequest,
        run_id: str,
        append_sequence: int,
        attempt: int,
    ) -> SourceAttemptRecord:
        started_at = datetime.now(UTC)
        started = monotonic()
        outcome = SourceOutcome.PARSE_ERROR
        evidence = AttemptEvidence(error="attempt did not complete")
        raw_written = 0
        pages_visited = 0

        try:
            parsed = await asyncio.wait_for(
                self._fetch_search_pages(job),
                timeout=self._config.source_attempt_timeout_seconds,
            )
            listings = self._validated_listings(job, parsed.listings)
            raw_written = self._write_raw_records(
                listings,
                run_id=run_id,
                append_sequence=append_sequence,
                job=job,
            )
            pages_visited = parsed.pages_visited
            outcome = parsed.outcome
            evidence = parsed.evidence
        except TimeoutError:
            outcome = SourceOutcome.SOURCE_TIMEOUT
            evidence = AttemptEvidence(error="source attempt deadline expired")
        except ClassifiedSourceError as exc:
            outcome = exc.outcome
            evidence = exc.evidence
        except ValueError as exc:
            outcome = SourceOutcome.INVALID_SOURCE_OUTPUT
            evidence = AttemptEvidence(error=str(exc))
        except Exception as exc:
            outcome = SourceOutcome.PARSE_ERROR
            evidence = AttemptEvidence(error=str(exc))

        finished_at = datetime.now(UTC)
        elapsed_ms = int((monotonic() - started) * 1000)
        descriptor = job.scraper.descriptor
        return SourceAttemptRecord(
            source=descriptor.source_id,
            source_type=descriptor.source_type,
            query_variant=job.request.query_variant,
            attempt=attempt,
            outcome=outcome,
            started_at=started_at,
            finished_at=finished_at,
            elapsed_ms=elapsed_ms,
            source_limit=descriptor.source_limit,
            limit_reached=outcome == SourceOutcome.SUCCESS and raw_written >= descriptor.source_limit,
            counts=AttemptCounts(raw_listings_written=raw_written, pages_visited=pages_visited),
            criteria=_criteria_diagnostics(descriptor=descriptor, request=search_request),
            retry=RetryInfo(
                attempts=attempt,
                max_attempts=self._config.retry_policy.max_attempts,
                next_action=RetryNextAction.NONE,
            ),
            evidence=evidence,
        )

    async def _fetch_search_pages(self, job: _SearchJob) -> _ParsedAttempt:
        descriptor = job.scraper.descriptor
        listings: list[_CollectedListing] = []
        current_request: SourceFetchRequest | None = job.request
        pages_visited = 0
        last_evidence = AttemptEvidence()

        while current_request is not None and len(listings) < descriptor.source_limit:
            response = await self._fetcher.fetch(current_request)
            if response.source_id != descriptor.source_id:
                raise ValueError("response.source_id must match scraper descriptor")

            result = job.scraper.parse_search_response(response, current_request)
            if not isinstance(result, SourceSearchParseResult):
                raise ValueError("parse_search_response must return SourceSearchParseResult")

            pages_visited += 1
            last_evidence = result.evidence

            if result.outcome == SourceOutcome.NO_RESULTS:
                if listings:
                    raise ValueError("no_results page after collected listings is invalid")
                return _ParsedAttempt(
                    outcome=SourceOutcome.NO_RESULTS,
                    listings=(),
                    evidence=result.evidence,
                    pages_visited=pages_visited,
                )

            remaining = descriptor.source_limit - len(listings)
            page_listings = result.listings[:remaining]
            listings.extend(
                _CollectedListing(
                    listing=listing,
                    description_availability=(
                        DescriptionAvailability.PRESENT
                        if listing.description
                        else DescriptionAvailability.NOT_REQUESTED
                    ),
                    detail_fetched=False,
                )
                for listing in page_listings
            )
            current_request = result.next_request

        if not listings:
            raise ValueError("source produced neither listings nor explicit no_results")

        return _ParsedAttempt(
            outcome=SourceOutcome.SUCCESS,
            listings=tuple(listings),
            evidence=last_evidence,
            pages_visited=pages_visited,
        )

    def _validated_listings(
        self,
        job: _SearchJob,
        listings: tuple[_CollectedListing, ...],
    ) -> tuple[_CollectedListing, ...]:
        descriptor = job.scraper.descriptor
        for collected in listings:
            listing = collected.listing
            if not isinstance(listing, RawListing):
                raise ValueError("source returned a non-RawListing item")
            if listing.source != descriptor.source_id:
                raise ValueError("listing.source must match source descriptor")
        return listings

    def _write_raw_records(
        self,
        listings: tuple[_CollectedListing, ...],
        *,
        run_id: str,
        append_sequence: int,
        job: _SearchJob,
    ) -> int:
        descriptor = job.scraper.descriptor
        for collected in listings:
            listing = collected.listing
            self._writer.append_raw_record(
                RawSearchRecord(
                    run_id=run_id,
                    append_sequence=append_sequence,
                    query_variant=job.request.query_variant,
                    source=descriptor.source_id,
                    source_type=descriptor.source_type,
                    collected_at=datetime.now(UTC),
                    listing=listing,
                    description_availability=collected.description_availability,
                    detail_fetched=collected.detail_fetched,
                    detail_parse_error=collected.detail_parse_error,
                    source_url=job.request.url,
                )
            )
        return len(listings)

    async def _cancel_pending_as_run_timeout(
        self,
        pending: set[asyncio.Task[SourceAttemptRecord]],
        tasks: dict[asyncio.Task[SourceAttemptRecord], _SearchJob],
        request: SearchRequest,
    ) -> list[SourceAttemptRecord]:
        attempts: list[SourceAttemptRecord] = []
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in pending:
            job = tasks[task]
            now = datetime.now(UTC)
            descriptor = job.scraper.descriptor
            record = SourceAttemptRecord(
                source=descriptor.source_id,
                source_type=descriptor.source_type,
                query_variant=job.request.query_variant,
                attempt=1,
                outcome=SourceOutcome.RUN_TIMEOUT,
                started_at=now,
                finished_at=now,
                elapsed_ms=0,
                source_limit=descriptor.source_limit,
                limit_reached=False,
                counts=AttemptCounts(raw_listings_written=0, pages_visited=0),
                criteria=_criteria_diagnostics(descriptor=descriptor, request=request),
                retry=RetryInfo(
                    attempts=1,
                    max_attempts=self._config.retry_policy.max_attempts,
                    next_action=RetryNextAction.NONE,
                ),
                evidence=AttemptEvidence(error="run deadline expired"),
            )
            self._writer.append_attempt_record(record)
            attempts.append(record)
        return attempts


def _criteria_diagnostics(
    *,
    descriptor: SourceDescriptor,
    request: SearchRequest,
) -> CriteriaDiagnostics:
    native = descriptor.native_request_criteria & request.requested_criteria
    structured = descriptor.structured_output_criteria & request.requested_criteria
    unsupported = descriptor.unsupported_criteria & request.requested_criteria
    return CriteriaDiagnostics(
        requested=request.requested_criteria,
        native_applied=native,
        structured_evidence_available=structured,
        unsupported=unsupported,
        postprocess=request.requested_criteria - native,
    )


def _with_retry(
    record: SourceAttemptRecord,
    *,
    next_action: RetryNextAction,
    max_attempts: int,
) -> SourceAttemptRecord:
    return SourceAttemptRecord(
        source=record.source,
        source_type=record.source_type,
        query_variant=record.query_variant,
        attempt=record.attempt,
        outcome=record.outcome,
        started_at=record.started_at,
        finished_at=record.finished_at,
        elapsed_ms=record.elapsed_ms,
        source_limit=record.source_limit,
        limit_reached=record.limit_reached,
        counts=record.counts,
        criteria=record.criteria,
        retry=RetryInfo(
            attempts=record.attempt,
            max_attempts=max_attempts,
            next_action=next_action,
        ),
        evidence=record.evidence,
    )


def _resolve_run_id(request: SearchRequest, explicit_run_id: str | None) -> str:
    if request.append_to_run_id is not None:
        if explicit_run_id is not None and explicit_run_id != request.append_to_run_id:
            raise ValueError("run_id must match append_to_run_id")
        return request.append_to_run_id
    if explicit_run_id is not None:
        if not explicit_run_id.strip():
            raise ValueError("run_id must be non-empty")
        return explicit_run_id
    now = datetime.now(UTC)
    return f"r-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
