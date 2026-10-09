"""HTTP-представления запусков; снимки запросов не переинтерпретируются."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue

RunStatus = Literal["queued", "running", "complete", "partial", "failed", "cancelled", "interrupted"]
SourceStatus = Literal[
    "queued", "running", "complete", "partial", "blocked", "failed", "cancelled", "interrupted", "skipped"
]
SNAPSHOT_DESCRIPTION = (
    "Неизменяемый JSON-снимок параметров запуска. Схема зависит от версии, создавшей запуск; "
    "исторические поля сохраняются без преобразования в текущую модель запроса."
)


class QueryMetrics(BaseModel):
    pages: int = 0
    new: int = 0


class RunMetrics(BaseModel):
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
    unknown: int = 0
    prefiltered: int = 0
    filtered: int = 0
    descriptions_ready: int = 0
    source_total: int | None = None
    hh_total: int | None = None
    first_listing_ms: float | None = None
    first_detail_ms: float | None = None
    first_description_ms: float | None = None
    elapsed_ms: float = 0
    queries: dict[str, QueryMetrics] = Field(default_factory=dict)


class RunSourceState(BaseModel):
    status: SourceStatus
    error: str | None = None
    blocked_reason: str | None = None
    hint: str | None = None
    limit_reached: bool = False
    metrics: RunMetrics


class RunSummary(BaseModel):
    id: str
    status: RunStatus
    created_at: float
    request: dict[str, JsonValue] = Field(description=SNAPSHOT_DESCRIPTION)


class RunResponse(RunSummary):
    brief_id: str
    finished_at: float | None
    metrics: RunMetrics
    error: str | None
    limit_reached: bool
    sources: dict[str, RunSourceState]


class EventResponse(BaseModel):
    id: int
    run_id: str | None
    time: float
    event: str
    data: dict[str, JsonValue] = Field(description="Данные события; набор полей определяется типом event.")
