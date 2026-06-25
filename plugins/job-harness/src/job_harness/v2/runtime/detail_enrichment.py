"""Controlled detail-page enrichment for pre-filtered listings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from job_harness.v2.contracts import (
    DescriptionAvailability,
    DetailEnrichmentScraper,
    RawListing,
    SourceOutcome,
)
from job_harness.v2.ports import ArtifactFetcher, DetailRecordWriter
from job_harness.v2.runtime.catalog import SourceCatalog
from job_harness.v2.runtime.config import DetailServiceConfig
from job_harness.v2.runtime.errors import ClassifiedSourceError


@dataclass(frozen=True)
class DetailWorkItem:
    raw_record_id: int
    source: str
    query_variant: str
    listing: RawListing

    def __post_init__(self) -> None:
        if self.raw_record_id < 1:
            raise ValueError("raw_record_id must be >= 1")
        if not self.source.strip():
            raise ValueError("source must be non-empty")
        if not self.query_variant.strip():
            raise ValueError("query_variant must be non-empty")
        if self.listing.source != self.source:
            raise ValueError("listing.source must match work item source")


@dataclass(frozen=True)
class DetailRunResult:
    attempted: int
    enriched: int
    failed: int
    stopped_sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.attempted < 0:
            raise ValueError("attempted must be >= 0")
        if self.enriched < 0:
            raise ValueError("enriched must be >= 0")
        if self.failed < 0:
            raise ValueError("failed must be >= 0")
        if self.enriched + self.failed > self.attempted:
            raise ValueError("enriched + failed must be <= attempted")


@dataclass(frozen=True)
class _ItemResult:
    attempted: bool
    enriched: bool
    failed: bool
    stop_source: bool


class _SourceState:
    def __init__(self) -> None:
        self.stopped = False
        self.stop_lock = asyncio.Lock()
        self.pace_lock = asyncio.Lock()
        self.next_available_at = 0.0


class DetailEnrichmentRunner:
    def __init__(
        self,
        *,
        catalog: SourceCatalog,
        fetcher: ArtifactFetcher,
        writer: DetailRecordWriter,
        config: DetailServiceConfig,
    ) -> None:
        self._catalog = catalog
        self._fetcher = fetcher
        self._writer = writer
        self._config = config

    async def run(self, work_items: tuple[DetailWorkItem, ...]) -> DetailRunResult:
        groups = _group_by_source(work_items)
        if not groups:
            return DetailRunResult(attempted=0, enriched=0, failed=0, stopped_sources=())

        results = await asyncio.gather(
            *(
                self._run_source_group(source, tuple(items))
                for source, items in groups.items()
            )
        )
        attempted = sum(result.attempted for result in results)
        enriched = sum(result.enriched for result in results)
        failed = sum(result.failed for result in results)
        stopped_sources = tuple(
            sorted(source for result in results for source in result.stopped_sources)
        )
        return DetailRunResult(
            attempted=attempted,
            enriched=enriched,
            failed=failed,
            stopped_sources=stopped_sources,
        )

    async def _run_source_group(
        self,
        source: str,
        work_items: tuple[DetailWorkItem, ...],
    ) -> DetailRunResult:
        scraper = self._catalog.get(source)
        if not isinstance(scraper, DetailEnrichmentScraper):
            raise ValueError(f"source does not implement detail enrichment: {source}")

        queue: asyncio.Queue[DetailWorkItem] = asyncio.Queue()
        for item in work_items:
            queue.put_nowait(item)

        state = _SourceState()
        result_lock = asyncio.Lock()
        attempted = 0
        enriched = 0
        failed = 0

        async def worker() -> None:
            nonlocal attempted, enriched, failed
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    item_result = await self._process_when_allowed(
                        item,
                        scraper=scraper,
                        state=state,
                    )
                    async with result_lock:
                        attempted += int(item_result.attempted)
                        enriched += int(item_result.enriched)
                        failed += int(item_result.failed)
                finally:
                    queue.task_done()

        workers = tuple(
            asyncio.create_task(worker())
            for _ in range(min(self._config.per_source_concurrency, len(work_items)))
        )
        await asyncio.gather(*workers)
        return DetailRunResult(
            attempted=attempted,
            enriched=enriched,
            failed=failed,
            stopped_sources=(source,) if state.stopped else (),
        )

    async def _process_when_allowed(
        self,
        item: DetailWorkItem,
        *,
        scraper: DetailEnrichmentScraper,
        state: _SourceState,
    ) -> _ItemResult:
        async with state.stop_lock:
            if state.stopped:
                return _ItemResult(attempted=False, enriched=False, failed=False, stop_source=False)

        await self._wait_for_source_slot(item.source, state)
        item_result = await self._process_item(item, scraper=scraper)
        if item_result.stop_source:
            async with state.stop_lock:
                state.stopped = True
        return item_result

    async def _wait_for_source_slot(self, source: str, state: _SourceState) -> None:
        delay = self._config.delay_for_source(source)
        async with state.pace_lock:
            now = monotonic()
            if now < state.next_available_at:
                await asyncio.sleep(state.next_available_at - now)
            state.next_available_at = monotonic() + delay

    async def _process_item(
        self,
        item: DetailWorkItem,
        *,
        scraper: DetailEnrichmentScraper,
    ) -> _ItemResult:
        try:
            detail_request = scraper.build_detail_request(item.listing)
            response = await self._fetcher.fetch(detail_request)
            if response.source_id != item.source:
                raise ValueError("detail response.source_id must match source descriptor")
            detailed = scraper.parse_detail_response(response, item.listing)
            if not isinstance(detailed, RawListing):
                raise ValueError("parse_detail_response must return RawListing")
            if detailed.source != item.source:
                raise ValueError("detail listing.source must match source descriptor")
        except ClassifiedSourceError as exc:
            availability, error_message = _detail_failure_status(exc)
            self._writer.update_raw_record_detail(
                raw_record_id=item.raw_record_id,
                listing=item.listing,
                description_availability=availability,
                detail_fetched=True,
                detail_parse_error=error_message,
            )
            return _ItemResult(
                attempted=True,
                enriched=False,
                failed=True,
                stop_source=_should_stop_source(exc.outcome, self._config),
            )
        except Exception as exc:
            self._writer.update_raw_record_detail(
                raw_record_id=item.raw_record_id,
                listing=item.listing,
                description_availability=DescriptionAvailability.DETAIL_PARSE_ERROR,
                detail_fetched=True,
                detail_parse_error=str(exc),
            )
            return _ItemResult(attempted=True, enriched=False, failed=True, stop_source=False)

        self._writer.update_raw_record_detail(
            raw_record_id=item.raw_record_id,
            listing=detailed,
            description_availability=(
                DescriptionAvailability.PRESENT
                if detailed.description
                else DescriptionAvailability.NOT_EXPOSED
            ),
            detail_fetched=True,
            detail_parse_error=None,
        )
        return _ItemResult(attempted=True, enriched=True, failed=False, stop_source=False)


def _group_by_source(work_items: tuple[DetailWorkItem, ...]) -> dict[str, list[DetailWorkItem]]:
    groups: dict[str, list[DetailWorkItem]] = {}
    for item in work_items:
        groups.setdefault(item.source, []).append(item)
    return groups


def _detail_failure_status(exc: ClassifiedSourceError) -> tuple[DescriptionAvailability, str]:
    message = exc.evidence.error or str(exc)
    if exc.outcome is SourceOutcome.BLOCKED:
        return DescriptionAvailability.DETAIL_BLOCKED, message
    if exc.outcome is SourceOutcome.RATE_LIMITED:
        return DescriptionAvailability.DETAIL_RATE_LIMITED, message
    if exc.outcome is SourceOutcome.PARSE_ERROR:
        return DescriptionAvailability.DETAIL_PARSE_ERROR, message
    if exc.outcome in (SourceOutcome.SOURCE_TIMEOUT, SourceOutcome.RUN_TIMEOUT):
        return DescriptionAvailability.DETAIL_TIMEOUT, message
    return DescriptionAvailability.DETAIL_PARSE_ERROR, message


def _should_stop_source(outcome: SourceOutcome, config: DetailServiceConfig) -> bool:
    if outcome is SourceOutcome.BLOCKED:
        return config.stop_on_blocked
    if outcome is SourceOutcome.RATE_LIMITED:
        return config.stop_on_rate_limited
    return False
