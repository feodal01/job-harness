"""One source within one run: plan → listing pages → filter → cards → employer profiles."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, TypeVar
from urllib.parse import parse_qs, urlsplit

import httpx

from src.core.observability import log_context
from src.engine.filter_stage import FilterStage
from src.engine.listing import ListingWalker, Step
from src.engine.models import BLOCKING_KINDS, HINTS, RunSpec, SourceMetrics
from src.engine.recorder import SourceRecorder
from src.engine.reuse import KnownData
from src.engine.tasks import TaskRegistry
from src.enrichment.registry import Enrichment
from src.models.company import Company
from src.models.vacancy import Vacancy
from src.network.client import FetchError, Network
from src.parsers.interfaces import SearchPage, SearchPlan, SearchRequest, SourceAdapter

logger = logging.getLogger(__name__)
FETCH_ERRORS = (FetchError, ValueError, httpx.HTTPError)
T = TypeVar("T")


class SourceRun:
    def __init__(
        self,
        adapter: SourceAdapter,
        network: Network,
        spec: RunSpec,
        recorder: SourceRecorder,
        known: KnownData,
        registry: TaskRegistry,
        enrichment: Enrichment,
    ) -> None:
        self.adapter, self.network, self.spec, self.recorder = adapter, network, spec, recorder
        self.enrichment = enrichment
        self.known, self.registry = known, registry
        self.filters = FilterStage(
            spec.filters,
            tuple(spec.brief.queries),
            frozenset(k for k, mode in adapter.search.parameters.items() if mode == "none"),
            catalog=enrichment.catalog,
        )
        self.limits = spec.limits[adapter.id]
        self.profiles = spec.company_profiles[adapter.id]
        self.seen: set[str] = set()
        # Profile outcome per URL within this run, failures included: one attempt and one error per employer.
        self.companies: dict[str, asyncio.Task[Company | None]] = {}
        self.blocked: str | None = None

    @property
    def metrics(self) -> SourceMetrics:
        return self.recorder.metrics

    async def run(self) -> None:
        with log_context(run_id=self.spec.run_id, source_id=self.adapter.id, stage="source"):
            logger.info("source.started")
            try:
                await self._execute_source()
            finally:
                logger.info("source.finished")

    async def _execute_source(self) -> None:
        """Never raises except on cancellation: one source must not stop the others."""
        state = self.recorder.state
        state.status = "running"
        self.recorder.publish()
        try:
            plan = await self._prepare()
            await self._collect(plan)
            self._finish()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._block(exc):
                logger.exception("Source %s failed in run %s", self.adapter.id, self.spec.run_id)
                state.status = "failed"
                state.error = f"{type(exc).__name__}: {exc}"
                self.recorder.event("source.failed", error=state.error)
            else:
                self._finish()
        finally:
            for task in self.companies.values():
                task.cancel()  # Only this run's waiters; shared fetches in the registry continue.
            self.recorder.publish()

    def _finish(self) -> None:
        state, metrics = self.recorder.state, self.metrics
        if self.blocked:
            state.status = "blocked"
            state.error = f"{self.blocked}: {HINTS[self.blocked]}"
        elif metrics.failures:
            state.status = "partial" if metrics.unique_seen else "failed"
            state.error = f"{metrics.failures} failed actions; see events"
        else:
            state.status = "complete"

    def _block(self, exc: BaseException) -> bool:
        kind = getattr(exc, "kind", None)
        if kind not in BLOCKING_KINDS:
            return False
        if not self.blocked:
            self.blocked = kind
            state = self.recorder.state
            state.blocked_reason, state.hint = kind, HINTS[kind]
            self.recorder.event("source.blocked", kind=kind, error=str(exc), hint=HINTS[kind])
        return True

    async def _prepare(self) -> SearchPlan:
        async def fetch(url: str) -> str:
            self.metrics.preparation_requests += 1
            return await self.network.get(self.spec.run_id, url, "geography")

        plan = await self.adapter.search.prepare(
            self.spec.brief, fetch, second_chance=self.spec.second_chance
        )
        self.recorder.event(
            "search.plan",
            application=plan.application,
            second_chance=self.spec.second_chance,
            notes=plan.notes,
            filters=self.spec.filters.model_dump(mode="json"),
            places=[place.to_json() for place in plan.places],
        )
        for place in plan.places:
            # An unprepared place is a failed branch, never an empty result; other places continue.
            for condition in place.conditions:
                if condition.status == "error":
                    self.recorder.failed(
                        "listing",
                        ValueError(condition.reason or "place preparation failed"),
                        condition.origin or "",
                    )
        if self.adapter.search.parameters.get("query") == "ignored":
            self.recorder.event(
                "search.query_skipped",
                queries=self.spec.brief.queries,
                reason=self.adapter.search.query_notes,
            )
        return plan

    async def _collect(self, plan: SearchPlan) -> None:
        concurrency = self.adapter.settings.concurrency
        queue: asyncio.Queue[Vacancy | None] = asyncio.Queue(maxsize=concurrency * 2)
        walker = ListingWalker(plan.requests, self.limits)
        async with asyncio.TaskGroup() as group:
            for _ in range(concurrency):
                group.create_task(self._worker(queue))
            try:
                while not self.blocked and (step := walker.next()):
                    await self._listing_step(walker, step, queue)
            finally:
                for _ in range(concurrency):
                    await queue.put(None)
        self.recorder.state.limit_reached = walker.limit_reached

    async def _listing_step(
        self, walker: ListingWalker, step: Step, queue: asyncio.Queue[Vacancy | None]
    ) -> None:
        url, query, metrics = step.request.url, step.phrase.query, self.metrics
        if walker.repeated(step):
            self.recorder.failed("listing", ValueError("Pagination repeated a request"), url)
            return
        self._log_request(step.request)
        page_number = step.page + 1
        try:
            page = await self._fetch_parse(
                url, step.request.stage, self.adapter.search.parse, step.request, request=step.request
            )
        except FETCH_ERRORS as exc:
            self.recorder.failed("listing", exc, url)
            self._block(exc)
            self.recorder.publish()
            return
        # Facts in the brief vocabulary: parsers stay pure, the engine enriches what they return.
        page = replace(page, vacancies=[self._enrich(v) for v in page.vacancies])
        if step.request.stage == "listing":
            metrics.pages += 1
            metrics.source_total = page.total
        else:
            metrics.preparation_requests += 1
        metrics.raw_seen += len(page.vacancies)
        self.recorder.event(
            "listing.parsed",
            url=url,
            total=page.total,
            count=len(page.vacancies),
            next_requests=len(page.next_requests),
        )
        new_items = await self._take_page(walker, step, page, page_number, queue)
        walker.finished(step, page, new_items)
        progress = metrics.queries.setdefault(query, {"pages": 0, "new": 0})
        progress["pages"] += 1
        progress["new"] += new_items
        self.recorder.publish()

    async def _take_page(
        self,
        walker: ListingWalker,
        step: Step,
        page: SearchPage,
        page_number: int,
        queue: asyncio.Queue[Vacancy | None],
    ) -> int:
        """Save the page's vacancies, filter them, and queue those worth a card. Returns new uniques."""
        capacity, new_items = walker.capacity(step.phrase), 0
        for vacancy in page.vacancies:
            if vacancy.id in self.seen:
                self.recorder.origin(vacancy, step.phrase.query, step.request.url, page_number)
                continue
            if new_items >= capacity:
                walker.limit_reached = True
                break
            self.seen.add(vacancy.id)
            new_items += 1
            self.metrics.unique_seen += 1
            if self.metrics.first_listing_ms is None:
                self.metrics.first_listing_ms = self.recorder.clock.ms()
            decision = self.filters.after_listing(vacancy)
            if not decision.needs_more:
                self.metrics.prefiltered += 1
                self.recorder.save(vacancy, decision.selection, "filtered")
            else:
                wanted = self.adapter.vacancy_details is not None or self._wants_profile(vacancy)
                self.recorder.save(vacancy, decision.selection, "pending" if wanted else "not_required")
            self.recorder.origin(vacancy, step.phrase.query, step.request.url, page_number)
            if decision.needs_more:
                await queue.put(vacancy)
        return new_items

    def _log_request(self, request: SearchRequest) -> None:
        self.recorder.event(
            "search.request",
            stage=request.stage,
            url=request.url,
            parameters={"query": request.query, "filters": self.spec.filters.model_dump(mode="json")},
        )
        self.recorder.event(
            "search.mapping",
            query=request.query,
            mode=self.adapter.search.parameters["query"],
            parameters={
                key: values[0] if len(values) == 1 else values
                for key, values in parse_qs(urlsplit(request.url).query).items()
            },
        )

    async def _worker(self, queue: asyncio.Queue[Vacancy | None]) -> None:
        while (vacancy := await queue.get()) is not None:
            if self.blocked:
                self.metrics.skipped_blocked += 1
                self.recorder.save(vacancy, self.filters.evaluate(vacancy), "skipped_blocked", self.blocked)
                continue
            card, status, error, fetched = await self._card(vacancy)
            decision = self.filters.after_detail(card)
            if status != "not_required":
                self.recorder.save(card, decision.selection, status, error, detail_fetched=fetched)
            if decision.needs_more and self._wants_profile(card):
                company = await self._company(card)
                if company and card.company:
                    card.company = card.company.model_copy(update=company.model_dump(exclude_none=True))
                    self.recorder.save(card, decision.selection, status, error)

    async def _card(self, vacancy: Vacancy) -> tuple[Vacancy, str, str | None, bool]:
        """(vacancy, detail_status, error, fetched now). A known card is reused, never fetched again."""
        details = self.adapter.vacancy_details
        if details is None:
            return vacancy, "not_required", None, False
        known = self.known.card(self.adapter.id, vacancy)
        if known is not None:
            self.metrics.details_reused += 1
            return known, "reused", None, False
        url = details.url(vacancy)
        try:
            card = await self.registry.once(
                f"{self.adapter.id}:vacancy:{vacancy.id}",
                lambda: self._fetch_parse(url, "detail", details.parse, vacancy),
            )
        except FETCH_ERRORS as exc:
            if getattr(exc, "kind", None) == "not_found":
                self.metrics.details_not_found += 1
                self.recorder.event("detail.not_found", url=url)
                return vacancy, "not_found", str(exc), False
            self.recorder.failed("detail", exc, url)
            self._block(exc)
            return vacancy, "error", str(exc), False
        card = self._enrich(card)
        self.metrics.details_ok += 1
        if self.metrics.first_detail_ms is None:
            self.metrics.first_detail_ms = self.recorder.clock.ms()
        return card, "ready", None, True

    def _enrich(self, vacancy: Vacancy) -> Vacancy:
        return self.enrichment.enrich(vacancy, self.adapter.id)

    def _wants_profile(self, vacancy: Vacancy) -> bool:
        component = self.adapter.company_profile
        return bool(self.profiles and component and vacancy.company and component.url(vacancy.company))

    async def _company(self, vacancy: Vacancy) -> Company | None:
        component, company = self.adapter.company_profile, vacancy.company
        if component is None or company is None or self.blocked:
            return None
        url = component.url(company)
        if url is None:
            return None
        if url not in self.companies:
            self.companies[url] = asyncio.ensure_future(self._company_once(url, company))
        return await self.companies[url]

    async def _company_once(self, url: str, company: Company) -> Company | None:
        known = self.known.company(self.adapter.id, url)
        if known is not None:
            self.metrics.companies_reused += 1
            return known
        component = self.adapter.company_profile
        assert component is not None

        async def fetch() -> Company:
            parsed: Company = await self._fetch_parse(url, "company", component.parse, company)
            self.known.remember_company(self.adapter.id, url, parsed)
            return parsed

        try:
            parsed = await self.registry.once(f"{self.adapter.id}:company:{url}", fetch)
        except FETCH_ERRORS as exc:
            self.recorder.failed("company", exc, url)
            self._block(exc)
            return None
        else:
            self.metrics.companies_ok += 1
            return parsed
        finally:
            self.recorder.publish()

    async def _fetch_parse(
        self, url: str, stage: str, parser: Callable[..., T], *args: Any, request: SearchRequest | None = None
    ) -> T:
        run_id = self.spec.run_id
        body = (
            await self.network.fetch(run_id, request)
            if request is not None
            else await self.network.get(run_id, url, stage)
        )
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(parser, body, *args)
        except Exception as exc:
            kind = getattr(exc, "kind", "parse_error")
            if kind == "blocked":
                self.network.block()
            self.recorder.event(
                "parse.error", stage=stage, url=url, kind=kind, error=f"{kind}: {type(exc).__name__}: {exc}"
            )
            if not isinstance(exc, ValueError):
                raise
            raise FetchError(kind, str(exc)) from exc
        self.recorder.event(
            "parse.finished", stage=stage, url=url, elapsed_ms=round((time.monotonic() - started) * 1000, 2)
        )
        return result
