"""Starts runs and drives their sources in parallel. Knows no concrete source."""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import fields

import httpx
from pydantic import ValidationError

from src.core.config import Settings
from src.core.errors import ConflictError, InvalidSearchError, NotFoundError
from src.core.observability import log_context
from src.engine.models import Clock, Limits, RunSpec, SourceMetrics, SourceState, SourceStatus
from src.engine.ports import SearchStorage
from src.engine.recorder import SourceRecorder
from src.engine.reuse import KnownData
from src.engine.source_run import SourceRun
from src.engine.tasks import TaskRegistry
from src.enrichment.registry import Enrichment, default_enrichment
from src.models.brief import BriefInput
from src.models.run import RunInput
from src.network.client import HostSlots, Network
from src.parsers.interfaces import Fetch, SourceAdapter

logger = logging.getLogger(__name__)
FINISHED_OK = {"complete", "skipped"}


class SearchEngine:
    def __init__(
        self,
        settings: Settings,
        store: SearchStorage,
        sources: dict[str, SourceAdapter],
        client: httpx.AsyncClient,
        enrichment: Enrichment | None = None,
    ) -> None:
        self.settings, self.store, self.sources = settings, store, sources
        # The application passes its single enrichment (built on its geography catalog).
        self.enrichment = enrichment or default_enrichment()
        self.client = client
        self.hosts = HostSlots(settings.max_requests_per_host)
        # One network per resource for the service's life, so runs share pacing and breakers.
        self.resources: dict[str, Network] = {}
        self.networks: dict[str, Network] = {key: self._network(source) for key, source in sources.items()}
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancelled_runs: set[str] = set()
        self.registry = TaskRegistry()
        self.known = KnownData(store.vacancies, store.companies, self.enrichment)
        self.stopping = False

    def _network(self, source: SourceAdapter) -> Network:
        network = self.resources.get(source.resource)
        if network is None:
            network = self.resources[source.resource] = Network(
                source, self.store.resources, self.store.events, self.client, self.hosts
            )
        elif network.settings != source.settings or network.source.allowed_hosts != source.allowed_hosts:
            raise ValueError(f"Conflicting policies for shared resource {source.resource}")
        return network

    def start(
        self,
        request: RunInput,
        boards: Mapping[str, SourceAdapter] | None = None,
        unavailable: Mapping[str, str] | None = None,
    ) -> dict:
        """Start a run. `boards` are company boards read from the catalog for this run;
        `unavailable` are requested sources left out with the reason (e.g. no login)."""
        if self.stopping:
            raise ConflictError("Сервис останавливается. Запустите его снова перед поиском.")
        if len(self.tasks) >= self.settings.max_active_runs:
            raise ConflictError("Too many active searches; wait or cancel one")
        adapters = {**self.sources, **(boards or {})}
        unavailable = dict(unavailable or {})
        unknown = [s for s in request.sources if s not in adapters and s not in unavailable]
        if unknown:
            raise InvalidSearchError(f"Unknown source: {', '.join(unknown)}")
        brief = self.store.briefs.brief(request.brief_id)
        if brief is None:
            raise NotFoundError("Brief not found")
        if brief.get("migration_note"):
            raise InvalidSearchError("Проверьте и сохраните фильтры этого брифа: " + brief["migration_note"])
        try:
            saved_brief = BriefInput.model_validate(
                {k: v for k, v in brief.items() if k in BriefInput.model_fields}
            )
        except ValidationError as exc:
            raise InvalidSearchError("Проверьте и сохраните условия этого брифа перед поиском") from exc
        selected = [s for s in dict.fromkeys(request.sources or self.sources) if s not in unavailable]
        skipped = {
            s: f"{adapters[s].name} requires at least one search query"
            for s in selected
            if adapters[s].search.parameters.get("query") in {"native", "mapped"} and not saved_brief.queries
        }
        active = [s for s in selected if s not in skipped]
        skipped.update(unavailable)
        if not active:
            raise InvalidSearchError("; ".join(dict.fromkeys(skipped.values())))
        for s in active:
            self._network(adapters[s])  # refuses conflicting policies before the run is stored
        limits = {s: self._limits(adapters[s], request) for s in active}
        profiles = {
            s: request.company_profiles is not False and adapters[s].company_profile is not None
            for s in active
        }
        second_chance = (
            self.settings.second_chance if request.second_chance is None else request.second_chance
        )
        payload = {
            "brief_id": request.brief_id,
            "sources": active,
            "skipped": skipped,
            "limits": {s: vars(v) for s, v in limits.items()},
            "company_profiles": profiles,
            "second_chance": second_chance,
        }
        states = {s: SourceState().to_json() for s in active}
        states.update({s: {**SourceState(status="skipped", error=e).to_json()} for s, e in skipped.items()})
        run = self.store.runs.create_run(request.brief_id, payload, states)
        spec = RunSpec(
            run_id=run["id"],
            brief_id=request.brief_id,
            brief=saved_brief,
            filters=saved_brief.filters,
            sources=tuple(active),
            limits=limits,
            company_profiles=profiles,
            second_chance=second_chance,
            adapters={s: adapters[s] for s in active},
        )
        task = asyncio.create_task(self._run(spec), name=f"search-{run['id']}")
        self.tasks[run["id"]] = task
        task.add_done_callback(lambda _: self._finished(run["id"]))
        return run

    def _finished(self, run_id: str) -> None:
        self.tasks.pop(run_id, None)
        self.cancelled_runs.discard(run_id)

    def _limits(self, source: SourceAdapter, request: RunInput) -> Limits:
        settings, override = source.settings, request.limits.get(source.id)
        return Limits(
            max_items_per_query=(override and override.max_items_per_query) or settings.max_items_per_query,
            max_pages_per_query=(override and override.max_pages_per_query) or settings.max_pages_per_query,
        )

    def cancel(self, run_id: str) -> None:
        run = self.store.runs.run(run_id)
        task = self.tasks.get(run_id)
        if task and not task.done():
            self.cancelled_runs.add(run_id)
            self.store.runs.progress(run_id, run["metrics"], status="cancelled", error="Cancelled by user")
            task.cancel()

    async def close(self) -> None:
        self.stopping = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.registry.close()

    def catalog_fetch(self, source: str) -> Fetch:
        return lambda url: self.networks[source].get(None, url, "geography")

    def source_state(self, source: str) -> dict:
        """Whether the source is currently refused by its breaker (shared by all runs)."""
        return self.networks[source].availability()

    async def _run(self, spec: RunSpec) -> None:
        with log_context(run_id=spec.run_id, stage="search"):
            logger.info("run.started")
            try:
                await self._execute_run(spec)
            finally:
                logger.info("run.finished")

    async def _execute_run(self, spec: RunSpec) -> None:
        clock = Clock()
        recorders: dict[str, SourceRecorder] = {}

        def publish() -> None:
            self.store.runs.progress(spec.run_id, combined(recorders.values(), clock))

        for source in spec.sources:
            recorders[source] = SourceRecorder(
                self.store,
                spec.run_id,
                spec.brief_id,
                source,
                clock,
                publish,
                enrichment=self.enrichment,
                salary_range=self.settings.salary_range,
                unsupported=frozenset(
                    key for key, mode in spec.adapters[source].search.parameters.items() if mode == "none"
                ),
            )
        run = self.store.runs.run(spec.run_id)
        self.store.events.event(
            spec.run_id,
            "run.started",
            request=run["request"],
            settings={s: spec.adapters[s].settings.model_dump(mode="json") for s in spec.sources},
            capabilities={s: spec.adapters[s].capabilities.model_dump(mode="json") for s in spec.sources},
            run_timeout_seconds=self.settings.run_timeout_seconds,
        )
        self.store.events.event(spec.run_id, "filters.started", filters=run["request"]["brief"]["filters"])
        publish()
        status: SourceStatus
        error: str | None
        status, error = "complete", None
        try:
            async with asyncio.timeout(self.settings.run_timeout_seconds):
                async with asyncio.TaskGroup() as group:
                    for source in spec.sources:
                        runner = SourceRun(
                            spec.adapters[source],
                            self._network(spec.adapters[source]),
                            spec,
                            recorders[source],
                            self.known,
                            self.registry,
                            self.enrichment,
                            self.settings.salary_range,
                        )
                        group.create_task(runner.run())
            status, error = outcome(recorders)
        except TimeoutError:
            self._stop_unfinished(recorders, "partial", "run_deadline: search time budget exceeded")
            status, error = outcome(recorders)
            error = "run_deadline: search time budget exceeded; start a new search"
        except asyncio.CancelledError:
            user_cancelled = spec.run_id in self.cancelled_runs
            status = "interrupted" if self.stopping and not user_cancelled else "cancelled"
            error = "Service stopped" if status == "interrupted" else "Cancelled by user"
            self._stop_unfinished(recorders, status, error)
        except Exception as exc:
            logger.exception("Search failed: %s", spec.run_id)
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            metrics = combined(recorders.values(), clock)
            limited = any(r.state.limit_reached for r in recorders.values())
            self.store.runs.progress(spec.run_id, metrics, status=status, error=error, limit_reached=limited)
            self.store.events.event(
                spec.run_id,
                "run.finished",
                status=status,
                error=error,
                metrics=metrics,
                limit_reached=limited,
            )

    @staticmethod
    def _stop_unfinished(recorders: dict[str, SourceRecorder], status: SourceStatus, error: str) -> None:
        for recorder in recorders.values():
            if recorder.state.status in {"queued", "running"}:
                recorder.state.status = status
                recorder.state.error = error
                recorder.store.runs.update_source(recorder.run_id, recorder.source, recorder.state.to_json())


def outcome(recorders: dict[str, SourceRecorder]) -> tuple[SourceStatus, str | None]:
    states = {source: r.state for source, r in recorders.items()}
    if all(s.status in FINISHED_OK for s in states.values()):
        return "complete", None
    error = "; ".join(f"{source}: {s.error}" for source, s in states.items() if s.status not in FINISHED_OK)
    found = sum(s.metrics.unique_seen for s in states.values())
    return ("partial" if found else "failed"), error


def combined(recorders: Iterable[SourceRecorder], clock: Clock) -> dict:
    """Run-level metrics: sums of counters, earliest first-result times, elapsed time of the whole run."""
    metrics = [r.metrics for r in recorders]
    total: dict = {}
    for f in fields(SourceMetrics):
        values = [getattr(m, f.name) for m in metrics]
        if f.name.startswith("first_"):
            present = [v for v in values if v is not None]
            total[f.name] = min(present) if present else None
        elif f.type is int:
            total[f.name] = sum(values)
    total["elapsed_ms"] = clock.ms()
    total["filtered"] = total.get("rejected", 0)
    return total
