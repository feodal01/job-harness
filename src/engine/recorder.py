"""Persistence, events and metrics of one source within one run."""

import logging
from collections.abc import Callable
from typing import Any

from src.engine.filter_stage import FilterStage
from src.engine.models import Clock, SourceMetrics, SourceState
from src.engine.ports import RecordingStorage
from src.enrichment.registry import Enrichment
from src.models.brief import BriefInput
from src.models.filters import FilterResult
from src.models.vacancy import Vacancy
from src.selection.salary.settings import SalaryRange

logger = logging.getLogger(__name__)


class SourceRecorder:
    def __init__(
        self,
        store: RecordingStorage,
        run_id: str,
        brief_id: str,
        source: str,
        clock: Clock,
        on_progress: Callable[[], None],
        *,
        enrichment: Enrichment,
        salary_range: SalaryRange,
        unsupported: frozenset[str] = frozenset(),
    ) -> None:
        self.store, self.run_id, self.brief_id, self.source = store, run_id, brief_id, source
        self.enrichment, self.salary_range = enrichment, salary_range
        self.clock = clock
        self.state = SourceState()
        self._on_progress = on_progress
        self._selections: dict[str, str] = {}
        self._described: set[str] = set()
        self.unsupported = unsupported

    @property
    def metrics(self) -> SourceMetrics:
        return self.state.metrics

    def event(self, name: str, **data: Any) -> None:
        self.store.events.event(self.run_id, name, source=self.source, **data)

    def save(
        self,
        vacancy: Vacancy,
        selection: FilterResult,
        detail_status: str,
        error: str | None = None,
        *,
        detail_fetched: bool = False,
    ) -> None:
        metrics = self.metrics
        previous = self._selections.get(vacancy.id)
        if previous:
            setattr(metrics, previous, getattr(metrics, previous) - 1)
        setattr(metrics, selection.status, getattr(metrics, selection.status) + 1)
        self._selections[vacancy.id] = selection.status
        result = selection.model_dump(mode="json")
        self.store.vacancies.save_vacancy(
            self.run_id,
            self.brief_id,
            vacancy,
            detail_status,
            error,
            source=self.source,
            selection=result,
            detail_fetched=detail_fetched,
        )
        # История относится к условиям запуска, пул — к текущему брифу и всем уже известным данным.
        current = self.store.briefs.brief(self.brief_id)
        assert current is not None
        brief = BriefInput.model_validate({k: v for k, v in current.items() if k in BriefInput.model_fields})
        key = f"{self.source}:{vacancy.id}"
        stored = self.store.vacancies.pool_item(self.brief_id, key)
        canonical = self.enrichment.enrich(Vacancy.model_validate(stored), self.source)
        pool_selection = FilterStage(
            brief.filters,
            tuple(brief.queries),
            self.unsupported,
            catalog=self.enrichment.catalog,
            salary_range=self.salary_range,
        ).evaluate(canonical)
        self.store.vacancies.set_pool_selection(self.brief_id, key, pool_selection.model_dump(mode="json"))
        self.event(
            "filters.evaluated",
            vacancy_id=vacancy.id,
            stage="detail" if vacancy.description else "listing",
            result=result,
        )
        if vacancy.description and vacancy.id not in self._described:
            self._described.add(vacancy.id)
            metrics.descriptions_ready += 1
            if metrics.first_description_ms is None:
                metrics.first_description_ms = self.clock.ms()
        self.event(
            "vacancy.saved",
            vacancy_id=vacancy.id,
            detail_status=detail_status,
            has_description=bool(vacancy.description),
        )
        self.publish()

    def origin(self, vacancy: Vacancy, query: str, request_url: str, page: int) -> None:
        self.store.vacancies.add_origin(self.run_id, f"{self.source}:{vacancy.id}", query, request_url, page)

    def failed(self, stage: str, exc: Exception, url: str) -> None:
        logger.warning("source.action_failed", extra={"stage": stage})
        counter = f"{stage}_errors"
        setattr(self.metrics, counter, getattr(self.metrics, counter) + 1)
        self.event(f"{stage}.error", url=url, error=str(exc), kind=getattr(exc, "kind", type(exc).__name__))

    def publish(self) -> None:
        self.metrics.elapsed_ms = self.clock.ms()
        self.store.runs.update_source(self.run_id, self.source, self.state.to_json())
        self._on_progress()
