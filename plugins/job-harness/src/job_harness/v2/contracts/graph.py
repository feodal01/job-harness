"""Durable graph task and event contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from job_harness.v2.contracts.independent import ParserInput, ParserRef, ParserType
from job_harness.v2.contracts.json_types import JsonObject


class TaskClass(StrEnum):
    LISTING = "listing"
    DETAIL = "detail"
    PROFILE = "profile"
    SITE = "site"


class StaleLeaseError(RuntimeError):
    """Raised when a worker attempts to commit with an expired lease token."""


@dataclass(frozen=True)
class ParserInvocationSpec:
    execution_id: str
    source_plan_id: str | None
    parent_invocation_id: str | None
    cause_event_id: str | None
    parser_ref: ParserRef
    parser_type: ParserType
    input_schema_id: str
    parser_input: ParserInput
    task_class: TaskClass
    task_key: str
    available_at: float
    reserved_collection_units: int | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.execution_id, "execution_id"),
            (self.input_schema_id, "input_schema_id"),
            (self.task_key, "task_key"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.available_at < 0:
            raise ValueError("available_at must be >= 0")
        if self.reserved_collection_units is not None and self.reserved_collection_units < 1:
            raise ValueError("reserved_collection_units must be >= 1")
        expected_task_class = {
            ParserType.SEARCH_LISTING: TaskClass.LISTING,
            ParserType.VACANCY_DETAIL: TaskClass.DETAIL,
            ParserType.COMPANY_PROFILE: TaskClass.PROFILE,
            ParserType.COMPANY_SITE: TaskClass.SITE,
        }[self.parser_type]
        if self.task_class != expected_task_class:
            raise ValueError("task_class must match parser_type")


@dataclass(frozen=True)
class LeasedParserInvocation:
    invocation_id: str
    spec: ParserInvocationSpec
    lease_owner: str
    lease_token: str
    lease_until: float

    @property
    def parser_input(self) -> ParserInput:
        return self.spec.parser_input


@dataclass(frozen=True)
class ExecutionCoordinatorLease:
    execution_id: str
    owner_id: str
    token: str
    lease_until: float


@dataclass(frozen=True)
class StoredDomainEvent:
    event_id: str
    execution_id: str
    producer_invocation_id: str | None
    event_type: str
    schema_version: int
    payload: JsonObject
    occurred_at: float
