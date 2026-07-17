"""SQLite repository for the durable independent-scraper execution graph."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanyProfileResult,
    CompanyRef,
    CompanySiteInput,
    CompanySiteResult,
    ExecutionArtifact,
    ExecutionCoordinatorLease,
    FactDerivation,
    FactProviderSpec,
    LeasedParserInvocation,
    ParserInvocationSpec,
    ParserManifest,
    ParserRef,
    ParserType,
    ProviderStage,
    SearchListingInput,
    SearchListingOutput,
    SearchListingResult,
    SearchResultOutcome,
    SelectionDecision,
    SelectionOutcome,
    StaleLeaseError,
    StoredDomainEvent,
    TargetResolution,
    TaskClass,
    VacancyDetailInput,
    VacancyDetailResult,
)
from job_harness.v2.ports import ParserAttemptMetrics
from job_harness.v2.serialization import JsonObject, to_jsonable

_MISSING = object()
_MIN_DUPLICATE_MEMBERS = 2
_MAX_COORDINATOR_EVENTS = 20
_MAX_COORDINATOR_LISTINGS = 250
_MAX_SPECULATIVE_ADMISSIONS = 25
_MAX_SPECULATIVE_ADMISSIONS_PER_SOURCE = 10
_SOURCE_LIMIT_REASONS = frozenset({"collection_unit_limit", "invocation_limit", "item_limit"})
type _SchedulingBranch = Literal["listing", "enrichment"]
type _TargetResolver = Callable[[ParserType, str | None, str], TargetResolution]
type _WaitingConsumers = dict[tuple[str, str, str], tuple[sqlite3.Row, ...]]


class _PlannedRequirement(Protocol):
    @property
    def criterion(self) -> str: ...

    @property
    def fact_path(self) -> str: ...

    @property
    def comparison(self) -> JsonObject: ...

    @property
    def provider(self) -> FactProviderSpec: ...

    @property
    def skip_when_final_keep(self) -> bool: ...


@dataclass(frozen=True)
class _ProviderTarget:
    expected_type: ParserType
    parser_input: VacancyDetailInput | CompanyProfileInput | CompanySiteInput
    task_class: TaskClass
    provider_hint: str
    normalized_url: str


type _DiscoveredRequirementPlanner = Callable[
    [str, ParserManifest, tuple[SearchListingInput, ...]],
    tuple[_PlannedRequirement, ...],
]
_SCHEDULING_CYCLE: tuple[_SchedulingBranch, ...] = (
    "listing",
    "listing",
    "enrichment",
)


class _FairReadyInvocationQueue:
    def __init__(self, candidate_rows: list[sqlite3.Row]) -> None:
        self._cursor = 0 if not candidate_rows else int(candidate_rows[0]["scheduler_cursor"])
        self._rows: dict[_SchedulingBranch, list[sqlite3.Row]] = {
            "listing": [
                row for row in candidate_rows if row["scheduling_branch"] == "listing"
            ],
            "enrichment": [
                row for row in candidate_rows if row["scheduling_branch"] == "enrichment"
            ],
        }
        self._indexes: dict[_SchedulingBranch, int] = {"listing": 0, "enrichment": 0}

    @classmethod
    def load(
        cls,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        now: float,
        limit: int,
        excluded_resource_keys: tuple[str, ...],
        require_resolved_resource_keys: bool,
    ) -> _FairReadyInvocationQueue:
        exclusion = ""
        exclusion_parameters: tuple[str, ...] = ()
        if excluded_resource_keys:
            placeholders = ",".join("?" for _ in excluded_resource_keys)
            exclusion = (
                f"AND (resource_key IS NULL OR resource_key NOT IN ({placeholders}))"
            )
            exclusion_parameters = excluded_resource_keys
        resolved_filter = "AND resource_key_resolved = 1" if require_resolved_resource_keys else ""
        candidate_rows = connection.execute(
            f"""
            WITH listing_ready AS (
                SELECT parser_invocations.*, 'listing' AS scheduling_branch
                FROM parser_invocations
                WHERE execution_id = ?
                  AND status IN ('queued', 'waiting')
                  AND available_at <= ?
                  AND task_class = 'listing'
                  {resolved_filter}
                  {exclusion}
                ORDER BY available_at, created_at, invocation_id
                LIMIT ?
            ),
            enrichment_ready AS (
                SELECT parser_invocations.*, 'enrichment' AS scheduling_branch
                FROM parser_invocations
                WHERE execution_id = ?
                  AND status IN ('queued', 'waiting')
                  AND available_at <= ?
                  AND task_class != 'listing'
                  {resolved_filter}
                  {exclusion}
                ORDER BY available_at, created_at, invocation_id
                LIMIT ?
            ),
            ready AS (
                SELECT * FROM listing_ready
                UNION ALL
                SELECT * FROM enrichment_ready
            )
            SELECT ready.*, execution.scheduler_cursor
            FROM ready
            JOIN search_executions AS execution
              ON execution.execution_id = ready.execution_id
            ORDER BY ready.scheduling_branch, ready.available_at,
                ready.created_at, ready.invocation_id
            """,
            (
                execution_id,
                now,
                *exclusion_parameters,
                limit,
                execution_id,
                now,
                *exclusion_parameters,
                limit,
            ),
        ).fetchall()
        return cls(candidate_rows)

    def pop(self) -> tuple[sqlite3.Row, _SchedulingBranch] | None:
        listing = self._peek("listing")
        enrichment = self._peek("enrichment")
        if listing is None and enrichment is None:
            return None
        if enrichment is None:
            branch: _SchedulingBranch = "listing"
        elif listing is None:
            branch = "enrichment"
        else:
            branch = _SCHEDULING_CYCLE[self._cursor]
        self._cursor = (self._cursor + 1) % len(_SCHEDULING_CYCLE)
        return self._take(branch)

    @property
    def cursor(self) -> int:
        return self._cursor

    def _peek(self, branch: _SchedulingBranch) -> sqlite3.Row | None:
        index = self._indexes[branch]
        rows = self._rows[branch]
        return None if index >= len(rows) else rows[index]

    def _take(self, branch: _SchedulingBranch) -> tuple[sqlite3.Row, _SchedulingBranch]:
        row = self._peek(branch)
        if row is None:
            raise RuntimeError("ready invocation branch is empty")
        self._indexes[branch] += 1
        return row, branch


class SqliteGraphRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self._database_path, check_same_thread=False, timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.executescript(_schema_sql())
        self._closed = False

    @property
    def database_path(self) -> Path:
        return self._database_path

    def create_execution(
        self,
        *,
        run_id: str,
        intent: JsonObject,
        append_sequence: int,
        policy_version: str,
        runtime_config_version: str,
        active_runtime_budget_ms: int,
        discovery_plan_budget: int = 20,
        speculative_admission_budget: int = 0,
        execution_kind: str = "search",
        parent_execution_id: str | None = None,
        now: float = 0.0,
    ) -> str:
        _require_text(run_id, "run_id")
        _require_text(policy_version, "policy_version")
        _require_text(runtime_config_version, "runtime_config_version")
        if append_sequence < 0:
            raise ValueError("append_sequence must be >= 0")
        if discovery_plan_budget < 0:
            raise ValueError("discovery_plan_budget must be >= 0")
        if speculative_admission_budget < 0:
            raise ValueError("speculative_admission_budget must be >= 0")
        if active_runtime_budget_ms < 1:
            raise ValueError("active_runtime_budget_ms must be >= 1")
        if execution_kind not in {"search", "enrichment", "discovered_search"}:
            raise ValueError("invalid execution_kind")
        if execution_kind == "search" and parent_execution_id is not None:
            raise ValueError("search execution cannot declare a parent")
        if execution_kind != "search" and parent_execution_id is None:
            raise ValueError("child execution requires parent_execution_id")
        execution_id = _new_id("execution")
        intent_id = _new_id("intent")
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runs (run_id, created_at, updated_at) VALUES (?, ?, ?)",
                (run_id, now, now),
            )
            connection.execute(
                "INSERT INTO search_intents (intent_id, schema_id, intent_json, created_at) VALUES (?, ?, ?, ?)",
                (intent_id, "search-intent.v1", _json_dumps(intent), now),
            )
            connection.execute(
                """
                INSERT INTO search_executions (
                    execution_id,
                    run_id,
                    intent_id,
                    append_sequence,
                    execution_kind,
                    parent_execution_id,
                    status,
                    policy_version,
                    runtime_config_version,
                    active_runtime_budget_ms,
                    discovery_plan_budget,
                    speculative_admission_budget,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    run_id,
                    intent_id,
                    append_sequence,
                    execution_kind,
                    parent_execution_id,
                    policy_version,
                    runtime_config_version,
                    active_runtime_budget_ms,
                    discovery_plan_budget,
                    speculative_admission_budget,
                    now,
                ),
            )
        return execution_id

    def next_append_sequence(self, run_id: str) -> int:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT MAX(append_sequence) AS latest FROM search_executions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None or row["latest"] is None:
            return 0
        return int(row["latest"]) + 1

    def workflow_snapshot(self, execution_id: str) -> JsonObject:
        with self._lock:
            self._ensure_open()
            selected = self._connection.execute(
                """
                WITH RECURSIVE ancestors AS (
                    SELECT execution_id, parent_execution_id, execution_kind
                    FROM search_executions
                    WHERE execution_id = ?
                    UNION ALL
                    SELECT parent.execution_id, parent.parent_execution_id,
                           parent.execution_kind
                    FROM search_executions AS parent
                    JOIN ancestors AS child
                      ON child.parent_execution_id = parent.execution_id
                )
                SELECT execution_id FROM ancestors
                WHERE execution_kind = 'search'
                """,
                (execution_id,),
            ).fetchone()
            if selected is None:
                raise KeyError(f"unknown workflow execution: {execution_id}")
            root_execution_id = str(selected["execution_id"])
            root = self._connection.execute(
                """
                SELECT execution.run_id, execution.append_sequence, intent.intent_json
                FROM search_executions AS execution
                JOIN search_intents AS intent ON intent.intent_id = execution.intent_id
                WHERE execution.execution_id = ?
                """,
                (root_execution_id,),
            ).fetchone()
            if root is None:
                raise KeyError(f"unknown workflow root: {root_execution_id}")
            rows = self._connection.execute(
                """
                SELECT execution_id, execution_kind, parent_execution_id, status,
                       policy_version, runtime_config_version, completion_reason
                FROM search_executions
                WHERE run_id = ? AND append_sequence = ?
                ORDER BY execution_kind
                """,
                (root["run_id"], root["append_sequence"]),
            ).fetchall()
        executions: JsonObject = {
            str(row["execution_kind"]): {
                "execution_id": str(row["execution_id"]),
                "parent_execution_id": _optional_text(row["parent_execution_id"]),
                "status": str(row["status"]),
                "policy_version": str(row["policy_version"]),
                "runtime_config_version": str(row["runtime_config_version"]),
                "completion_reason": _optional_text(row["completion_reason"]),
            }
            for row in rows
        }
        required_kinds = {"search", "enrichment", "discovered_search"}
        if set(executions) != required_kinds:
            raise RuntimeError("workflow execution set is incomplete")
        return {
            "run_id": str(root["run_id"]),
            "append_sequence": int(root["append_sequence"]),
            "intent": _json_object(root["intent_json"]),
            "executions": executions,
        }

    def final_items(self, execution_id: str) -> tuple[JsonObject, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT final.payload_json
                FROM final_vacancies AS final
                JOIN vacancy_listings AS listing ON listing.listing_id = final.listing_id
                WHERE final.execution_id = ?
                ORDER BY final.score DESC, listing.vacancy_id, final.listing_id
                """,
                (execution_id,),
            ).fetchall()
        return tuple(_json_object(row["payload_json"]) for row in rows)

    def create_source_plan(
        self,
        *,
        execution_id: str,
        source_id: str,
        manifest: ParserManifest,
        queries: tuple[str, ...],
        unit_budget: int,
        item_budget: int,
        invocation_budget: int,
    ) -> str:
        if manifest.parser_type != ParserType.SEARCH_LISTING or manifest.query_mode is None:
            raise ValueError("source plan requires a search-listing manifest")
        if not queries or any(not query.strip() for query in queries):
            raise ValueError("queries must contain non-empty values")
        for value, name in (
            (unit_budget, "unit_budget"),
            (item_budget, "item_budget"),
            (invocation_budget, "invocation_budget"),
        ):
            if value < 1:
                raise ValueError(f"{name} must be >= 1")
        source_plan_id = _new_id("source-plan")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_plans (
                    source_plan_id,
                    execution_id,
                    source_id,
                    parser_id,
                    parser_version,
                    query_mode,
                    queries_json,
                    unit_budget,
                    item_budget,
                    invocation_budget,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned')
                """,
                (
                    source_plan_id,
                    execution_id,
                    source_id,
                    manifest.parser_id,
                    manifest.implementation_version,
                    manifest.query_mode,
                    _json_dumps(queries),
                    unit_budget,
                    item_budget,
                    invocation_budget,
                ),
            )
        return source_plan_id

    def add_fact_requirement(
        self,
        *,
        source_plan_id: str,
        criterion: str,
        fact_path: str,
        comparison: JsonObject,
        provider: FactProviderSpec,
        skip_when_final_keep: bool = False,
    ) -> str:
        _require_text(criterion, "criterion")
        _require_text(fact_path, "fact_path")
        requirement_id = _stable_id("requirement", source_plan_id, criterion, fact_path)
        with self._transaction() as connection:
            self._add_fact_requirement(
                connection,
                requirement_id=requirement_id,
                source_plan_id=source_plan_id,
                criterion=criterion,
                fact_path=fact_path,
                comparison=comparison,
                provider=provider,
                skip_when_final_keep=skip_when_final_keep,
            )
        return requirement_id

    @staticmethod
    def _add_fact_requirement(
        connection: sqlite3.Connection,
        *,
        requirement_id: str,
        source_plan_id: str,
        criterion: str,
        fact_path: str,
        comparison: JsonObject,
        provider: FactProviderSpec,
        skip_when_final_keep: bool,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO criterion_requirements (
                requirement_id, source_plan_id, criterion, required_fact_path,
                comparison_json, skip_when_final_keep, unsupported_reason
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                requirement_id,
                source_plan_id,
                criterion,
                fact_path,
                _json_dumps(comparison),
                int(skip_when_final_keep),
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fact_providers (
                provider_id, requirement_id, provider_stage, parser_id, parser_version,
                deriver_id, deriver_version, fact_path, depends_on_fact_paths_json,
                required_for_final, cost_class, ordering
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider.provider_id,
                requirement_id,
                provider.stage.value,
                None if provider.parser_ref is None else provider.parser_ref.parser_id,
                None if provider.parser_ref is None else provider.parser_ref.implementation_version,
                provider.deriver_id,
                provider.deriver_version,
                provider.fact_path,
                _json_dumps(provider.depends_on_fact_paths),
                int(provider.required_for_final),
                provider.cost_class,
                provider.ordering,
            ),
        )

    def enqueue_invocation(self, spec: ParserInvocationSpec) -> str:
        with self._transaction() as connection:
            return self._enqueue_invocation(connection, spec)

    def lease_ready_invocations(
        self,
        *,
        execution_id: str,
        owner_id: str,
        limit: int,
        lease_seconds: float,
        now: float,
        excluded_resource_keys: tuple[str, ...] = (),
        require_resolved_resource_keys: bool = False,
    ) -> tuple[LeasedParserInvocation, ...]:
        _require_text(owner_id, "owner_id")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        for resource_key in excluded_resource_keys:
            _require_text(resource_key, "excluded_resource_key")
        excluded_resource_keys = tuple(dict.fromkeys(excluded_resource_keys))
        leased: list[LeasedParserInvocation] = []
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE parser_attempts
                SET finished_at = ?, outcome = 'worker_lost',
                    failure_kind = 'worker_lost', retry_decision = 'scheduled'
                WHERE finished_at IS NULL
                  AND invocation_id IN (
                      SELECT invocation_id FROM parser_invocations
                      WHERE execution_id = ? AND status = 'leased' AND lease_until <= ?
                  )
                """,
                (now, execution_id, now),
            )
            connection.execute(
                """
                UPDATE parser_invocations
                SET status = 'queued', lease_owner = NULL, lease_token = NULL, lease_until = NULL
                WHERE execution_id = ? AND status = 'leased' AND lease_until <= ?
                """,
                (execution_id, now),
            )
            budget_rows = connection.execute(
                """
                SELECT
                    plan.source_plan_id,
                    plan.unit_budget,
                    plan.units_used,
                    COALESCE(SUM(invocation.reserved_collection_units), 0)
                        AS active_reservations
                FROM source_plans AS plan
                LEFT JOIN parser_invocations AS invocation
                  ON invocation.source_plan_id = plan.source_plan_id
                 AND invocation.status = 'leased'
                 AND invocation.lease_until > ?
                WHERE plan.execution_id = ?
                GROUP BY plan.source_plan_id
                """,
                (now, execution_id),
            ).fetchall()
            remaining_by_plan = {
                str(row["source_plan_id"]): max(
                    int(row["unit_budget"])
                    - int(row["units_used"])
                    - int(row["active_reservations"]),
                    0,
                )
                for row in budget_rows
            }
            active_reservations_by_plan = {
                str(row["source_plan_id"]): int(row["active_reservations"])
                for row in budget_rows
            }
            ready_queue = _FairReadyInvocationQueue.load(
                connection,
                execution_id=execution_id,
                now=now,
                limit=limit,
                excluded_resource_keys=excluded_resource_keys,
                require_resolved_resource_keys=require_resolved_resource_keys,
            )
            while len(leased) < limit:
                candidate = ready_queue.pop()
                if candidate is None:
                    break
                row, _branch = candidate
                reserved_collection_units = (
                    None
                    if row["reserved_collection_units"] is None
                    else int(row["reserved_collection_units"])
                )
                source_plan_id = _optional_text(row["source_plan_id"])
                if source_plan_id is not None:
                    remaining = remaining_by_plan.get(source_plan_id, 0)
                    if remaining == 0:
                        if active_reservations_by_plan.get(source_plan_id, 0) == 0:
                            connection.execute(
                                """
                                UPDATE parser_invocations
                                SET status = 'cancelled', outcome = 'collection_unit_limit', finished_at = ?
                                WHERE invocation_id = ?
                                """,
                                (now, row["invocation_id"]),
                            )
                            connection.execute(
                                """
                                UPDATE source_plans
                                SET status = 'limit_reached', terminal_reason = 'collection_unit_limit'
                                WHERE source_plan_id = ?
                                """,
                                (source_plan_id,),
                            )
                        continue
                    requested = reserved_collection_units or 1
                    reserved_collection_units = min(requested, remaining)
                    remaining_by_plan[source_plan_id] = remaining - reserved_collection_units
                    active_reservations_by_plan[source_plan_id] = (
                        active_reservations_by_plan.get(source_plan_id, 0)
                        + reserved_collection_units
                    )
                token = uuid4().hex
                lease_until = now + lease_seconds
                connection.execute(
                    """
                    UPDATE parser_invocations
                    SET status = 'leased', lease_owner = ?, lease_token = ?, lease_until = ?,
                        reserved_collection_units = ?
                    WHERE invocation_id = ?
                    """,
                    (
                        owner_id,
                        token,
                        lease_until,
                        reserved_collection_units,
                        row["invocation_id"],
                    ),
                )
                if source_plan_id is not None:
                    connection.execute(
                        """
                        UPDATE source_plans SET status = 'running'
                        WHERE source_plan_id = ? AND status = 'planned'
                        """,
                        (source_plan_id,),
                    )
                leased.append(
                    _leased_invocation(
                        row,
                        owner_id,
                        token,
                        lease_until,
                        reserved_collection_units=reserved_collection_units,
                    )
                )
            connection.execute(
                "UPDATE search_executions SET scheduler_cursor = ? WHERE execution_id = ?",
                (ready_queue.cursor, execution_id),
            )
        return tuple(leased)

    def unresolved_ready_invocations(
        self,
        execution_id: str,
        *,
        limit: int,
        now: float,
    ) -> tuple[tuple[str, ParserInvocationSpec], ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT * FROM parser_invocations
                WHERE execution_id = ?
                  AND status IN ('queued', 'waiting')
                  AND available_at <= ?
                  AND resource_key_resolved = 0
                ORDER BY available_at, created_at, invocation_id
                LIMIT ?
                """,
                (execution_id, now, limit),
            ).fetchall()
        return tuple(
            (str(row["invocation_id"]), _invocation_spec(row)) for row in rows
        )

    def resolve_invocation_resource_keys(
        self,
        resolutions: tuple[tuple[str, str | None], ...],
    ) -> int:
        if not resolutions:
            return 0
        for invocation_id, resource_key in resolutions:
            _require_text(invocation_id, "invocation_id")
            if resource_key is not None:
                _require_text(resource_key, "resource_key")
        with self._transaction() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                UPDATE parser_invocations
                SET resource_key = ?, resource_key_resolved = 1
                WHERE invocation_id = ?
                  AND status IN ('queued', 'waiting')
                  AND resource_key_resolved = 0
                """,
                (
                    (resource_key, invocation_id)
                    for invocation_id, resource_key in resolutions
                ),
            )
            return connection.total_changes - before

    def renew_invocation_leases(
        self,
        *,
        owner_id: str,
        leases: tuple[tuple[str, str], ...],
        lease_seconds: float,
        now: float,
    ) -> int:
        _require_text(owner_id, "owner_id")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if not leases:
            return 0
        for invocation_id, lease_token in leases:
            _require_text(invocation_id, "invocation_id")
            _require_text(lease_token, "lease_token")
        values = ", ".join("(?, ?)" for _ in leases)
        parameters: list[object] = [
            value
            for invocation_id, lease_token in leases
            for value in (invocation_id, lease_token)
        ]
        parameters.extend((now + lease_seconds, owner_id, now))
        with self._transaction() as connection:
            renewed = connection.execute(
                f"""
                WITH owned(invocation_id, lease_token) AS (VALUES {values})
                UPDATE parser_invocations
                SET lease_until = ?
                WHERE status = 'leased'
                  AND lease_owner = ?
                  AND lease_until > ?
                  AND EXISTS (
                      SELECT 1 FROM owned
                      WHERE owned.invocation_id = parser_invocations.invocation_id
                        AND owned.lease_token = parser_invocations.lease_token
                  )
                RETURNING invocation_id
                """,
                tuple(parameters),
            ).fetchall()
        return len(renewed)

    def begin_parser_attempt(
        self,
        invocation: LeasedParserInvocation,
        *,
        now: float,
    ) -> tuple[str, int]:
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            attempt_number = int(
                connection.execute(
                    "SELECT COUNT(*) FROM parser_attempts WHERE invocation_id = ?",
                    (invocation.invocation_id,),
                ).fetchone()[0]
            ) + 1
            attempt_id = _stable_id("parser-attempt", invocation.invocation_id, str(attempt_number))
            connection.execute(
                """
                INSERT INTO parser_attempts (
                    parser_attempt_id, invocation_id, attempt_number, started_at
                ) VALUES (?, ?, ?, ?)
                """,
                (attempt_id, invocation.invocation_id, attempt_number, now),
            )
        return attempt_id, attempt_number

    def defer_invocation(
        self,
        invocation: LeasedParserInvocation,
        *,
        attempt_id: str,
        failure_kind: str,
        attempt_metrics: ParserAttemptMetrics | None = None,
        waiting_reason: str,
        retry_delay_ms: int,
        available_at: float,
        now: float,
    ) -> None:
        if waiting_reason != "retry_backoff":
            raise ValueError("defer_invocation only schedules request retry backoff")
        if retry_delay_ms < 0:
            raise ValueError("retry_delay_ms must be >= 0")
        if available_at <= now:
            raise ValueError("waiting available_at must be later than now")
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            self._finish_parser_attempt(
                connection,
                invocation_id=invocation.invocation_id,
                attempt_id=attempt_id,
                outcome=failure_kind,
                failure_kind=failure_kind,
                attempt_metrics=attempt_metrics,
                retry_decision="scheduled",
                retry_delay_ms=retry_delay_ms,
                now=now,
            )
            connection.execute(
                """
                UPDATE parser_invocations
                SET status = 'waiting', available_at = ?, waiting_reason = ?,
                    lease_owner = NULL, lease_token = NULL, lease_until = NULL
                WHERE invocation_id = ?
                """,
                (available_at, waiting_reason, invocation.invocation_id),
            )

    def defer_unstarted_invocation(
        self,
        invocation: LeasedParserInvocation,
        *,
        waiting_reason: str,
        available_at: float,
        now: float,
    ) -> None:
        if waiting_reason != "resource_pacing":
            raise ValueError("unstarted deferral only supports resource_pacing")
        if available_at <= now:
            raise ValueError("waiting available_at must be later than now")
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            connection.execute(
                """
                UPDATE parser_invocations
                SET status = 'waiting', available_at = ?, waiting_reason = ?,
                    lease_owner = NULL, lease_token = NULL, lease_until = NULL
                WHERE invocation_id = ?
                """,
                (available_at, waiting_reason, invocation.invocation_id),
            )

    def next_scheduler_wakeup_at(
        self,
        execution_id: str,
        *,
        now: float,
    ) -> float | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT MIN(wakeup_at)
                FROM (
                    SELECT available_at AS wakeup_at
                    FROM parser_invocations
                    WHERE execution_id = ? AND status IN ('queued', 'waiting')
                    UNION ALL
                    SELECT lease_until AS wakeup_at
                    FROM parser_invocations
                    WHERE execution_id = ? AND status = 'leased' AND lease_until IS NOT NULL
                    UNION ALL
                    SELECT coordinator_lease_until AS wakeup_at
                    FROM search_executions
                    WHERE execution_id = ?
                      AND coordinator_lease_until IS NOT NULL
                      AND EXISTS (
                          SELECT 1 FROM domain_events
                          WHERE domain_events.execution_id = search_executions.execution_id
                            AND processed_at IS NULL
                      )
                    UNION ALL
                    SELECT ? + MAX(
                        0.0,
                        (
                            active_runtime_budget_ms
                            - active_runtime_ms
                            - CASE
                                WHEN active_session_started_at IS NULL THEN 0
                                ELSE MAX(0, (? - active_session_started_at) * 1000)
                              END
                        ) / 1000.0
                    ) AS wakeup_at
                    FROM search_executions
                    WHERE execution_id = ?
                      AND status = 'running'
                      AND (
                          EXISTS (
                              SELECT 1 FROM parser_invocations
                              WHERE parser_invocations.execution_id = search_executions.execution_id
                                AND status IN ('queued', 'waiting', 'leased')
                          )
                          OR EXISTS (
                              SELECT 1 FROM domain_events
                              WHERE domain_events.execution_id = search_executions.execution_id
                                AND processed_at IS NULL
                          )
                          OR EXISTS (
                              SELECT 1 FROM listing_enrichment_requests
                              WHERE listing_enrichment_requests.execution_id = search_executions.execution_id
                                AND status = 'waiting'
                          )
                      )
                )
                """,
                (
                    execution_id,
                    execution_id,
                    execution_id,
                    now,
                    now,
                    execution_id,
                ),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def release_coordinator_leases(
        self,
        owners: tuple[tuple[str, str], ...],
    ) -> int:
        if not owners:
            return 0
        with self._transaction() as connection:
            released = 0
            for execution_id, owner_id in owners:
                cursor = connection.execute(
                    """
                    UPDATE search_executions
                    SET coordinator_owner = NULL, coordinator_token = NULL,
                        coordinator_lease_until = NULL
                    WHERE execution_id = ? AND coordinator_owner = ?
                    """,
                    (execution_id, owner_id),
                )
                released += cursor.rowcount
        return released

    def begin_execution_sessions(
        self,
        execution_ids: tuple[str, ...],
        *,
        now: float,
    ) -> int:
        if not execution_ids:
            return 0
        placeholders = ",".join("?" for _ in execution_ids)
        with self._transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT execution_id, status FROM search_executions
                WHERE execution_id IN ({placeholders})
                """,
                execution_ids,
            ).fetchall()
            if len(rows) != len(set(execution_ids)):
                raise KeyError("unknown execution in active session batch")
            invalid = tuple(
                str(row["execution_id"])
                for row in rows
                if row["status"] not in {"running", "stopping"}
            )
            if invalid:
                raise RuntimeError(
                    "cannot begin active session for terminal execution: "
                    + ", ".join(invalid)
                )
            cursor = connection.execute(
                f"""
                UPDATE search_executions
                SET active_session_started_at = ?, active_heartbeat_at = ?
                WHERE execution_id IN ({placeholders})
                """,
                (now, now, *execution_ids),
            )
        return cursor.rowcount

    def heartbeat_execution_sessions(
        self,
        execution_ids: tuple[str, ...],
        *,
        now: float,
    ) -> int:
        return self._advance_execution_sessions(
            execution_ids,
            now=now,
            keep_active=True,
        )

    def end_execution_sessions(
        self,
        execution_ids: tuple[str, ...],
        *,
        now: float,
    ) -> int:
        return self._advance_execution_sessions(
            execution_ids,
            now=now,
            keep_active=False,
        )

    def _advance_execution_sessions(
        self,
        execution_ids: tuple[str, ...],
        *,
        now: float,
        keep_active: bool,
    ) -> int:
        if not execution_ids:
            return 0
        placeholders = ",".join("?" for _ in execution_ids)
        next_session = "?" if keep_active else "NULL"
        session_parameters = (now,) if keep_active else ()
        parameters: tuple[object, ...] = (now, now, *session_parameters, *execution_ids)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE search_executions
                SET active_runtime_ms = active_runtime_ms + MAX(
                        0,
                        CAST(ROUND((? - active_session_started_at) * 1000) AS INTEGER)
                    ),
                    active_heartbeat_at = ?,
                    active_session_started_at = {next_session}
                WHERE execution_id IN ({placeholders})
                  AND active_session_started_at IS NOT NULL
                  AND status IN ('running', 'stopping')
                """,
                parameters,
            )
        return cursor.rowcount

    def request_attempt_number(self, invocation_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COUNT(*)
                FROM parser_attempts
                WHERE invocation_id = ?
                  AND (outcome IS NULL OR outcome NOT IN ('worker_lost', 'resource_pacing'))
                """,
                (invocation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("request attempt count query returned no row")
        return int(row[0])

    def request_retry_elapsed_seconds(
        self,
        invocation_id: str,
        *,
        current_network_elapsed_ms: int,
    ) -> float:
        if current_network_elapsed_ms < 0:
            raise ValueError("current_network_elapsed_ms must be >= 0")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT COALESCE(SUM(network_elapsed_ms + retry_delay_ms), 0)
                FROM parser_attempts
                WHERE invocation_id = ?
                  AND finished_at IS NOT NULL
                  AND outcome NOT IN ('worker_lost', 'resource_pacing')
                """,
                (invocation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("request retry elapsed query returned no row")
        return (int(row[0]) + current_network_elapsed_ms) / 1000.0

    def commit_search_result(
        self,
        invocation: LeasedParserInvocation,
        result: SearchListingResult,
        manifest: ParserManifest,
        *,
        attempt_id: str | None = None,
        attempt_metrics: ParserAttemptMetrics | None = None,
        now: float,
    ) -> None:
        if invocation.spec.parser_type != ParserType.SEARCH_LISTING:
            raise ValueError("search result requires a search-listing invocation")
        if invocation.spec.source_plan_id is None:
            raise ValueError("search-listing invocation requires source_plan_id")
        if manifest.ref != invocation.spec.parser_ref or manifest.parser_type != ParserType.SEARCH_LISTING:
            raise ValueError("manifest does not match leased invocation")
        if result.collection_units_consumed > (invocation.spec.reserved_collection_units or 1):
            raise ValueError("result consumed more collection units than reserved")

        with self._transaction() as connection:
            row = self._assert_current_lease(connection, invocation, now)
            source_plan = self._source_plan(connection, invocation.spec.source_plan_id)
            parser_input = invocation.spec.parser_input
            if not isinstance(parser_input, SearchListingInput):
                raise ValueError("stored search invocation has the wrong input type")
            bounded_result, plan_limit_reason = self._bounded_search_result(result, source_plan)
            self._validate_search_result(parser_input, bounded_result, source_plan)

            run_id = self._execution_run_id(connection, invocation.spec.execution_id)
            observation_ids = self._store_listing_observations(
                connection,
                run_id=run_id,
                invocation=invocation,
                manifest=manifest,
                items=bounded_result.items,
                now=now,
            )
            continuation_count, continuations_truncated = self._store_continuations(
                connection,
                invocation=invocation,
                manifest=manifest,
                continuations=bounded_result.continuations,
                source_plan=source_plan,
                now=now,
            )
            if continuations_truncated:
                plan_limit_reason = "invocation_limit"
            connection.execute(
                """
                UPDATE parser_invocations
                SET
                    status = 'succeeded',
                    result_kind = 'search_listing',
                    outcome = ?,
                    lease_owner = NULL,
                    lease_token = NULL,
                    lease_until = NULL,
                    finished_at = ?
                WHERE invocation_id = ?
                """,
                (result.outcome.value, now, invocation.invocation_id),
            )
            if attempt_id is not None:
                self._finish_parser_attempt_success(
                    connection,
                    invocation,
                    attempt_id,
                    attempt_metrics,
                    now,
                )
            self._update_source_plan_after_search_result(
                connection,
                source_plan=source_plan,
                result=bounded_result,
                continuation_count=continuation_count,
                plan_limit_reason=plan_limit_reason,
            )
            if observation_ids:
                event_id = _stable_id("event", invocation.invocation_id, "listing-observations")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO domain_events (
                        event_id,
                        execution_id,
                        producer_invocation_id,
                        event_key,
                        event_type,
                        schema_version,
                        payload_json,
                        occurred_at
                    )
                    VALUES (?, ?, ?, ?, 'listing_observations_stored', 1, ?, ?)
                    """,
                    (
                        event_id,
                        invocation.spec.execution_id,
                        invocation.invocation_id,
                        f"listing-observations:{invocation.invocation_id}",
                        _json_dumps(
                            {
                                "sourcePlanId": invocation.spec.source_plan_id,
                                "invocationId": invocation.invocation_id,
                                "observationIds": observation_ids,
                                "eventSchemaVersion": 1,
                            }
                        ),
                        now,
                    ),
                )
            connection.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (now, run_id))
            if row["invocation_id"] != invocation.invocation_id:
                raise RuntimeError("lease row changed during commit")

    def commit_detail_result(
        self,
        invocation: LeasedParserInvocation,
        result: VacancyDetailResult,
        manifest: ParserManifest,
        *,
        attempt_id: str | None = None,
        attempt_metrics: ParserAttemptMetrics | None = None,
        now: float,
    ) -> None:
        if invocation.spec.parser_type != ParserType.VACANCY_DETAIL:
            raise ValueError("detail result requires a vacancy-detail invocation")
        if manifest.ref != invocation.spec.parser_ref or manifest.parser_type != ParserType.VACANCY_DETAIL:
            raise ValueError("manifest does not match leased invocation")
        parser_input = invocation.spec.parser_input
        if not isinstance(parser_input, VacancyDetailInput):
            raise ValueError("stored detail invocation has the wrong input type")
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            run_id = self._execution_run_id(connection, invocation.spec.execution_id)
            event_type = "invocation_terminal"
            event_payload: JsonObject = {
                "invocationId": invocation.invocation_id,
                "outcome": result.outcome.value,
                "eventSchemaVersion": 1,
            }
            if result.item is not None:
                item = result.item
                if item.target_provider_id != parser_input.target_provider_id:
                    raise ValueError("detail target_provider_id must match parser input")
                if (
                    parser_input.source_listing_id is not None
                    and item.source_listing_id != parser_input.source_listing_id
                ):
                    raise ValueError("detail source_listing_id must match parser input")
                canonical_url = _normalize_url(item.canonical_vacancy_url)
                vacancy_id = self._resolve_vacancy_claims(
                    connection,
                    run_id=run_id,
                    claims=((item.target_provider_id, item.source_listing_id, canonical_url),),
                    now=now,
                )[0]
                observation_id = _stable_id("detail-observation", invocation.invocation_id)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO vacancy_detail_observations (
                        detail_observation_id, vacancy_id, execution_id, invocation_id,
                        output_schema_id, payload_json, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        vacancy_id,
                        invocation.spec.execution_id,
                        invocation.invocation_id,
                        manifest.output_schema_id,
                        _json_dumps(item),
                        now,
                    ),
                )
                event_type = "vacancy_detail_observation_stored"
                event_payload = {
                    "vacancyId": vacancy_id,
                    "detailObservationId": observation_id,
                    "eventSchemaVersion": 1,
                }
            self._finish_invocation(
                connection,
                invocation_id=invocation.invocation_id,
                status="succeeded",
                result_kind="vacancy_detail",
                outcome=result.outcome.value,
                now=now,
            )
            if attempt_id is not None:
                self._finish_parser_attempt_success(
                    connection,
                    invocation,
                    attempt_id,
                    attempt_metrics,
                    now,
                )
            self._insert_terminal_event(
                connection,
                invocation=invocation,
                event_type=event_type,
                payload=event_payload,
                now=now,
            )
            connection.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (now, run_id))

    def commit_profile_result(
        self,
        invocation: LeasedParserInvocation,
        result: CompanyProfileResult,
        manifest: ParserManifest,
        *,
        attempt_id: str | None = None,
        attempt_metrics: ParserAttemptMetrics | None = None,
        now: float,
    ) -> None:
        if invocation.spec.parser_type != ParserType.COMPANY_PROFILE:
            raise ValueError("profile result requires a company-profile invocation")
        if manifest.ref != invocation.spec.parser_ref or manifest.parser_type != ParserType.COMPANY_PROFILE:
            raise ValueError("manifest does not match leased invocation")
        parser_input = invocation.spec.parser_input
        if not isinstance(parser_input, CompanyProfileInput):
            raise ValueError("stored profile invocation has the wrong input type")
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            run_id = self._execution_run_id(connection, invocation.spec.execution_id)
            event_type = "invocation_terminal"
            event_payload: JsonObject = {
                "invocationId": invocation.invocation_id,
                "outcome": result.outcome.value,
                "eventSchemaVersion": 1,
            }
            if result.item is not None:
                item = result.item
                if item.target_provider_id != parser_input.target_provider_id:
                    raise ValueError("profile target_provider_id must match parser input")
                if (
                    parser_input.source_company_id is not None
                    and item.source_company_id != parser_input.source_company_id
                ):
                    raise ValueError("profile source_company_id must match parser input")
                profile_url = _normalize_url(item.profile_url)
                claims = [("profile_url", profile_url)]
                if item.source_company_id is not None:
                    claims.insert(0, ("provider_id", f"{item.target_provider_id}:{item.source_company_id}"))
                if item.official_site_url is not None:
                    claims.append(("verified_domain", _verified_domain(item.official_site_url)))
                company_id = self._resolve_company_ids(
                    connection,
                    run_id=run_id,
                    groups=((item.company_name, tuple(claims)),),
                    now=now,
                )[0]
                if company_id is None:
                    raise RuntimeError("profile output did not produce a strong company identity")
                observation_id = _stable_id("profile-observation", invocation.invocation_id)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO company_profile_observations (
                        profile_observation_id, company_id, execution_id, invocation_id,
                        output_schema_id, profile_url, payload_json, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        company_id,
                        invocation.spec.execution_id,
                        invocation.invocation_id,
                        manifest.output_schema_id,
                        profile_url,
                        _json_dumps(item),
                        now,
                    ),
                )
                for claim_type, claim_value in claims:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO company_identity_claims (
                            company_claim_id, run_id, company_id, claim_type, claim_value,
                            profile_observation_id, status
                        ) VALUES (?, ?, ?, ?, ?, ?, 'active')
                        """,
                        (
                            _stable_id("company-claim", run_id, claim_type, claim_value),
                            run_id,
                            company_id,
                            claim_type,
                            claim_value,
                            observation_id,
                        ),
                    )
                self._store_discovered_endpoints(
                    connection,
                    company_id=company_id,
                    profile_observation_id=observation_id,
                    site_observation_id=None,
                    endpoints=item.career_endpoints,
                )
                event_type = "company_profile_observation_stored"
                event_payload = {
                    "companyId": company_id,
                    "profileObservationId": observation_id,
                    "eventSchemaVersion": 1,
                }
            self._finish_invocation(
                connection,
                invocation_id=invocation.invocation_id,
                status="succeeded",
                result_kind="company_profile",
                outcome=result.outcome.value,
                now=now,
            )
            if attempt_id is not None:
                self._finish_parser_attempt_success(
                    connection,
                    invocation,
                    attempt_id,
                    attempt_metrics,
                    now,
                )
            self._insert_terminal_event(
                connection,
                invocation=invocation,
                event_type=event_type,
                payload=event_payload,
                now=now,
            )
            connection.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (now, run_id))

    def commit_site_result(
        self,
        invocation: LeasedParserInvocation,
        result: CompanySiteResult,
        manifest: ParserManifest,
        *,
        attempt_id: str | None = None,
        attempt_metrics: ParserAttemptMetrics | None = None,
        now: float,
    ) -> None:
        if invocation.spec.parser_type != ParserType.COMPANY_SITE:
            raise ValueError("site result requires a company-site invocation")
        if manifest.ref != invocation.spec.parser_ref or manifest.parser_type != ParserType.COMPANY_SITE:
            raise ValueError("manifest does not match leased invocation")
        parser_input = invocation.spec.parser_input
        if not isinstance(parser_input, CompanySiteInput):
            raise ValueError("stored site invocation has the wrong input type")
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            run_id = self._execution_run_id(connection, invocation.spec.execution_id)
            event_type = "invocation_terminal"
            event_payload: JsonObject = {
                "invocationId": invocation.invocation_id,
                "outcome": result.outcome.value,
                "eventSchemaVersion": 1,
            }
            if result.item is not None:
                item = result.item
                input_domain = _verified_domain(parser_input.site_url)
                canonical_domain = _verified_domain(item.canonical_site_url)
                if input_domain != canonical_domain:
                    raise ValueError("canonical site must remain on the verified input domain")
                company_id = self._resolve_company_ids(
                    connection,
                    run_id=run_id,
                    groups=((item.company_name, (("verified_domain", canonical_domain),)),),
                    now=now,
                )[0]
                if company_id is None:
                    raise RuntimeError("site output did not produce a strong company identity")
                observation_id = _stable_id("site-observation", invocation.invocation_id)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO company_site_observations (
                        site_observation_id, company_id, execution_id, invocation_id,
                        output_schema_id, canonical_site_url, payload_json, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        observation_id,
                        company_id,
                        invocation.spec.execution_id,
                        invocation.invocation_id,
                        manifest.output_schema_id,
                        _normalize_url(item.canonical_site_url),
                        _json_dumps(item),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO company_identity_claims (
                        company_claim_id, run_id, company_id, claim_type, claim_value,
                        site_observation_id, status
                    ) VALUES (?, ?, ?, 'verified_domain', ?, ?, 'active')
                    """,
                    (
                        _stable_id("company-claim", run_id, "verified_domain", canonical_domain),
                        run_id,
                        company_id,
                        canonical_domain,
                        observation_id,
                    ),
                )
                self._store_discovered_endpoints(
                    connection,
                    company_id=company_id,
                    profile_observation_id=None,
                    site_observation_id=observation_id,
                    endpoints=item.career_endpoints,
                )
                event_type = "company_site_observation_stored"
                event_payload = {
                    "companyId": company_id,
                    "siteObservationId": observation_id,
                    "eventSchemaVersion": 1,
                }
            self._finish_invocation(
                connection,
                invocation_id=invocation.invocation_id,
                status="succeeded",
                result_kind="company_site",
                outcome=result.outcome.value,
                now=now,
            )
            if attempt_id is not None:
                self._finish_parser_attempt_success(
                    connection,
                    invocation,
                    attempt_id,
                    attempt_metrics,
                    now,
                )
            self._insert_terminal_event(
                connection,
                invocation=invocation,
                event_type=event_type,
                payload=event_payload,
                now=now,
            )
            connection.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (now, run_id))

    def commit_failure(
        self,
        invocation: LeasedParserInvocation,
        *,
        attempt_id: str | None = None,
        failure_kind: str,
        retry_decision: str,
        public_notice: str | None,
        attempt_metrics: ParserAttemptMetrics | None = None,
        now: float,
    ) -> None:
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            if attempt_id is not None:
                self._finish_parser_attempt(
                    connection,
                    invocation_id=invocation.invocation_id,
                    attempt_id=attempt_id,
                    outcome=failure_kind,
                    failure_kind=failure_kind,
                    attempt_metrics=attempt_metrics,
                    retry_decision=retry_decision,
                    now=now,
                )
            self._finish_invocation(
                connection,
                invocation_id=invocation.invocation_id,
                status="failed",
                result_kind=None,
                outcome=failure_kind,
                now=now,
            )
            if invocation.spec.source_plan_id is not None:
                source_plan = self._source_plan(connection, invocation.spec.source_plan_id)
                source_status = "partial" if int(source_plan["items_used"]) > 0 else "failed"
                connection.execute(
                    """
                    UPDATE source_plans
                    SET status = ?, terminal_reason = ?, invocations_used = invocations_used + 1
                    WHERE source_plan_id = ?
                    """,
                    (source_status, failure_kind, invocation.spec.source_plan_id),
                )
            self._insert_terminal_event(
                connection,
                invocation=invocation,
                event_type="invocation_terminal",
                payload={
                    "invocationId": invocation.invocation_id,
                    "failureKind": failure_kind,
                    "retryDecision": retry_decision,
                    "publicNotice": public_notice,
                    "eventSchemaVersion": 1,
                },
                now=now,
            )

    def settle_deadline(
        self,
        execution_id: str,
        *,
        now: float,
        selection_evaluator: Callable[[JsonObject], SelectionDecision] | None = None,
    ) -> bool:
        with self._transaction() as connection:
            execution = connection.execute(
                """
                SELECT status, active_runtime_budget_ms, active_runtime_ms,
                       active_session_started_at
                FROM search_executions WHERE execution_id = ?
                """,
                (execution_id,),
            ).fetchone()
            if execution is None:
                raise KeyError(f"unknown execution: {execution_id}")
            if execution["status"] in {
                "stopping",
                "assembling",
                "artifacts_pending",
                "completed",
                "failed",
            }:
                return False
            active_runtime_ms = int(execution["active_runtime_ms"])
            session_started_at = execution["active_session_started_at"]
            if session_started_at is not None:
                active_runtime_ms += max(
                    0,
                    round((now - float(session_started_at)) * 1000),
                )
            if active_runtime_ms < int(execution["active_runtime_budget_ms"]):
                return False
            cancelled_rows = connection.execute(
                """
                SELECT invocation_id FROM parser_invocations
                WHERE execution_id = ? AND status IN ('queued', 'leased', 'waiting')
                """,
                (execution_id,),
            ).fetchall()
            cancelled_ids = tuple(str(row["invocation_id"]) for row in cancelled_rows)
            if cancelled_ids:
                placeholders = ",".join("?" for _ in cancelled_ids)
                connection.execute(
                    f"""
                    UPDATE parser_attempts
                    SET finished_at = ?, outcome = 'cancelled',
                        failure_kind = 'execution_deadline', retry_decision = 'terminal'
                    WHERE finished_at IS NULL AND invocation_id IN ({placeholders})
                    """,
                    (now, *cancelled_ids),
                )
            connection.execute(
                """
                UPDATE parser_invocations
                SET status = 'cancelled', outcome = 'execution_deadline',
                    lease_owner = NULL, lease_token = NULL, lease_until = NULL,
                    finished_at = ?
                WHERE execution_id = ? AND status IN ('queued', 'leased', 'waiting')
                """,
                (now, execution_id),
            )
            connection.execute(
                """
                UPDATE source_plans
                SET status = 'cancelled', terminal_reason = 'execution_deadline'
                WHERE execution_id = ?
                  AND status NOT IN ('succeeded', 'no_results', 'partial', 'limit_reached', 'failed', 'cancelled')
                """,
                (execution_id,),
            )
            self._settle_terminal_dependencies(
                connection,
                execution_id=execution_id,
                terminal_invocation_ids=cancelled_ids,
                affected_listing_ids=None,
                selection_evaluator=selection_evaluator or _keep_selection,
                now=now,
            )
            connection.execute(
                """
                UPDATE search_executions
                SET status = 'stopping', completion_reason = 'deadline',
                    active_runtime_ms = ?, active_session_started_at = NULL,
                    active_heartbeat_at = ?,
                    coordinator_owner = NULL, coordinator_token = NULL,
                    coordinator_lease_until = NULL
                WHERE execution_id = ?
                """,
                (active_runtime_ms, now, execution_id),
            )
        return True

    def acquire_coordinator(
        self,
        *,
        execution_id: str,
        owner_id: str,
        lease_seconds: float,
        now: float,
    ) -> ExecutionCoordinatorLease | None:
        _require_text(owner_id, "owner_id")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        token = uuid4().hex
        lease_until = now + lease_seconds
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT coordinator_owner, coordinator_lease_until
                FROM search_executions
                WHERE execution_id = ?
                """,
                (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown execution: {execution_id}")
            current_owner = row["coordinator_owner"]
            current_until = row["coordinator_lease_until"]
            if (
                current_owner is not None
                and current_owner != owner_id
                and current_until is not None
                and float(current_until) > now
            ):
                return None
            connection.execute(
                """
                UPDATE search_executions
                SET coordinator_owner = ?, coordinator_token = ?, coordinator_lease_until = ?
                WHERE execution_id = ?
                """,
                (owner_id, token, lease_until, execution_id),
            )
        return ExecutionCoordinatorLease(execution_id, owner_id, token, lease_until)

    def has_unprocessed_events(self, execution_id: str) -> bool:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                """
                SELECT 1 FROM domain_events
                WHERE execution_id = ? AND processed_at IS NULL
                LIMIT 1
                """,
                (execution_id,),
            ).fetchone()
        return row is not None

    def read_unprocessed_events(self, execution_id: str, *, limit: int) -> tuple[StoredDomainEvent, ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT
                    event.*,
                    COALESCE(
                        (
                            SELECT json_group_array(consumer.listing_id)
                            FROM (
                                SELECT DISTINCT enrichment.listing_id
                                FROM listing_enrichment_requests AS enrichment
                                WHERE enrichment.execution_id = event.execution_id
                                  AND enrichment.invocation_id = event.producer_invocation_id
                                  AND enrichment.status = 'waiting'
                                ORDER BY enrichment.listing_id
                                LIMIT ?
                            ) AS consumer
                        ),
                        '[]'
                    ) AS affected_listing_ids_json
                FROM domain_events AS event
                WHERE event.execution_id = ? AND event.processed_at IS NULL
                ORDER BY event.occurred_at, event.event_id
                LIMIT ?
                """,
                (
                    _MAX_COORDINATOR_LISTINGS + 1,
                    execution_id,
                    min(limit, _MAX_COORDINATOR_EVENTS),
                ),
            ).fetchall()
            return self._bounded_event_batch(rows)

    def _bounded_event_batch(
        self,
        rows: list[sqlite3.Row],
    ) -> tuple[StoredDomainEvent, ...]:
        remaining = _MAX_COORDINATOR_LISTINGS
        events: list[StoredDomainEvent] = []
        for row in rows:
            if remaining == 0:
                break
            event = _stored_event(row)
            offset = int(row["processing_offset"])
            if event.event_type == "listing_observations_stored":
                raw_ids = event.payload.get("observationIds")
                if not isinstance(raw_ids, list) or any(
                    not isinstance(item, str) for item in raw_ids
                ):
                    raise ValueError("listing event contains invalid observation ids")
                selected_ids = raw_ids[offset : offset + remaining]
                payload = dict(event.payload)
                payload["observationIds"] = selected_ids
                advance = len(selected_ids)
                events.append(
                    replace(
                        event,
                        payload=payload,
                        processing_advance=advance,
                        processing_complete=offset + advance >= len(raw_ids),
                    )
                )
                remaining -= advance
                continue
            raw_affected_ids = json.loads(str(row["affected_listing_ids_json"]))
            if not isinstance(raw_affected_ids, list) or any(
                not isinstance(item, str) for item in raw_affected_ids
            ):
                raise ValueError("event consumer query returned invalid listing ids")
            affected_ids = tuple(raw_affected_ids[:remaining])
            events.append(
                replace(
                    event,
                    processing_advance=len(affected_ids),
                    processing_complete=len(raw_affected_ids) <= remaining,
                    affected_listing_ids=affected_ids,
                )
            )
            remaining -= len(affected_ids)
        return tuple(events)

    def mark_events_processed(
        self,
        coordinator: ExecutionCoordinatorLease,
        event_ids: tuple[str, ...],
        *,
        now: float,
    ) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT coordinator_owner, coordinator_token, coordinator_lease_until
                FROM search_executions WHERE execution_id = ?
                """,
                (coordinator.execution_id,),
            ).fetchone()
            if (
                row is None
                or row["coordinator_owner"] != coordinator.owner_id
                or row["coordinator_token"] != coordinator.token
                or row["coordinator_lease_until"] is None
                or float(row["coordinator_lease_until"]) < now
            ):
                raise StaleLeaseError("execution coordinator lease is stale")
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                connection.execute(
                    f"""
                    UPDATE domain_events SET processed_at = ?
                    WHERE execution_id = ? AND event_id IN ({placeholders}) AND processed_at IS NULL
                    """,
                    (now, coordinator.execution_id, *event_ids),
                )

    def process_listing_event_batch(  # noqa: PLR0912, PLR0915 - one atomic event-batch transaction
        self,
        coordinator: ExecutionCoordinatorLease,
        events: tuple[StoredDomainEvent, ...],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        preliminary_selection_evaluator: Callable[[JsonObject], SelectionDecision],
        final_selection_evaluator: Callable[[JsonObject], SelectionDecision],
        score_evaluator: Callable[[JsonObject], float],
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
        target_resolver: _TargetResolver,
        discovered_planner: Callable[
            [ParserRef, JsonObject], tuple[SearchListingInput, ...]
        ],
        discovered_requirement_planner: _DiscoveredRequirementPlanner,
        *,
        requirement_scope: Literal["all", "required", "optional"] = "all",
        optional_execution_id: str | None = None,
        discovery_execution_id: str | None = None,
        now: float,
    ) -> None:
        observation_events: dict[str, StoredDomainEvent] = {}
        detail_events: dict[str, StoredDomainEvent] = {}
        profile_events: dict[str, StoredDomainEvent] = {}
        site_events: dict[str, StoredDomainEvent] = {}
        terminal_events: dict[str, StoredDomainEvent] = {}
        for event in events:
            if event.event_type == "invocation_terminal":
                if event.producer_invocation_id is None:
                    raise ValueError("terminal event is missing producer invocation")
                terminal_events[event.producer_invocation_id] = event
                continue
            if event.event_type == "vacancy_detail_observation_stored":
                if event.producer_invocation_id is None:
                    raise ValueError("detail event is missing producer invocation")
                detail_events[event.producer_invocation_id] = event
                continue
            if event.event_type == "company_profile_observation_stored":
                if event.producer_invocation_id is None:
                    raise ValueError("profile event is missing producer invocation")
                profile_events[event.producer_invocation_id] = event
                continue
            if event.event_type == "company_site_observation_stored":
                if event.producer_invocation_id is None:
                    raise ValueError("site event is missing producer invocation")
                site_events[event.producer_invocation_id] = event
                continue
            if event.event_type != "listing_observations_stored":
                continue
            raw_ids = event.payload.get("observationIds")
            if not isinstance(raw_ids, list) or any(not isinstance(item, str) for item in raw_ids):
                raise ValueError("listing event contains invalid observation ids")
            for observation_id in raw_ids:
                observation_events[observation_id] = event

        with self._transaction() as connection:
            self._assert_coordinator(connection, coordinator, now)
            latest_by_listing = self._latest_listing_observations(
                connection,
                tuple(observation_events),
            )
            detail_consumers = self._bounded_consumer_rows(
                self._detail_consumer_rows(connection, tuple(detail_events)),
                detail_events,
                invocation_column="detail_invocation_id",
            )
            profile_consumers = self._bounded_consumer_rows(
                self._profile_consumer_rows(connection, tuple(profile_events)),
                profile_events,
                invocation_column="provider_invocation_id",
            )
            site_consumers = self._bounded_consumer_rows(
                self._site_consumer_rows(connection, tuple(site_events)),
                site_events,
                invocation_column="provider_invocation_id",
            )
            source_plan_ids = {
                str(row["source_plan_id"])
                for row in (
                    *latest_by_listing.values(),
                    *detail_consumers,
                    *profile_consumers,
                    *site_consumers,
                )
            }
            all_requirements = self._requirements_for_source_plans(
                connection,
                tuple(source_plan_ids),
            )
            requirements = self._filter_requirements_by_scope(
                all_requirements,
                requirement_scope,
            )
            affected_listing_ids = tuple(
                dict.fromkeys(
                    str(row["listing_id"])
                    for row in (
                        *latest_by_listing.values(),
                        *detail_consumers,
                        *profile_consumers,
                        *site_consumers,
                    )
                )
            )
            current_snapshots = self._current_fact_snapshots(
                connection,
                execution_id=coordinator.execution_id,
                listing_ids=affected_listing_ids,
            )
            enrichment_states = self._enrichment_states(
                connection,
                execution_id=coordinator.execution_id,
                listing_ids=affected_listing_ids,
            )
            for row in latest_by_listing.values():
                event = observation_events[str(row["listing_observation_id"])]
                listing_id = str(row["listing_id"])
                self._materialize_and_schedule_listing(
                    connection,
                    row=row,
                    event=event,
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    current_snapshots=current_snapshots,
                    enrichment_state=enrichment_states.setdefault(listing_id, {}),
                    manifest_resolver=manifest_resolver,
                    target_resolver=target_resolver,
                    preliminary_selection_evaluator=preliminary_selection_evaluator,
                    final_selection_evaluator=final_selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            completed_provider_ids, failed_provider_ids = self._terminal_waiting_provider_invocations(
                connection,
                execution_id=coordinator.execution_id,
                listing_ids=tuple(latest_by_listing),
            )
            for event in self._provider_observation_events(connection, completed_provider_ids):
                producer_invocation_id = event.producer_invocation_id
                if producer_invocation_id is None:
                    raise ValueError("provider observation event is missing producer invocation")
                existing = (
                    detail_events.get(producer_invocation_id)
                    or profile_events.get(producer_invocation_id)
                    or site_events.get(producer_invocation_id)
                )
                affected_ids = tuple(
                    dict.fromkeys(
                        (*(() if existing is None else existing.affected_listing_ids), *latest_by_listing)
                    )
                )
                event = replace(event, affected_listing_ids=affected_ids)
                if event.event_type == "vacancy_detail_observation_stored":
                    detail_events[producer_invocation_id] = event
                elif event.event_type == "company_profile_observation_stored":
                    profile_events[producer_invocation_id] = event
                elif event.event_type == "company_site_observation_stored":
                    site_events[producer_invocation_id] = event
            if latest_by_listing:
                detail_consumers = self._bounded_consumer_rows(
                    self._detail_consumer_rows(connection, tuple(detail_events)),
                    detail_events,
                    invocation_column="detail_invocation_id",
                )
                profile_consumers = self._bounded_consumer_rows(
                    self._profile_consumer_rows(connection, tuple(profile_events)),
                    profile_events,
                    invocation_column="provider_invocation_id",
                )
                site_consumers = self._bounded_consumer_rows(
                    self._site_consumer_rows(connection, tuple(site_events)),
                    site_events,
                    invocation_column="provider_invocation_id",
                )
            waiting_consumers = self._waiting_enrichment_consumers(
                connection,
                execution_id=coordinator.execution_id,
                invocation_ids=tuple(
                    dict.fromkeys(
                        (
                            str(row["detail_invocation_id"])
                            for row in detail_consumers
                        ),
                    )
                )
                + tuple(
                    dict.fromkeys(
                        (
                            str(row["provider_invocation_id"])
                            for row in (*profile_consumers, *site_consumers)
                        ),
                    )
                ),
            )
            for row in detail_consumers:
                event = detail_events[str(row["detail_invocation_id"])]
                listing_id = str(row["listing_id"])
                self._materialize_detail_consumer(
                    connection,
                    row=row,
                    event=event,
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    current_snapshots=current_snapshots,
                    enrichment_state=enrichment_states.setdefault(listing_id, {}),
                    manifest_resolver=manifest_resolver,
                    target_resolver=target_resolver,
                    waiting_consumers=waiting_consumers,
                    selection_evaluator=final_selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            for row in profile_consumers:
                listing_id = str(row["listing_id"])
                self._materialize_company_consumer(
                    connection,
                    row=row,
                    event=profile_events[str(row["provider_invocation_id"])],
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    current_snapshots=current_snapshots,
                    enrichment_state=enrichment_states.setdefault(listing_id, {}),
                    provider_payload_column="profile_payload_json",
                    provider_observation_column="profile_observation_id",
                    evidence_key="profileObservationId",
                    expected_event_type="company_profile_observation_stored",
                    manifest_resolver=manifest_resolver,
                    target_resolver=target_resolver,
                    waiting_consumers=waiting_consumers,
                    selection_evaluator=final_selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            for row in site_consumers:
                listing_id = str(row["listing_id"])
                self._materialize_company_consumer(
                    connection,
                    row=row,
                    event=site_events[str(row["provider_invocation_id"])],
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    current_snapshots=current_snapshots,
                    enrichment_state=enrichment_states.setdefault(listing_id, {}),
                    provider_payload_column="site_payload_json",
                    provider_observation_column="site_observation_id",
                    evidence_key="siteObservationId",
                    expected_event_type="company_site_observation_stored",
                    manifest_resolver=manifest_resolver,
                    target_resolver=target_resolver,
                    waiting_consumers=waiting_consumers,
                    selection_evaluator=final_selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            self._route_discovered_endpoints(
                connection,
                origin_execution_id=coordinator.execution_id,
                target_execution_id=discovery_execution_id or coordinator.execution_id,
                profile_events=profile_events,
                site_events=site_events,
                manifest_resolver=manifest_resolver,
                target_resolver=target_resolver,
                discovered_planner=discovered_planner,
                discovered_requirement_planner=discovered_requirement_planner,
                now=now,
            )
            self._settle_terminal_dependencies(
                connection,
                execution_id=coordinator.execution_id,
                terminal_invocation_ids=tuple({*terminal_events, *failed_provider_ids}),
                affected_listing_ids=tuple(
                    dict.fromkeys(
                        (
                            *latest_by_listing,
                            *(
                                listing_id
                                for event in terminal_events.values()
                                for listing_id in event.affected_listing_ids
                            ),
                        )
                    )
                ),
                selection_evaluator=final_selection_evaluator,
                now=now,
            )
            if requirement_scope == "required":
                self._admit_speculative_optional_batch(
                    connection,
                    parent_execution_id=coordinator.execution_id,
                    child_execution_id=optional_execution_id,
                    score_evaluator=score_evaluator,
                    manifest_resolver=manifest_resolver,
                    target_resolver=target_resolver,
                    now=now,
                )
            self._advance_events(
                connection,
                coordinator.execution_id,
                events,
                now=now,
            )

    def assemble_final(
        self,
        execution_id: str,
        *,
        projector: Callable[[JsonObject], JsonObject],
        scorer: Callable[[JsonObject], float],
        now: float,
    ) -> tuple[JsonObject, ...]:
        with self._transaction() as connection:
            blockers = self._drain_blockers(connection, execution_id, now)
            if blockers:
                raise RuntimeError("execution is not drained: " + ", ".join(blockers))
            connection.execute(
                "UPDATE search_executions SET status = 'assembling' WHERE execution_id = ?",
                (execution_id,),
            )
            rows = connection.execute(
                """
                WITH ranked_final AS (
                    SELECT
                        listing.listing_id,
                        listing.vacancy_id,
                        listing.company_id,
                        listing.source_id,
                        evaluation.evaluation_id,
                        evaluation.outcome,
                        fact_set.materialized_facts_json,
                        fact_set.created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY evaluation.listing_id
                            ORDER BY fact_set.created_at DESC, fact_set.rowid DESC
                        ) AS evaluation_rank
                    FROM selection_evaluations AS evaluation
                    JOIN fact_sets AS fact_set ON fact_set.fact_set_id = evaluation.fact_set_id
                    JOIN vacancy_listings AS listing ON listing.listing_id = evaluation.listing_id
                    WHERE evaluation.execution_id = ?
                      AND evaluation.stage = 'final'
                )
                SELECT
                    listing_id,
                    vacancy_id,
                    company_id,
                    source_id,
                    evaluation_id,
                    materialized_facts_json,
                    created_at
                FROM ranked_final
                WHERE evaluation_rank = 1 AND outcome = 'keep'
                ORDER BY vacancy_id, source_id, listing_id
                """,
                (execution_id,),
            ).fetchall()
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(str(row["vacancy_id"]), []).append(row)

            records: list[tuple[sqlite3.Row, list[sqlite3.Row], JsonObject, str | None]] = []
            scores: dict[str, float] = {}
            for vacancy_id, members in grouped.items():
                representative = max(
                    members,
                    key=lambda member: scorer(_json_object(member["materialized_facts_json"])),
                )
                duplicate_group_id = self._store_exact_duplicate_group(
                    connection,
                    execution_id=execution_id,
                    vacancy_id=vacancy_id,
                    members=members,
                    now=now,
                )
                materialized_facts = _json_object(representative["materialized_facts_json"])
                score = max(0.0, float(scorer(materialized_facts)))
                payload = projector(materialized_facts)
                payload["relevanceScore"] = score
                payload["sourceVariants"] = tuple(str(member["source_id"]) for member in members)
                if duplicate_group_id is not None:
                    payload["duplicateConfidence"] = "exact"
                records.append((representative, members, payload, duplicate_group_id))
                scores[str(representative["listing_id"])] = score

            probable_groups = self._store_probable_duplicate_groups(
                connection,
                execution_id=execution_id,
                records=records,
                now=now,
            )
            items: list[JsonObject] = []
            ordered_records = sorted(
                records,
                key=lambda record: (
                    -scores[str(record[0]["listing_id"])],
                    str(record[0]["vacancy_id"]),
                    str(record[0]["listing_id"]),
                ),
            )
            for representative, _members, payload, exact_group_id in ordered_records:
                listing_id = str(representative["listing_id"])
                score = scores[listing_id]
                duplicate_group_id = exact_group_id or probable_groups.get(listing_id)
                if exact_group_id is None and duplicate_group_id is not None:
                    payload["duplicateConfidence"] = "probable"
                final_vacancy_id = _stable_id(
                    "final-vacancy",
                    execution_id,
                    str(representative["listing_id"]),
                    "1",
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO final_vacancies (
                        final_vacancy_id, execution_id, listing_id, evaluation_id,
                        duplicate_group_id, snapshot_version, score, payload_json
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        final_vacancy_id,
                        execution_id,
                        representative["listing_id"],
                        representative["evaluation_id"],
                        duplicate_group_id,
                        score,
                        _json_dumps(payload),
                    ),
                )
                items.append(payload)
        return tuple(items)

    def prepare_execution_artifacts(
        self,
        execution_id: str,
        *,
        artifacts: tuple[ExecutionArtifact, ...],
        now: float,
    ) -> None:
        if not artifacts:
            raise ValueError("execution must declare at least one artifact")
        names = tuple(artifact.name for artifact in artifacts)
        paths = tuple(artifact.path for artifact in artifacts)
        if len(names) != len(set(names)):
            raise ValueError("artifact names must be unique within an execution")
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique within an execution")
        with self._transaction() as connection:
            execution = connection.execute(
                "SELECT status FROM search_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if execution is None:
                raise KeyError(f"unknown execution: {execution_id}")
            if execution["status"] not in {
                "assembling",
                "artifacts_pending",
                "completed",
            }:
                raise RuntimeError(
                    "execution artifacts require an assembled snapshot: "
                    f"{execution['status']}"
                )
            connection.execute(
                "DELETE FROM execution_artifacts WHERE execution_id = ?",
                (execution_id,),
            )
            connection.executemany(
                """
                INSERT INTO execution_artifacts (
                    execution_id, artifact_name, artifact_path, schema_version,
                    sha256, byte_count, status, prepared_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'expected', ?, NULL)
                """,
                (
                    (
                        execution_id,
                        artifact.name,
                        artifact.path,
                        artifact.schema_version,
                        artifact.sha256,
                        artifact.byte_count,
                        now,
                    )
                    for artifact in artifacts
                ),
            )
            connection.execute(
                """
                UPDATE search_executions
                SET status = 'artifacts_pending'
                WHERE execution_id = ?
                """,
                (execution_id,),
            )

    def complete_execution_artifacts(
        self,
        execution_id: str,
        *,
        verified_artifacts: tuple[ExecutionArtifact, ...],
        now: float,
    ) -> None:
        verified_by_name = {artifact.name: artifact for artifact in verified_artifacts}
        if len(verified_by_name) != len(verified_artifacts):
            raise ValueError("verified artifact names must be unique")
        with self._transaction() as connection:
            execution = connection.execute(
                "SELECT status FROM search_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if execution is None:
                raise KeyError(f"unknown execution: {execution_id}")
            if execution["status"] != "artifacts_pending":
                raise RuntimeError("execution is not awaiting artifact verification")
            expected_rows = connection.execute(
                """
                SELECT artifact_name, artifact_path, schema_version, sha256, byte_count
                FROM execution_artifacts
                WHERE execution_id = ?
                ORDER BY artifact_name
                """,
                (execution_id,),
            ).fetchall()
            if len(expected_rows) != len(verified_by_name):
                raise ValueError("verified artifact set does not match expected artifacts")
            for row in expected_rows:
                name = str(row["artifact_name"])
                actual = verified_by_name.get(name)
                if actual is None:
                    raise ValueError(f"missing verified artifact: {name}")
                if actual.sha256 != row["sha256"]:
                    raise ValueError(f"artifact digest mismatch: {name}")
                if (
                    actual.path != row["artifact_path"]
                    or actual.schema_version != row["schema_version"]
                    or actual.byte_count != row["byte_count"]
                ):
                    raise ValueError(f"artifact metadata mismatch: {name}")
            connection.execute(
                """
                UPDATE execution_artifacts
                SET status = 'verified', verified_at = ?
                WHERE execution_id = ?
                """,
                (now, execution_id),
            )
            connection.execute(
                """
                UPDATE search_executions
                SET status = 'completed',
                    completion_reason = COALESCE(completion_reason, 'drained')
                WHERE execution_id = ?
                """,
                (execution_id,),
            )

    def _admit_optional_listing(
        self,
        connection: sqlite3.Connection,
        *,
        parent_execution_id: str,
        child_execution_id: str,
        listing_id: str,
        facts: JsonObject,
        evidence_refs: JsonObject,
        parent_fact_set_id: str,
        source_plan_id: str,
        parent_invocation_id: str,
        event: StoredDomainEvent,
        requirements: tuple[sqlite3.Row, ...],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        now: float,
    ) -> None:
        evidence_refs = {
            **evidence_refs,
            "parentExecutionId": parent_execution_id,
            "parentFactSetId": parent_fact_set_id,
            "sourcePlanId": source_plan_id,
        }
        fingerprint = _fingerprint(facts)
        fact_set_id = _stable_id(
            "fact-set",
            child_execution_id,
            listing_id,
            fingerprint,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fact_sets (
                fact_set_id, execution_id, listing_id, evidence_refs_json,
                materialized_facts_json, fingerprint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_set_id,
                child_execution_id,
                listing_id,
                _json_dumps(evidence_refs),
                _json_dumps(facts),
                fingerprint,
                now,
            ),
        )
        missing = tuple(
            requirement
            for requirement in requirements
            if not _comparison_matches(
                _fact_at_path(facts, str(requirement["required_fact_path"])),
                _json_object(requirement["comparison_json"]),
            )
        )
        self._insert_evaluation(
            connection,
            execution_id=child_execution_id,
            listing_id=listing_id,
            fact_set_id=fact_set_id,
            stage="preliminary" if missing else "final",
            outcome="enrich" if missing else "keep",
            reasons=("optional_enrichment_admitted",),
        )
        enrichment_state: dict[str, tuple[str, bool, str | None]] = {}
        self._schedule_missing_fact_providers(
            connection,
            execution_id=child_execution_id,
            source_execution_id=parent_execution_id,
            listing_id=listing_id,
            parent_invocation_id=parent_invocation_id,
            event=event,
            facts=facts,
            missing=missing,
            enrichment_state=enrichment_state,
            manifest_resolver=manifest_resolver,
            target_resolver=target_resolver,
            now=now,
        )
        self._finalize_if_all_dependencies_terminal(
            connection,
            execution_id=child_execution_id,
            listing_id=listing_id,
            fact_set_id=fact_set_id,
            facts=facts,
            enrichment_state=enrichment_state,
            selection_evaluator=_keep_selection,
        )

    def _admit_speculative_optional_batch(
        self,
        connection: sqlite3.Connection,
        *,
        parent_execution_id: str,
        child_execution_id: str | None,
        score_evaluator: Callable[[JsonObject], float],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        now: float,
    ) -> None:
        if child_execution_id is None:
            return
        child = connection.execute(
            """
            SELECT speculative_admission_budget, speculative_admissions_created
            FROM search_executions
            WHERE execution_id = ?
              AND execution_kind = 'enrichment'
              AND parent_execution_id = ?
              AND status = 'running'
            """,
            (child_execution_id, parent_execution_id),
        ).fetchone()
        if child is None:
            return
        budget = min(
            int(child["speculative_admission_budget"]),
            _MAX_SPECULATIVE_ADMISSIONS,
        )
        remaining = budget - int(child["speculative_admissions_created"])
        if remaining <= 0:
            return
        rows = connection.execute(
            """
            WITH latest_fact AS (
                SELECT
                    fact_set.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY fact_set.listing_id
                        ORDER BY fact_set.created_at DESC, fact_set.rowid DESC
                    ) AS newest_rank
                FROM fact_sets AS fact_set
                WHERE fact_set.execution_id = ?
            ),
            latest_observation AS (
                SELECT
                    observation.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY observation.listing_id
                        ORDER BY observation.observed_at DESC, observation.rowid DESC
                    ) AS newest_rank
                FROM listing_observations AS observation
                WHERE observation.execution_id = ?
            )
            SELECT
                fact.fact_set_id,
                fact.listing_id,
                listing.vacancy_id,
                listing.source_id,
                fact.materialized_facts_json,
                fact.evidence_refs_json,
                observation.source_plan_id,
                observation.invocation_id,
                event.event_id,
                event.event_type,
                event.schema_version,
                event.payload_json,
                event.occurred_at
            FROM latest_fact AS fact
            JOIN selection_evaluations AS evaluation
              ON evaluation.fact_set_id = fact.fact_set_id
             AND evaluation.execution_id = fact.execution_id
             AND evaluation.listing_id = fact.listing_id
             AND evaluation.stage = 'final'
             AND evaluation.outcome = 'keep'
            JOIN vacancy_listings AS listing
              ON listing.listing_id = fact.listing_id
            JOIN latest_observation AS observation
              ON observation.listing_id = fact.listing_id
             AND observation.newest_rank = 1
            JOIN domain_events AS event
              ON event.execution_id = observation.execution_id
             AND event.producer_invocation_id = observation.invocation_id
             AND event.event_type = 'listing_observations_stored'
            WHERE fact.newest_rank = 1
            ORDER BY fact.listing_id
            """,
            (parent_execution_id, parent_execution_id),
        ).fetchall()
        if not rows:
            return
        source_plan_ids = tuple(
            dict.fromkeys(str(row["source_plan_id"]) for row in rows)
        )
        optional_requirements = self._filter_requirements_by_scope(
            self._requirements_for_source_plans(connection, source_plan_ids),
            "optional",
        )
        admitted, admissions_by_source = self._speculative_admission_state(
            connection,
            child_execution_id,
        )
        ranked: list[tuple[float, str, str, sqlite3.Row, JsonObject]] = []
        for row in rows:
            source_plan_id = str(row["source_plan_id"])
            if not optional_requirements.get(source_plan_id):
                continue
            facts = _json_object(row["materialized_facts_json"])
            ranked.append(
                (
                    score_evaluator(facts),
                    str(row["vacancy_id"]),
                    str(row["listing_id"]),
                    row,
                    facts,
                )
            )
        ranked.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2]))
        created = 0
        for _score, _vacancy_id, listing_id, row, facts in ranked:
            if created >= remaining:
                break
            if listing_id in admitted:
                continue
            source_id = str(row["source_id"])
            if (
                admissions_by_source.get(source_id, 0)
                >= _MAX_SPECULATIVE_ADMISSIONS_PER_SOURCE
            ):
                continue
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO enrichment_admissions (
                    child_execution_id, parent_execution_id, listing_id,
                    admission_kind, created_at
                ) VALUES (?, ?, ?, 'speculative', ?)
                """,
                (child_execution_id, parent_execution_id, listing_id, now),
            )
            if not inserted.rowcount:
                continue
            created += 1
            admissions_by_source[source_id] = admissions_by_source.get(source_id, 0) + 1
            source_plan_id = str(row["source_plan_id"])
            self._admit_optional_listing(
                connection,
                parent_execution_id=parent_execution_id,
                child_execution_id=child_execution_id,
                listing_id=listing_id,
                facts=facts,
                evidence_refs=_json_object(row["evidence_refs_json"]),
                parent_fact_set_id=str(row["fact_set_id"]),
                source_plan_id=source_plan_id,
                parent_invocation_id=str(row["invocation_id"]),
                event=StoredDomainEvent(
                    event_id=str(row["event_id"]),
                    execution_id=parent_execution_id,
                    producer_invocation_id=str(row["invocation_id"]),
                    event_type=str(row["event_type"]),
                    schema_version=int(row["schema_version"]),
                    payload=_json_object(row["payload_json"]),
                    occurred_at=float(row["occurred_at"]),
                ),
                requirements=optional_requirements[source_plan_id],
                manifest_resolver=manifest_resolver,
                target_resolver=target_resolver,
                now=now,
            )
        if created:
            connection.execute(
                """
                UPDATE search_executions
                SET speculative_admissions_created = speculative_admissions_created + ?
                WHERE execution_id = ?
                """,
                (created, child_execution_id),
            )

    @staticmethod
    def _speculative_admission_state(
        connection: sqlite3.Connection,
        child_execution_id: str,
    ) -> tuple[set[str], dict[str, int]]:
        rows = connection.execute(
            """
            SELECT admission.listing_id, listing.source_id
            FROM enrichment_admissions AS admission
            JOIN vacancy_listings AS listing
              ON listing.listing_id = admission.listing_id
            WHERE admission.child_execution_id = ?
            """,
            (child_execution_id,),
        ).fetchall()
        admitted = {str(row["listing_id"]) for row in rows}
        admissions_by_source: dict[str, int] = {}
        for row in rows:
            source_id = str(row["source_id"])
            admissions_by_source[source_id] = admissions_by_source.get(source_id, 0) + 1
        return admitted, admissions_by_source

    @staticmethod
    def _store_probable_duplicate_groups(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        records: list[tuple[sqlite3.Row, list[sqlite3.Row], JsonObject, str | None]],
        now: float,
    ) -> dict[str, str]:
        candidates: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        for representative, _members, payload, exact_group_id in records:
            company_id = representative["company_id"]
            if exact_group_id is not None or not isinstance(company_id, str):
                continue
            title = _normalized_duplicate_text(payload.get("title"))
            location = _normalized_duplicate_location(payload.get("location"))
            if not title:
                continue
            candidates.setdefault((company_id, title, location), []).append(representative)

        membership: dict[str, str] = {}
        for (company_id, title, location), members in candidates.items():
            if len(members) < _MIN_DUPLICATE_MEMBERS:
                continue
            group_id = _stable_id(
                "duplicate-group",
                execution_id,
                "probable",
                company_id,
                title,
                location,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO vacancy_duplicate_groups (
                    duplicate_group_id, execution_id, confidence,
                    evidence_json, policy_version, created_at
                ) VALUES (?, ?, 'probable', ?, 'identity-v1', ?)
                """,
                (
                    group_id,
                    execution_id,
                    _json_dumps(
                        {
                            "companyId": company_id,
                            "normalizedTitle": title,
                            "normalizedLocation": location or None,
                            "reason": "same_strong_company_title_location",
                        }
                    ),
                    now,
                ),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO vacancy_duplicate_members (
                    duplicate_group_id, listing_id, member_role
                ) VALUES (?, ?, ?)
                """,
                (
                    (
                        group_id,
                        member["listing_id"],
                        "representative" if index == 0 else "variant",
                    )
                    for index, member in enumerate(members)
                ),
            )
            membership.update({str(member["listing_id"]): group_id for member in members})
        return membership

    @staticmethod
    def _store_exact_duplicate_group(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        vacancy_id: str,
        members: list[sqlite3.Row],
        now: float,
    ) -> str | None:
        if len(members) < _MIN_DUPLICATE_MEMBERS:
            return None
        duplicate_group_id = _stable_id("duplicate-group", execution_id, "exact", vacancy_id)
        connection.execute(
            """
            INSERT OR IGNORE INTO vacancy_duplicate_groups (
                duplicate_group_id, execution_id, confidence,
                evidence_json, policy_version, created_at
            ) VALUES (?, ?, 'exact', ?, 'identity-v1', ?)
            """,
            (
                duplicate_group_id,
                execution_id,
                _json_dumps({"vacancyId": vacancy_id, "reason": "shared_vacancy_resource"}),
                now,
            ),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO vacancy_duplicate_members (
                duplicate_group_id, listing_id, member_role
            ) VALUES (?, ?, ?)
            """,
            (
                (
                    duplicate_group_id,
                    member["listing_id"],
                    "representative" if index == 0 else "variant",
                )
                for index, member in enumerate(members)
            ),
        )
        return duplicate_group_id

    @staticmethod
    def _drain_blockers(
        connection: sqlite3.Connection,
        execution_id: str,
        now: float,
    ) -> tuple[str, ...]:
        execution = connection.execute(
            "SELECT status FROM search_executions WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if execution is None:
            raise KeyError(f"unknown execution: {execution_id}")
        blockers: list[str] = []
        active_invocations = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM parser_invocations
                WHERE execution_id = ?
                  AND (
                      status IN ('queued', 'waiting')
                      OR (status = 'leased' AND lease_until > ?)
                  )
                """,
                (execution_id, now),
            ).fetchone()[0]
        )
        if active_invocations:
            blockers.append("active_invocations")
        if connection.execute(
            "SELECT 1 FROM domain_events WHERE execution_id = ? AND processed_at IS NULL LIMIT 1",
            (execution_id,),
        ).fetchone():
            blockers.append("unprocessed_events")
        if connection.execute(
            "SELECT 1 FROM listing_enrichment_requests WHERE execution_id = ? AND status = 'waiting' LIMIT 1",
            (execution_id,),
        ).fetchone():
            blockers.append("waiting_dependencies")
        terminal_statuses = ("succeeded", "no_results", "partial", "limit_reached", "failed", "cancelled")
        placeholders = ",".join("?" for _ in terminal_statuses)
        if connection.execute(
            f"""
            SELECT 1 FROM source_plans
            WHERE execution_id = ? AND status NOT IN ({placeholders})
            LIMIT 1
            """,
            (execution_id, *terminal_statuses),
        ).fetchone():
            blockers.append("nonterminal_source_plans")
        return tuple(blockers)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def execution_diagnostics(self, execution_id: str) -> JsonObject:
        with self._lock:
            self._ensure_open()
            execution = self._connection.execute(
                """
                SELECT
                    status,
                    completion_reason,
                    (SELECT COUNT(*) FROM listing_observations WHERE execution_id = ?) AS observations,
                    (SELECT COUNT(*) FROM final_vacancies WHERE execution_id = ?) AS results
                FROM search_executions
                WHERE execution_id = ?
                """,
                (execution_id, execution_id, execution_id),
            ).fetchone()
            if execution is None:
                raise KeyError(f"unknown execution: {execution_id}")
            source_plans = self._connection.execute(
                """
                SELECT
                    plan.source_id, plan.status, plan.terminal_reason,
                    plan.units_used, plan.unit_budget,
                    plan.items_used, plan.item_budget,
                    plan.invocations_used, plan.invocation_budget,
                    COALESCE(
                        1000.0 * (
                            MAX(invocation.finished_at) - MIN(invocation.created_at)
                        ),
                        0.0
                    ) AS elapsed_ms
                FROM source_plans AS plan
                LEFT JOIN parser_invocations AS invocation
                    ON invocation.source_plan_id = plan.source_plan_id
                WHERE plan.execution_id = ?
                GROUP BY plan.source_plan_id
                ORDER BY plan.source_id, plan.source_plan_id
                """,
                (execution_id,),
            ).fetchall()
            invocation_counts = self._connection.execute(
                """
                SELECT status, outcome, COUNT(*) AS count
                FROM parser_invocations
                WHERE execution_id = ?
                GROUP BY status, outcome
                ORDER BY status, outcome
                """,
                (execution_id,),
            ).fetchall()
            failure_counts = self._connection.execute(
                """
                SELECT attempt.failure_kind, COUNT(*) AS count
                FROM parser_attempts AS attempt
                JOIN parser_invocations AS invocation
                    ON invocation.invocation_id = attempt.invocation_id
                WHERE invocation.execution_id = ? AND attempt.failure_kind IS NOT NULL
                GROUP BY attempt.failure_kind
                ORDER BY attempt.failure_kind
                """,
                (execution_id,),
            ).fetchall()
            required_enrichment_failures = int(
                self._connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM listing_enrichment_requests
                    WHERE execution_id = ? AND required = 1 AND status = 'terminal'
                      AND COALESCE(resolution_outcome, '') != 'satisfied'
                    """,
                    (execution_id,),
                ).fetchone()[0]
            )
        status_counts: dict[str, int] = {}
        outcome_counts: dict[str, int] = {}
        for row in invocation_counts:
            status = str(row["status"])
            count = int(row["count"])
            status_counts[status] = status_counts.get(status, 0) + count
            if row["outcome"] is not None:
                outcome = str(row["outcome"])
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + count
        source_status_counts: dict[str, int] = {}
        for row in source_plans:
            status = str(row["status"])
            source_status_counts[status] = source_status_counts.get(status, 0) + 1
        execution_quality, source_coverage = _execution_quality_and_coverage(
            source_status_counts,
            observation_count=int(execution["observations"]),
            required_enrichment_failures=required_enrichment_failures,
        )
        return {
            "execution_status": str(execution["status"]),
            "execution_quality": execution_quality,
            "completion_reason": _optional_text(execution["completion_reason"]),
            "listing_observation_count": int(execution["observations"]),
            "result_count": int(execution["results"]),
            "source_coverage": source_coverage,
            "required_enrichment_failures": required_enrichment_failures,
            "source_plans": [
                {
                    "source_id": str(row["source_id"]),
                    "status": str(row["status"]),
                    "terminal_reason": _optional_text(row["terminal_reason"]),
                    "units": {"used": int(row["units_used"]), "budget": int(row["unit_budget"])},
                    "items": {"used": int(row["items_used"]), "budget": int(row["item_budget"])},
                    "invocations": {
                        "used": int(row["invocations_used"]),
                        "budget": int(row["invocation_budget"]),
                    },
                    "elapsed_ms": round(float(row["elapsed_ms"])),
                }
                for row in source_plans
            ],
            "invocations": {
                "total": sum(status_counts.values()),
                "status_counts": status_counts,
                "outcome_counts": outcome_counts,
                "failure_kind_counts": {
                    str(row["failure_kind"]): int(row["count"])
                    for row in failure_counts
                },
            },
        }

    def project_filtered_vacancies(
        self,
        execution_id: str,
        *,
        projector: Callable[[JsonObject], JsonObject],
    ) -> tuple[JsonObject, ...]:
        with self._lock:
            self._ensure_open()
            rows = _filtered_vacancy_rows(self._connection, execution_id)
        return _project_filtered_vacancy_rows(rows, projector)

    def _enqueue_invocation(self, connection: sqlite3.Connection, spec: ParserInvocationSpec) -> str:
        invocation_id = _stable_id("invocation", spec.execution_id, spec.task_key)
        connection.execute(
            """
            INSERT OR IGNORE INTO parser_invocations (
                invocation_id,
                execution_id,
                source_plan_id,
                parent_invocation_id,
                cause_event_id,
                task_key,
                parser_id,
                parser_version,
                parser_type,
                input_schema_id,
                input_json,
                task_class,
                resource_key,
                resource_key_resolved,
                reserved_collection_units,
                status,
                available_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                invocation_id,
                spec.execution_id,
                spec.source_plan_id,
                spec.parent_invocation_id,
                spec.cause_event_id,
                spec.task_key,
                spec.parser_ref.parser_id,
                spec.parser_ref.implementation_version,
                spec.parser_type.value,
                spec.input_schema_id,
                _json_dumps(spec.parser_input),
                spec.task_class.value,
                spec.resource_key,
                int(spec.resource_key_resolved),
                spec.reserved_collection_units,
                spec.available_at,
                spec.available_at,
            ),
        )
        return invocation_id

    @staticmethod
    def _finish_invocation(
        connection: sqlite3.Connection,
        *,
        invocation_id: str,
        status: str,
        result_kind: str | None,
        outcome: str,
        now: float,
    ) -> None:
        connection.execute(
            """
            UPDATE parser_invocations
            SET
                status = ?, result_kind = ?, outcome = ?,
                lease_owner = NULL, lease_token = NULL, lease_until = NULL,
                finished_at = ?
            WHERE invocation_id = ?
            """,
            (status, result_kind, outcome, now, invocation_id),
        )

    @classmethod
    def _finish_parser_attempt_success(
        cls,
        connection: sqlite3.Connection,
        invocation: LeasedParserInvocation,
        attempt_id: str,
        attempt_metrics: ParserAttemptMetrics | None,
        now: float,
    ) -> None:
        cls._finish_parser_attempt(
            connection,
            invocation_id=invocation.invocation_id,
            attempt_id=attempt_id,
            outcome="success",
            failure_kind=None,
            attempt_metrics=attempt_metrics,
            now=now,
        )

    @staticmethod
    def _finish_parser_attempt(
        connection: sqlite3.Connection,
        *,
        invocation_id: str,
        attempt_id: str,
        outcome: str,
        failure_kind: str | None,
        attempt_metrics: ParserAttemptMetrics | None,
        retry_decision: str | None = None,
        retry_delay_ms: int = 0,
        now: float,
    ) -> None:
        metrics = attempt_metrics or ParserAttemptMetrics()
        cursor = connection.execute(
            """
            UPDATE parser_attempts
            SET finished_at = ?, outcome = ?, failure_kind = ?,
                network_action_count = ?, network_elapsed_ms = ?,
                last_status_code = ?, last_error_class = ?,
                retry_decision = ?, retry_delay_ms = ?
            WHERE parser_attempt_id = ? AND invocation_id = ? AND finished_at IS NULL
            """,
            (
                now,
                outcome,
                failure_kind,
                metrics.network_action_count,
                metrics.network_elapsed_ms,
                metrics.last_status_code,
                metrics.last_error_class,
                retry_decision,
                retry_delay_ms,
                attempt_id,
                invocation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("parser attempt is missing or already terminal")

    @staticmethod
    def _resolve_company_ids(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        groups: tuple[tuple[str | None, tuple[tuple[str, str], ...]], ...],
        now: float,
    ) -> tuple[str | None, ...]:
        claim_keys = tuple(dict.fromkeys(claim for _, claims in groups for claim in claims))
        existing: dict[tuple[str, str], str] = {}
        for offset in range(0, len(claim_keys), 200):
            chunk = claim_keys[offset : offset + 200]
            predicates = " OR ".join("(claim_type = ? AND claim_value = ?)" for _ in chunk)
            parameters: list[object] = [run_id]
            for claim_type, claim_value in chunk:
                parameters.extend((claim_type, claim_value))
            rows = connection.execute(
                f"""
                SELECT claim_type, claim_value, company_id
                FROM company_identity_claims
                WHERE run_id = ? AND status = 'active' AND ({predicates})
                """,
                parameters,
            ).fetchall()
            existing.update(
                {
                    (str(row["claim_type"]), str(row["claim_value"])): str(row["company_id"])
                    for row in rows
                }
            )

        company_rows: dict[str, tuple[object, ...]] = {}
        resolved: list[str | None] = []
        for display_name, claims in groups:
            if not claims:
                resolved.append(None)
                continue
            existing_ids = tuple(dict.fromkeys(existing[claim] for claim in claims if claim in existing))
            if existing_ids:
                company_id = existing_ids[0]
                for merged_id in existing_ids[1:]:
                    SqliteGraphRepository._merge_company_identity(
                        connection,
                        canonical_company_id=company_id,
                        merged_company_id=merged_id,
                        now=now,
                    )
            else:
                primary_type, primary_value = claims[0]
                company_id = _stable_id("company", run_id, primary_type, primary_value)
            company_rows[company_id] = (company_id, run_id, display_name, now)
            for claim in claims:
                existing[claim] = company_id
            resolved.append(company_id)

        connection.executemany(
            """
            INSERT INTO companies (company_id, run_id, display_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET display_name = excluded.display_name
            WHERE companies.display_name IS NULL AND excluded.display_name IS NOT NULL
            """,
            company_rows.values(),
        )
        return tuple(resolved)

    @staticmethod
    def _merge_company_identity(
        connection: sqlite3.Connection,
        *,
        canonical_company_id: str,
        merged_company_id: str,
        now: float,
    ) -> None:
        if canonical_company_id == merged_company_id:
            return
        connection.execute(
            """
            INSERT OR IGNORE INTO company_merges (
                company_merge_id, from_company_id, into_company_id,
                reason, policy_version, created_at
            ) VALUES (?, ?, ?, 'shared_strong_claim', 'identity-v1', ?)
            """,
            (
                _stable_id("company-merge", merged_company_id, canonical_company_id),
                merged_company_id,
                canonical_company_id,
                now,
            ),
        )
        for table in (
            "company_identity_claims",
            "vacancy_listings",
            "company_profile_observations",
            "company_site_observations",
            "discovered_endpoints",
        ):
            connection.execute(
                f"UPDATE {table} SET company_id = ? WHERE company_id = ?",
                (canonical_company_id, merged_company_id),
            )

    @staticmethod
    def _store_discovered_endpoints(
        connection: sqlite3.Connection,
        *,
        company_id: str,
        profile_observation_id: str | None,
        site_observation_id: str | None,
        endpoints: tuple[object, ...],
    ) -> None:
        rows: list[tuple[object, ...]] = []
        for endpoint in endpoints:
            payload = to_jsonable(endpoint)
            if not isinstance(payload, dict):
                raise ValueError("discovered endpoint must serialize to an object")
            normalized_url = _normalize_url(_required_json_text(payload, "url"))
            origin_observation_id = profile_observation_id or site_observation_id
            if origin_observation_id is None:
                raise ValueError("discovered endpoint requires an originating observation")
            origin_key = f"{origin_observation_id}:{payload.get('kind')}:{normalized_url}"
            rows.append(
                (
                    _stable_id("endpoint", origin_key),
                    company_id,
                    profile_observation_id,
                    site_observation_id,
                    origin_key,
                    payload.get("kind"),
                    normalized_url,
                    payload.get("provider_hint"),
                    payload.get("confidence"),
                    payload.get("discovery_method"),
                    "unresolved",
                )
            )
        connection.executemany(
            """
            INSERT OR IGNORE INTO discovered_endpoints (
                endpoint_id, company_id, profile_observation_id, site_observation_id,
                origin_key, kind, normalized_url, provider_hint, confidence,
                discovery_method, resolution_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    @staticmethod
    def _insert_terminal_event(
        connection: sqlite3.Connection,
        *,
        invocation: LeasedParserInvocation,
        event_type: str,
        payload: JsonObject,
        now: float,
    ) -> None:
        event_id = _stable_id("event", invocation.invocation_id, event_type)
        connection.execute(
            """
            INSERT OR IGNORE INTO domain_events (
                event_id, execution_id, producer_invocation_id, event_key,
                event_type, schema_version, payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                event_id,
                invocation.spec.execution_id,
                invocation.invocation_id,
                f"{event_type}:{invocation.invocation_id}",
                event_type,
                _json_dumps(payload),
                now,
            ),
        )

    @staticmethod
    def _assert_current_lease(
        connection: sqlite3.Connection,
        invocation: LeasedParserInvocation,
        now: float,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT invocation.*, execution.status AS execution_status,
                   execution.active_runtime_budget_ms AS execution_runtime_budget_ms,
                   execution.active_runtime_ms AS execution_runtime_ms,
                   execution.active_session_started_at AS execution_session_started_at
            FROM parser_invocations AS invocation
            JOIN search_executions AS execution
              ON execution.execution_id = invocation.execution_id
            WHERE invocation.invocation_id = ?
            """,
            (invocation.invocation_id,),
        ).fetchone()
        if (
            row is None
            or row["status"] != "leased"
            or row["lease_owner"] != invocation.lease_owner
            or row["lease_token"] != invocation.lease_token
            or row["lease_until"] is None
            or float(row["lease_until"]) < now
            or row["execution_status"] != "running"
            or _active_runtime_ms(row, now=now)
            >= int(row["execution_runtime_budget_ms"])
        ):
            raise StaleLeaseError("parser invocation lease is stale")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _assert_coordinator(
        connection: sqlite3.Connection,
        coordinator: ExecutionCoordinatorLease,
        now: float,
    ) -> None:
        row = connection.execute(
            """
            SELECT coordinator_owner, coordinator_token, coordinator_lease_until
            FROM search_executions WHERE execution_id = ?
            """,
            (coordinator.execution_id,),
        ).fetchone()
        if (
            row is None
            or row["coordinator_owner"] != coordinator.owner_id
            or row["coordinator_token"] != coordinator.token
            or row["coordinator_lease_until"] is None
            or float(row["coordinator_lease_until"]) < now
        ):
            raise StaleLeaseError("execution coordinator lease is stale")

    @staticmethod
    def _latest_listing_observations(
        connection: sqlite3.Connection,
        observation_ids: tuple[str, ...],
    ) -> dict[str, sqlite3.Row]:
        if not observation_ids:
            return {}
        placeholders = ",".join("?" for _ in observation_ids)
        rows = connection.execute(
            f"""
            SELECT
                observation.*,
                listing.vacancy_id,
                listing.source_listing_id,
                resource.target_provider_id
            FROM listing_observations AS observation
            JOIN vacancy_listings AS listing ON listing.listing_id = observation.listing_id
            JOIN vacancy_resources AS resource ON resource.vacancy_id = listing.vacancy_id
            WHERE observation.listing_observation_id IN ({placeholders})
            ORDER BY observation.observed_at, observation.listing_observation_id
            """,
            observation_ids,
        ).fetchall()
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            latest[str(row["listing_id"])] = row
        return latest

    @staticmethod
    def _requirements_for_source_plans(
        connection: sqlite3.Connection,
        source_plan_ids: tuple[str, ...],
    ) -> dict[str, tuple[sqlite3.Row, ...]]:
        if not source_plan_ids:
            return {}
        placeholders = ",".join("?" for _ in source_plan_ids)
        rows = connection.execute(
            f"""
            SELECT
                requirement.source_plan_id,
                requirement.requirement_id,
                requirement.criterion,
                requirement.required_fact_path,
                requirement.comparison_json,
                requirement.skip_when_final_keep,
                provider.provider_id,
                provider.provider_stage,
                provider.parser_id,
                provider.parser_version,
                provider.fact_path,
                provider.depends_on_fact_paths_json,
                provider.required_for_final,
                provider.cost_class,
                provider.ordering
            FROM criterion_requirements AS requirement
            JOIN fact_providers AS provider ON provider.requirement_id = requirement.requirement_id
            WHERE requirement.source_plan_id IN ({placeholders})
            ORDER BY requirement.source_plan_id, provider.ordering, provider.provider_id
            """,
            source_plan_ids,
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["source_plan_id"]), []).append(row)
        return {source_plan_id: tuple(items) for source_plan_id, items in grouped.items()}

    @staticmethod
    def _filter_requirements_by_scope(
        requirements: dict[str, tuple[sqlite3.Row, ...]],
        scope: Literal["all", "required", "optional"],
    ) -> dict[str, tuple[sqlite3.Row, ...]]:
        if scope == "all":
            return requirements
        required = scope == "required"
        return {
            source_plan_id: tuple(
                row
                for row in rows
                if bool(row["required_for_final"]) is required
            )
            for source_plan_id, rows in requirements.items()
        }

    @staticmethod
    def _current_fact_snapshots(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        listing_ids: tuple[str, ...],
    ) -> dict[str, tuple[JsonObject, JsonObject]]:
        if not listing_ids:
            return {}
        placeholders = ",".join("?" for _ in listing_ids)
        rows = connection.execute(
            f"""
            SELECT listing_id, materialized_facts_json, evidence_refs_json
            FROM (
                SELECT
                    listing_id,
                    materialized_facts_json,
                    evidence_refs_json,
                    ROW_NUMBER() OVER (
                        PARTITION BY listing_id
                        ORDER BY created_at DESC, rowid DESC
                    ) AS newest_rank
                FROM fact_sets
                WHERE execution_id = ? AND listing_id IN ({placeholders})
            )
            WHERE newest_rank = 1
            """,
            (execution_id, *listing_ids),
        ).fetchall()
        return {
            str(row["listing_id"]): (
                _json_object(row["materialized_facts_json"]),
                _json_object(row["evidence_refs_json"]),
            )
            for row in rows
        }

    @staticmethod
    def _enrichment_states(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        listing_ids: tuple[str, ...],
    ) -> dict[str, dict[str, tuple[str, bool, str | None]]]:
        if not listing_ids:
            return {}
        placeholders = ",".join("?" for _ in listing_ids)
        rows = connection.execute(
            f"""
            SELECT listing_id, provider_id, status, required, resolution_outcome
            FROM listing_enrichment_requests
            WHERE execution_id = ? AND listing_id IN ({placeholders})
            """,
            (execution_id, *listing_ids),
        ).fetchall()
        states: dict[str, dict[str, tuple[str, bool, str | None]]] = {}
        for row in rows:
            states.setdefault(str(row["listing_id"]), {})[str(row["provider_id"])] = (
                str(row["status"]),
                bool(row["required"]),
                None if row["resolution_outcome"] is None else str(row["resolution_outcome"]),
            )
        return states

    @staticmethod
    def _detail_consumer_rows(
        connection: sqlite3.Connection,
        detail_invocation_ids: tuple[str, ...],
    ) -> tuple[sqlite3.Row, ...]:
        if not detail_invocation_ids:
            return ()
        placeholders = ",".join("?" for _ in detail_invocation_ids)
        rows = connection.execute(
            f"""
            SELECT DISTINCT
                enrichment.execution_id,
                enrichment.source_execution_id,
                enrichment.listing_id,
                enrichment.invocation_id AS detail_invocation_id,
                listing_observation.source_plan_id,
                listing_observation.listing_observation_id,
                listing_observation.payload_json AS listing_payload_json,
                detail.detail_observation_id,
                detail.payload_json AS detail_payload_json
            FROM listing_enrichment_requests AS enrichment
            JOIN vacancy_detail_observations AS detail
              ON detail.invocation_id = enrichment.invocation_id
            JOIN listing_observations AS listing_observation
              ON listing_observation.listing_id = enrichment.listing_id
             AND listing_observation.execution_id = enrichment.source_execution_id
            WHERE enrichment.invocation_id IN ({placeholders})
              AND enrichment.status = 'waiting'
              AND listing_observation.listing_observation_id = (
                  SELECT newest.listing_observation_id
                  FROM listing_observations AS newest
                  WHERE newest.listing_id = enrichment.listing_id
                    AND newest.execution_id = enrichment.source_execution_id
                  ORDER BY newest.observed_at DESC, newest.listing_observation_id DESC
                  LIMIT 1
              )
            ORDER BY enrichment.listing_id
            """,
            detail_invocation_ids,
        ).fetchall()
        return tuple(rows)

    @staticmethod
    def _profile_consumer_rows(
        connection: sqlite3.Connection,
        invocation_ids: tuple[str, ...],
    ) -> tuple[sqlite3.Row, ...]:
        if not invocation_ids:
            return ()
        placeholders = ",".join("?" for _ in invocation_ids)
        return tuple(
            connection.execute(
                f"""
                SELECT DISTINCT
                    enrichment.execution_id,
                    enrichment.source_execution_id,
                    enrichment.listing_id,
                    enrichment.invocation_id AS provider_invocation_id,
                    listing_observation.source_plan_id,
                    listing_observation.listing_observation_id,
                    listing_observation.payload_json AS listing_payload_json,
                    profile.profile_observation_id,
                    profile.payload_json AS profile_payload_json
                FROM listing_enrichment_requests AS enrichment
                JOIN company_profile_observations AS profile
                  ON profile.invocation_id = enrichment.invocation_id
            JOIN listing_observations AS listing_observation
              ON listing_observation.listing_id = enrichment.listing_id
             AND listing_observation.execution_id = enrichment.source_execution_id
                WHERE enrichment.invocation_id IN ({placeholders})
                  AND enrichment.status = 'waiting'
                  AND listing_observation.listing_observation_id = (
                      SELECT newest.listing_observation_id
                      FROM listing_observations AS newest
                      WHERE newest.listing_id = enrichment.listing_id
                        AND newest.execution_id = enrichment.source_execution_id
                      ORDER BY newest.observed_at DESC, newest.listing_observation_id DESC
                      LIMIT 1
                  )
                ORDER BY enrichment.listing_id
                """,
                invocation_ids,
            ).fetchall()
        )

    @staticmethod
    def _site_consumer_rows(
        connection: sqlite3.Connection,
        invocation_ids: tuple[str, ...],
    ) -> tuple[sqlite3.Row, ...]:
        if not invocation_ids:
            return ()
        placeholders = ",".join("?" for _ in invocation_ids)
        return tuple(
            connection.execute(
                f"""
                SELECT DISTINCT
                    enrichment.execution_id,
                    enrichment.source_execution_id,
                    enrichment.listing_id,
                    enrichment.invocation_id AS provider_invocation_id,
                    listing_observation.source_plan_id,
                    listing_observation.listing_observation_id,
                    listing_observation.payload_json AS listing_payload_json,
                    site.site_observation_id,
                    site.payload_json AS site_payload_json
                FROM listing_enrichment_requests AS enrichment
                JOIN company_site_observations AS site
                  ON site.invocation_id = enrichment.invocation_id
            JOIN listing_observations AS listing_observation
              ON listing_observation.listing_id = enrichment.listing_id
             AND listing_observation.execution_id = enrichment.source_execution_id
                WHERE enrichment.invocation_id IN ({placeholders})
                  AND enrichment.status = 'waiting'
                  AND listing_observation.listing_observation_id = (
                      SELECT newest.listing_observation_id
                      FROM listing_observations AS newest
                      WHERE newest.listing_id = enrichment.listing_id
                        AND newest.execution_id = enrichment.source_execution_id
                      ORDER BY newest.observed_at DESC, newest.listing_observation_id DESC
                      LIMIT 1
                  )
                ORDER BY enrichment.listing_id
                """,
                invocation_ids,
            ).fetchall()
        )

    @staticmethod
    def _bounded_consumer_rows(
        rows: tuple[sqlite3.Row, ...],
        events: dict[str, StoredDomainEvent],
        *,
        invocation_column: str,
    ) -> tuple[sqlite3.Row, ...]:
        allowed = {
            invocation_id: set(event.affected_listing_ids)
            for invocation_id, event in events.items()
        }
        return tuple(
            row
            for row in rows
            if str(row["listing_id"])
            in allowed.get(str(row[invocation_column]), set())
        )

    @staticmethod
    def _waiting_enrichment_consumers(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        invocation_ids: tuple[str, ...],
    ) -> _WaitingConsumers:
        if not invocation_ids:
            return {}
        placeholders = ",".join("?" for _ in invocation_ids)
        rows = connection.execute(
            f"""
            SELECT execution_id, listing_id, invocation_id,
                   enrichment_request_id, provider_id
            FROM listing_enrichment_requests
            WHERE execution_id = ?
              AND invocation_id IN ({placeholders})
              AND status = 'waiting'
            ORDER BY invocation_id, listing_id, provider_id
            """,
            (execution_id, *invocation_ids),
        ).fetchall()
        grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        for row in rows:
            key = (
                str(row["execution_id"]),
                str(row["listing_id"]),
                str(row["invocation_id"]),
            )
            grouped.setdefault(key, []).append(row)
        return {key: tuple(values) for key, values in grouped.items()}

    @staticmethod
    def _terminal_waiting_provider_invocations(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        listing_ids: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not listing_ids:
            return (), ()
        placeholders = ",".join("?" for _ in listing_ids)
        rows = connection.execute(
            f"""
            SELECT DISTINCT invocation.invocation_id, invocation.status
            FROM listing_enrichment_requests AS enrichment
            JOIN parser_invocations AS invocation
              ON invocation.invocation_id = enrichment.invocation_id
            WHERE enrichment.execution_id = ?
              AND enrichment.listing_id IN ({placeholders})
              AND enrichment.status = 'waiting'
              AND invocation.status IN ('succeeded', 'failed', 'cancelled')
            ORDER BY invocation.invocation_id
            """,
            (execution_id, *listing_ids),
        ).fetchall()
        completed = tuple(
            str(row["invocation_id"])
            for row in rows
            if row["status"] == "succeeded"
        )
        failed = tuple(
            str(row["invocation_id"])
            for row in rows
            if row["status"] in {"failed", "cancelled"}
        )
        return completed, failed

    @staticmethod
    def _provider_observation_events(
        connection: sqlite3.Connection,
        invocation_ids: tuple[str, ...],
    ) -> tuple[StoredDomainEvent, ...]:
        if not invocation_ids:
            return ()
        placeholders = ",".join("?" for _ in invocation_ids)
        rows = connection.execute(
            f"""
            SELECT * FROM domain_events
            WHERE producer_invocation_id IN ({placeholders})
              AND event_type IN (
                  'vacancy_detail_observation_stored',
                  'company_profile_observation_stored',
                  'company_site_observation_stored'
              )
            ORDER BY occurred_at, event_id
            """,
            invocation_ids,
        ).fetchall()
        events = tuple(_stored_event(row) for row in rows)
        observed_invocation_ids = {
            event.producer_invocation_id for event in events
        }
        missing = tuple(
            invocation_id
            for invocation_id in invocation_ids
            if invocation_id not in observed_invocation_ids
        )
        if missing:
            raise RuntimeError(
                "succeeded provider invocation is missing an observation event: "
                + ", ".join(missing)
            )
        return events

    def _route_discovered_endpoints(  # noqa: PLR0912 - routing outcomes commit atomically
        self,
        connection: sqlite3.Connection,
        *,
        origin_execution_id: str,
        target_execution_id: str,
        profile_events: dict[str, StoredDomainEvent],
        site_events: dict[str, StoredDomainEvent],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        discovered_planner: Callable[
            [ParserRef, JsonObject], tuple[SearchListingInput, ...]
        ],
        discovered_requirement_planner: _DiscoveredRequirementPlanner,
        now: float,
    ) -> None:
        observation_events: dict[str, StoredDomainEvent] = {}
        for event in profile_events.values():
            observation_id = event.payload.get("profileObservationId")
            if isinstance(observation_id, str):
                observation_events[observation_id] = event
        for event in site_events.values():
            observation_id = event.payload.get("siteObservationId")
            if isinstance(observation_id, str):
                observation_events[observation_id] = event
        if not observation_events:
            return
        placeholders = ",".join("?" for _ in observation_events)
        parameters = tuple(observation_events)
        endpoints = connection.execute(
            f"""
            SELECT * FROM discovered_endpoints
            WHERE resolution_status = 'unresolved'
              AND (
                profile_observation_id IN ({placeholders})
                OR site_observation_id IN ({placeholders})
              )
            ORDER BY endpoint_id
            """,
            (*parameters, *parameters),
        ).fetchall()
        execution = connection.execute(
            """
            SELECT execution_kind, parent_execution_id,
                   discovery_plan_budget, discovery_plans_created
            FROM search_executions WHERE execution_id = ?
            """,
            (target_execution_id,),
        ).fetchone()
        if execution is None:
            raise KeyError(f"unknown execution: {target_execution_id}")
        if target_execution_id != origin_execution_id and execution["execution_kind"] != "discovered_search":
            raise ValueError("discovered endpoints must target a discovered_search execution")
        remaining_discovery_plans = max(
            int(execution["discovery_plan_budget"])
            - int(execution["discovery_plans_created"]),
            0,
        )
        for endpoint in endpoints:
            endpoint_id = str(endpoint["endpoint_id"])
            url = str(endpoint["normalized_url"])
            provider_hint = endpoint["provider_hint"]
            resolution = target_resolver(
                ParserType.SEARCH_LISTING,
                provider_hint if isinstance(provider_hint, str) else None,
                url,
            )
            if resolution.kind != "resolved" or resolution.parser_ref is None:
                connection.execute(
                    "UPDATE discovered_endpoints SET resolution_status = ? WHERE endpoint_id = ?",
                    (
                        "ambiguous" if resolution.kind == "ambiguous_target" else "unsupported",
                        endpoint_id,
                    ),
                )
                continue
            if remaining_discovery_plans == 0:
                connection.execute(
                    "UPDATE discovered_endpoints SET resolution_status = 'budget_exhausted' WHERE endpoint_id = ?",
                    (endpoint_id,),
                )
                continue
            parser_ref = resolution.parser_ref
            manifest = manifest_resolver(parser_ref)
            target: JsonObject = {
                "kind": "discovered_url",
                "url": url,
                "endpointId": endpoint_id,
            }
            initial_inputs = discovered_planner(parser_ref, target)
            if not initial_inputs:
                connection.execute(
                    "UPDATE discovered_endpoints SET resolution_status = 'unsupported' WHERE endpoint_id = ?",
                    (endpoint_id,),
                )
                continue
            queries = initial_inputs[0].queries
            if any(parser_input.queries != queries for parser_input in initial_inputs):
                raise ValueError("discovered source planner returned inconsistent queries")
            source_plan_id = _stable_id(
                "source-plan",
                target_execution_id,
                endpoint_id,
                parser_ref.parser_id,
            )
            observation_id = endpoint["profile_observation_id"] or endpoint["site_observation_id"]
            event = observation_events[str(observation_id)]
            inserted_plan = connection.execute(
                """
                INSERT OR IGNORE INTO source_plans (
                    source_plan_id, execution_id, origin_event_id, origin_company_id,
                    origin_endpoint_id, source_id, parser_id, parser_version,
                    query_mode, queries_json, unit_budget, item_budget,
                    invocation_budget, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned')
                """,
                (
                    source_plan_id,
                    target_execution_id,
                    event.event_id,
                    endpoint["company_id"],
                    endpoint_id,
                    f"discovered:{endpoint_id}",
                    parser_ref.parser_id,
                    parser_ref.implementation_version,
                    manifest.query_mode,
                    _json_dumps(queries),
                    manifest.default_unit_budget,
                    manifest.default_item_budget,
                    manifest.default_invocation_budget,
                ),
            )
            if inserted_plan.rowcount:
                connection.execute(
                    """
                    UPDATE search_executions
                    SET discovery_plans_created = discovery_plans_created + 1
                    WHERE execution_id = ?
                    """,
                    (target_execution_id,),
                )
                remaining_discovery_plans -= 1
            for requirement in discovered_requirement_planner(
                source_plan_id,
                manifest,
                initial_inputs,
            ):
                requirement_id = _stable_id(
                    "requirement",
                    source_plan_id,
                    requirement.criterion,
                    requirement.fact_path,
                )
                self._add_fact_requirement(
                    connection,
                    requirement_id=requirement_id,
                    source_plan_id=source_plan_id,
                    criterion=requirement.criterion,
                    fact_path=requirement.fact_path,
                    comparison=requirement.comparison,
                    provider=requirement.provider,
                    skip_when_final_keep=requirement.skip_when_final_keep,
                )
            for parser_input in initial_inputs:
                fingerprint = _fingerprint(parser_input)
                self._enqueue_invocation(
                    connection,
                    ParserInvocationSpec(
                        execution_id=target_execution_id,
                        source_plan_id=source_plan_id,
                        parent_invocation_id=event.producer_invocation_id,
                        cause_event_id=event.event_id,
                        parser_ref=parser_ref,
                        parser_type=ParserType.SEARCH_LISTING,
                        input_schema_id=manifest.input_schema_id,
                        parser_input=parser_input,
                        task_class=TaskClass.LISTING,
                        task_key=f"search_listing:{parser_ref.parser_id}:{source_plan_id}:{fingerprint}",
                        available_at=now,
                        reserved_collection_units=manifest.max_units_per_invocation,
                        resource_key_resolved=False,
                    ),
                )
            connection.execute(
                """
                UPDATE discovered_endpoints
                SET resolution_status = 'resolved', resolved_parser_id = ?,
                    resolved_parser_version = ?
                WHERE endpoint_id = ?
                """,
                (
                    parser_ref.parser_id,
                    parser_ref.implementation_version,
                    endpoint_id,
                ),
            )

    def _materialize_and_schedule_listing(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event: StoredDomainEvent,
        requirements: tuple[sqlite3.Row, ...],
        current_snapshots: dict[str, tuple[JsonObject, JsonObject]],
        enrichment_state: dict[str, tuple[str, bool, str | None]],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        preliminary_selection_evaluator: Callable[[JsonObject], SelectionDecision],
        final_selection_evaluator: Callable[[JsonObject], SelectionDecision],
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
        now: float,
    ) -> None:
        facts = _json_object(row["payload_json"])
        evidence_refs = {"listingObservationId": str(row["listing_observation_id"])}
        listing_id = str(row["listing_id"])
        facts, evidence_refs = self._merge_current_fact_snapshot(
            facts=facts,
            evidence_refs=evidence_refs,
            current_snapshot=current_snapshots.get(listing_id),
        )
        facts, evidence_refs = self._apply_fact_derivations(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            facts=facts,
            evidence_refs=evidence_refs,
            derivation_evaluator=derivation_evaluator,
        )
        fingerprint = _fingerprint(facts)
        fact_set_id = _stable_id(
            "fact-set",
            str(row["execution_id"]),
            str(row["listing_id"]),
            fingerprint,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fact_sets (
                fact_set_id, execution_id, listing_id, evidence_refs_json,
                materialized_facts_json, fingerprint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_set_id,
                row["execution_id"],
                row["listing_id"],
                _json_dumps(evidence_refs),
                _json_dumps(facts),
                fingerprint,
                now,
            ),
        )
        current_snapshots[listing_id] = (facts, evidence_refs)
        selection = preliminary_selection_evaluator(facts)
        if not selection.can_enrich:
            self._insert_evaluation(
                connection,
                execution_id=str(row["execution_id"]),
                listing_id=str(row["listing_id"]),
                fact_set_id=fact_set_id,
                stage="final",
                outcome="reject",
                reasons=selection.reasons,
            )
            return
        all_missing = tuple(
            requirement
            for requirement in requirements
            if not _comparison_matches(
                _fact_at_path(facts, str(requirement["required_fact_path"])),
                _json_object(requirement["comparison_json"]),
            )
        )
        final_selection = final_selection_evaluator(facts)
        missing = (
            tuple(
                requirement
                for requirement in all_missing
                if not final_selection.keep or not bool(requirement["skip_when_final_keep"])
            )
        )
        if missing:
            outcome = "enrich"
            stage = "preliminary"
            reason_codes = tuple(
                f"missing:{requirement['required_fact_path']}" for requirement in missing
            )
        else:
            outcome = "keep" if final_selection.keep else "reject"
            stage = "final"
            reason_codes = final_selection.reasons
        evaluation_id = _stable_id("evaluation", fact_set_id, stage)
        connection.execute(
            """
            INSERT OR IGNORE INTO selection_evaluations (
                evaluation_id, execution_id, listing_id, fact_set_id,
                stage, outcome, reason_codes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                row["execution_id"],
                row["listing_id"],
                fact_set_id,
                stage,
                outcome,
                _json_dumps(reason_codes),
            ),
        )
        if outcome == "reject":
            return
        self._schedule_missing_fact_providers(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            parent_invocation_id=str(row["invocation_id"]),
            event=event,
            facts=facts,
            missing=missing,
            enrichment_state=enrichment_state,
            manifest_resolver=manifest_resolver,
            target_resolver=target_resolver,
            now=now,
        )
        self._finalize_if_all_dependencies_terminal(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            fact_set_id=fact_set_id,
            facts=facts,
            enrichment_state=enrichment_state,
            selection_evaluator=final_selection_evaluator,
        )

    def _schedule_missing_fact_providers(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        source_execution_id: str | None = None,
        listing_id: str,
        parent_invocation_id: str,
        event: StoredDomainEvent,
        facts: JsonObject,
        missing: tuple[sqlite3.Row, ...],
        enrichment_state: dict[str, tuple[str, bool, str | None]],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        now: float,
    ) -> None:
        if not missing:
            return
        provider_fact_paths = {str(requirement["fact_path"]) for requirement in missing}
        scheduled_provider_ids: set[str] = set()
        for provider in missing:
            provider_id = str(provider["provider_id"])
            if provider_id in scheduled_provider_ids:
                continue
            scheduled_provider_ids.add(provider_id)
            if provider_id in enrichment_state:
                continue
            dependency_paths = _json_string_list(
                provider["depends_on_fact_paths_json"],
                "provider dependencies",
            )
            missing_dependencies = tuple(
                path
                for path in dependency_paths
                if _fact_at_path(facts, path) in (_MISSING, None, "", [], {})
            )
            if missing_dependencies:
                if all(path in provider_fact_paths for path in missing_dependencies):
                    continue
                self._insert_unresolved_enrichment(
                    connection,
                    execution_id=execution_id,
                    source_execution_id=source_execution_id,
                    listing_id=listing_id,
                    provider=provider,
                )
                enrichment_state[provider_id] = (
                    "terminal",
                    bool(provider["required_for_final"]),
                    "unresolved_no_trusted_url",
                )
                continue
            enrichment_state[provider_id] = self._schedule_listing_provider(
                connection,
                execution_id=execution_id,
                source_execution_id=source_execution_id,
                listing_id=listing_id,
                parent_invocation_id=parent_invocation_id,
                event=event,
                facts=facts,
                provider=provider,
                manifest_resolver=manifest_resolver,
                target_resolver=target_resolver,
                now=now,
            )

    def _schedule_listing_provider(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        source_execution_id: str | None,
        listing_id: str,
        parent_invocation_id: str,
        event: StoredDomainEvent,
        facts: JsonObject,
        provider: sqlite3.Row,
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        now: float,
    ) -> tuple[str, bool, str | None]:
        stage = ProviderStage(str(provider["provider_stage"]))
        if stage not in {
            ProviderStage.DETAIL_OUTPUT,
            ProviderStage.PROFILE_OUTPUT,
            ProviderStage.SITE_OUTPUT,
        }:
            return self._terminal_provider_state(
                connection,
                execution_id=execution_id,
                source_execution_id=source_execution_id,
                listing_id=listing_id,
                provider=provider,
                outcome="provider_unavailable",
                terminal_reason=f"unsupported_provider_stage:{stage.value}",
            )
        target = self._provider_target(stage, facts)
        if target is None:
            self._insert_unresolved_enrichment(
                connection,
                execution_id=execution_id,
                source_execution_id=source_execution_id,
                listing_id=listing_id,
                provider=provider,
            )
            return (
                "terminal",
                bool(provider["required_for_final"]),
                "unresolved_no_trusted_url",
            )
        route = self._resolve_provider_route(
            provider,
            target,
            manifest_resolver=manifest_resolver,
            target_resolver=target_resolver,
        )
        if isinstance(route, str):
            return self._terminal_provider_state(
                connection,
                execution_id=execution_id,
                source_execution_id=source_execution_id,
                listing_id=listing_id,
                provider=provider,
                outcome=route,
                terminal_reason=route,
            )
        parser_ref, manifest = route
        invocation_id = self._enqueue_invocation(
            connection,
            ParserInvocationSpec(
                execution_id=execution_id,
                source_plan_id=None,
                parent_invocation_id=parent_invocation_id,
                cause_event_id=event.event_id,
                parser_ref=parser_ref,
                parser_type=target.expected_type,
                input_schema_id=manifest.input_schema_id,
                parser_input=target.parser_input,
                task_class=target.task_class,
                task_key=_provider_task_key(stage, parser_ref, target),
                available_at=now,
                reserved_collection_units=None,
                resource_key_resolved=False,
            ),
        )
        invocation_row = connection.execute(
            "SELECT status FROM parser_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if invocation_row is None:
            raise RuntimeError(f"provider invocation disappeared: {invocation_id}")
        invocation_status = str(invocation_row["status"])
        observation_event = None
        request_status = "waiting"
        resolution_outcome = "resolved"
        terminal_reason = None
        if invocation_status in {"failed", "cancelled"}:
            request_status = "terminal"
            resolution_outcome = "provider_terminal"
            terminal_reason = "invocation_terminal"
        elif invocation_status == "succeeded":
            observation_event = connection.execute(
                """
                SELECT * FROM domain_events
                WHERE producer_invocation_id = ?
                  AND event_type IN (
                      'vacancy_detail_observation_stored',
                      'company_profile_observation_stored',
                      'company_site_observation_stored'
                  )
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT 1
                """,
                (invocation_id,),
            ).fetchone()
            if observation_event is None:
                request_status = "terminal"
                resolution_outcome = "provider_output_missing_fact"
                terminal_reason = "missing_declared_fact"
        enrichment_request_id, inserted = self._insert_enrichment_request(
            connection,
            execution_id=execution_id,
            source_execution_id=source_execution_id,
            listing_id=listing_id,
            provider=provider,
            invocation_id=invocation_id,
            status=request_status,
            resolution_outcome=resolution_outcome,
            terminal_reason=terminal_reason,
        )
        if inserted and observation_event is not None:
            self._insert_provider_redelivery_event(
                connection,
                event=observation_event,
                enrichment_request_id=enrichment_request_id,
                now=now,
            )
        return (
            request_status,
            bool(provider["required_for_final"]),
            resolution_outcome,
        )

    @staticmethod
    def _provider_target(
        stage: ProviderStage,
        facts: JsonObject,
    ) -> _ProviderTarget | None:
        if stage == ProviderStage.DETAIL_OUTPUT:
            provider_hint = _required_json_text(facts, "target_provider_id")
            vacancy_url = _required_json_text(facts, "vacancy_url")
            return _ProviderTarget(
                expected_type=ParserType.VACANCY_DETAIL,
                parser_input=VacancyDetailInput(
                    target_provider_id=provider_hint,
                    vacancy_url=vacancy_url,
                    source_listing_id=_optional_json_text(facts, "source_listing_id"),
                ),
                task_class=TaskClass.DETAIL,
                provider_hint=provider_hint,
                normalized_url=_normalize_url(vacancy_url),
            )
        company = _optional_object(facts, "company")
        if stage == ProviderStage.PROFILE_OUTPUT:
            profile_url = None if company is None else _optional_json_text(company, "profile_url")
            if profile_url is None or company is None:
                return None
            provider_hint = (
                _optional_json_text(company, "target_provider_id")
                or _required_json_text(facts, "target_provider_id")
            )
            return _ProviderTarget(
                expected_type=ParserType.COMPANY_PROFILE,
                parser_input=CompanyProfileInput(
                    target_provider_id=provider_hint,
                    company_profile_url=profile_url,
                    source_company_id=_optional_json_text(company, "source_company_id"),
                ),
                task_class=TaskClass.PROFILE,
                provider_hint=provider_hint,
                normalized_url=_normalize_url(profile_url),
            )
        site_url = _optional_json_text(facts, "official_site_url")
        if site_url is None and company is not None:
            site_url = _optional_json_text(company, "official_site_url")
        if site_url is None:
            return None
        provider_hint = (
            None if company is None else _optional_json_text(company, "target_provider_id")
        ) or _required_json_text(facts, "target_provider_id")
        return _ProviderTarget(
            expected_type=ParserType.COMPANY_SITE,
            parser_input=CompanySiteInput(site_url=site_url),
            task_class=TaskClass.SITE,
            provider_hint=provider_hint,
            normalized_url=_normalize_url(site_url),
        )

    @staticmethod
    def _resolve_provider_route(
        provider: sqlite3.Row,
        target: _ProviderTarget,
        *,
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
    ) -> tuple[ParserRef, ParserManifest] | str:
        resolution = target_resolver(
            target.expected_type,
            target.provider_hint,
            target.normalized_url,
        )
        if resolution.kind != "resolved" or resolution.parser_ref is None:
            return resolution.kind
        parser_ref = resolution.parser_ref
        declared_id = provider["parser_id"]
        declared_version = provider["parser_version"]
        if isinstance(declared_id, str) != isinstance(declared_version, str):
            raise ValueError("fact provider parser reference is incomplete")
        if isinstance(declared_id, str) and parser_ref != ParserRef(
            declared_id,
            cast(str, declared_version),
        ):
            return "parser_route_mismatch"
        manifest = manifest_resolver(parser_ref)
        if manifest.parser_type != target.expected_type:
            raise ValueError("resolved parser type does not match provider stage")
        return parser_ref, manifest

    def _terminal_provider_state(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        source_execution_id: str | None,
        listing_id: str,
        provider: sqlite3.Row,
        outcome: str,
        terminal_reason: str,
    ) -> tuple[str, bool, str]:
        self._insert_enrichment_request(
            connection,
            execution_id=execution_id,
            source_execution_id=source_execution_id,
            listing_id=listing_id,
            provider=provider,
            invocation_id=None,
            status="terminal",
            resolution_outcome=outcome,
            terminal_reason=terminal_reason,
        )
        return "terminal", bool(provider["required_for_final"]), outcome

    @staticmethod
    def _insert_unresolved_enrichment(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        source_execution_id: str | None,
        listing_id: str,
        provider: sqlite3.Row,
    ) -> None:
        SqliteGraphRepository._insert_enrichment_request(
            connection,
            execution_id=execution_id,
            source_execution_id=source_execution_id,
            listing_id=listing_id,
            provider=provider,
            invocation_id=None,
            status="terminal",
            resolution_outcome="unresolved_no_trusted_url",
            terminal_reason="missing_trusted_url",
        )

    @staticmethod
    def _insert_enrichment_request(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        source_execution_id: str | None,
        listing_id: str,
        provider: sqlite3.Row,
        invocation_id: str | None,
        status: str,
        resolution_outcome: str,
        terminal_reason: str | None,
    ) -> tuple[str, bool]:
        enrichment_request_id = _stable_id(
            "enrichment-request",
            execution_id,
            listing_id,
            str(provider["provider_id"]),
        )
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO listing_enrichment_requests (
                enrichment_request_id, execution_id, source_execution_id,
                listing_id, invocation_id,
                provider_id, required, status, resolution_outcome, terminal_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                enrichment_request_id,
                execution_id,
                source_execution_id or execution_id,
                listing_id,
                invocation_id,
                provider["provider_id"],
                provider["required_for_final"],
                status,
                resolution_outcome,
                terminal_reason,
            ),
        )
        return enrichment_request_id, bool(inserted.rowcount)

    @staticmethod
    def _insert_provider_redelivery_event(
        connection: sqlite3.Connection,
        *,
        event: sqlite3.Row,
        enrichment_request_id: str,
        now: float,
    ) -> None:
        producer_invocation_id = str(event["producer_invocation_id"])
        event_id = _stable_id(
            "event",
            producer_invocation_id,
            "late-consumer",
            enrichment_request_id,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO domain_events (
                event_id, execution_id, producer_invocation_id, event_key,
                event_type, schema_version, payload_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event["execution_id"],
                producer_invocation_id,
                f"provider-redelivery:{producer_invocation_id}:{enrichment_request_id}",
                event["event_type"],
                event["schema_version"],
                event["payload_json"],
                now,
            ),
        )

    @staticmethod
    def _finalize_if_all_dependencies_terminal(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        listing_id: str,
        fact_set_id: str,
        facts: JsonObject,
        enrichment_state: dict[str, tuple[str, bool, str | None]],
        selection_evaluator: Callable[[JsonObject], SelectionDecision],
    ) -> None:
        dependencies = tuple(enrichment_state.values())
        if not dependencies or any(status == "waiting" for status, _required, _outcome in dependencies):
            return
        required_failed = any(
            required
            and status == "terminal"
            and resolution_outcome != "provider_output_missing_fact"
            for status, required, resolution_outcome in dependencies
        )
        selection = selection_evaluator(facts)
        outcome = "reject" if required_failed or not selection.keep else "keep"
        dependency_reasons = tuple(
            sorted(outcome for _status, _required, outcome in dependencies if outcome is not None)
        )
        reasons = ("required_provider_terminal",) if required_failed else selection.reasons or dependency_reasons
        connection.execute(
            """
            INSERT OR IGNORE INTO selection_evaluations (
                evaluation_id, execution_id, listing_id, fact_set_id,
                stage, outcome, reason_codes_json
            ) VALUES (?, ?, ?, ?, 'final', ?, ?)
            """,
            (
                _stable_id("evaluation", fact_set_id, "final"),
                execution_id,
                listing_id,
                fact_set_id,
                outcome,
                _json_dumps(reasons),
            ),
        )

    @staticmethod
    def _insert_evaluation(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        listing_id: str,
        fact_set_id: str,
        stage: str,
        outcome: str,
        reasons: tuple[str, ...],
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO selection_evaluations (
                evaluation_id, execution_id, listing_id, fact_set_id,
                stage, outcome, reason_codes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _stable_id("evaluation", fact_set_id, stage),
                execution_id,
                listing_id,
                fact_set_id,
                stage,
                outcome,
                _json_dumps(reasons),
            ),
        )

    @staticmethod
    def _apply_fact_derivations(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        listing_id: str,
        facts: JsonObject,
        evidence_refs: JsonObject,
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
    ) -> tuple[JsonObject, JsonObject]:
        facts = _canonical_graph_facts(facts)
        input_evidence_refs = dict(evidence_refs)
        input_fingerprint = _fingerprint(facts)
        derived_facts: JsonObject = {}
        derivation_ids: list[str] = []
        for derivation in derivation_evaluator(facts):
            derivation_id = _stable_id(
                "fact-derivation",
                execution_id,
                listing_id,
                derivation.deriver_id,
                derivation.deriver_version,
                input_fingerprint,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO fact_derivations (
                    fact_derivation_id, execution_id, listing_id,
                    deriver_id, deriver_version, input_evidence_refs_json,
                    input_fingerprint, output_schema_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    derivation_id,
                    execution_id,
                    listing_id,
                    derivation.deriver_id,
                    derivation.deriver_version,
                    _json_dumps(input_evidence_refs),
                    input_fingerprint,
                    derivation.output_schema_id,
                    _json_dumps(derivation.payload),
                ),
            )
            derived_facts[derivation.deriver_id] = derivation.payload
            derivation_ids.append(derivation_id)
        materialized = dict(facts)
        if derived_facts:
            materialized["derived_facts"] = derived_facts
        materialized_evidence = dict(evidence_refs)
        if derivation_ids:
            materialized_evidence["factDerivationIds"] = tuple(derivation_ids)
        return materialized, materialized_evidence

    def _materialize_detail_consumer(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event: StoredDomainEvent,
        requirements: tuple[sqlite3.Row, ...],
        current_snapshots: dict[str, tuple[JsonObject, JsonObject]],
        enrichment_state: dict[str, tuple[str, bool, str | None]],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        waiting_consumers: _WaitingConsumers,
        selection_evaluator: Callable[[JsonObject], SelectionDecision],
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
        now: float,
    ) -> None:
        if event.event_type != "vacancy_detail_observation_stored":
            raise ValueError("detail consumer requires a detail observation event")
        listing_facts = _json_object(row["listing_payload_json"])
        detail_facts = _json_object(row["detail_payload_json"])
        facts = _merge_fact_payloads(listing_facts, detail_facts)
        evidence_refs = {
            "listingObservationId": str(row["listing_observation_id"]),
            "detailObservationId": str(row["detail_observation_id"]),
        }
        listing_id = str(row["listing_id"])
        facts, evidence_refs = self._merge_current_fact_snapshot(
            facts=facts,
            evidence_refs=evidence_refs,
            current_snapshot=current_snapshots.get(listing_id),
        )
        facts, evidence_refs = SqliteGraphRepository._apply_fact_derivations(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            facts=facts,
            evidence_refs=evidence_refs,
            derivation_evaluator=derivation_evaluator,
        )
        fingerprint = _fingerprint(facts)
        fact_set_id = _stable_id(
            "fact-set",
            str(row["execution_id"]),
            str(row["listing_id"]),
            fingerprint,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fact_sets (
                fact_set_id, execution_id, listing_id, evidence_refs_json,
                materialized_facts_json, fingerprint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_set_id,
                row["execution_id"],
                row["listing_id"],
                _json_dumps(evidence_refs),
                _json_dumps(facts),
                fingerprint,
                now,
            ),
        )
        current_snapshots[listing_id] = (facts, evidence_refs)
        self._settle_enrichment_requests_from_facts(
            connection,
            consumers=waiting_consumers.get(
                (
                    str(row["execution_id"]),
                    str(row["listing_id"]),
                    str(row["detail_invocation_id"]),
                ),
                (),
            ),
            requirements=requirements,
            facts=facts,
            enrichment_state=enrichment_state,
        )
        selection = selection_evaluator(facts)
        if not selection.can_enrich:
            self._insert_evaluation(
                connection,
                execution_id=str(row["execution_id"]),
                listing_id=str(row["listing_id"]),
                fact_set_id=fact_set_id,
                stage="final",
                outcome="reject",
                reasons=selection.reasons,
            )
            return
        missing = tuple(
            requirement
            for requirement in requirements
            if not _comparison_matches(
                _fact_at_path(facts, str(requirement["required_fact_path"])),
                _json_object(requirement["comparison_json"]),
            )
        )
        if missing:
            outcome = "enrich"
            stage = "preliminary"
            reason_codes = tuple(
                f"missing:{requirement['required_fact_path']}" for requirement in missing
            )
        else:
            outcome = "keep" if selection.keep else "reject"
            stage = "final"
            reason_codes = selection.reasons
        evaluation_id = _stable_id("evaluation", fact_set_id, stage)
        connection.execute(
            """
            INSERT OR IGNORE INTO selection_evaluations (
                evaluation_id, execution_id, listing_id, fact_set_id,
                stage, outcome, reason_codes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                row["execution_id"],
                row["listing_id"],
                fact_set_id,
                stage,
                outcome,
                _json_dumps(reason_codes),
            ),
        )
        self._schedule_missing_fact_providers(
            connection,
            execution_id=str(row["execution_id"]),
            source_execution_id=str(row["source_execution_id"]),
            listing_id=str(row["listing_id"]),
            parent_invocation_id=str(row["detail_invocation_id"]),
            event=event,
            facts=facts,
            missing=missing,
            enrichment_state=enrichment_state,
            manifest_resolver=manifest_resolver,
            target_resolver=target_resolver,
            now=now,
        )
        self._finalize_if_all_dependencies_terminal(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            fact_set_id=fact_set_id,
            facts=facts,
            enrichment_state=enrichment_state,
            selection_evaluator=selection_evaluator,
        )

    def _materialize_company_consumer(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event: StoredDomainEvent,
        requirements: tuple[sqlite3.Row, ...],
        current_snapshots: dict[str, tuple[JsonObject, JsonObject]],
        enrichment_state: dict[str, tuple[str, bool, str | None]],
        provider_payload_column: str,
        provider_observation_column: str,
        evidence_key: str,
        expected_event_type: str,
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: _TargetResolver,
        waiting_consumers: _WaitingConsumers,
        selection_evaluator: Callable[[JsonObject], SelectionDecision],
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
        now: float,
    ) -> None:
        if event.event_type != expected_event_type:
            raise ValueError("company consumer event type does not match provider output")
        listing_facts = _json_object(row["listing_payload_json"])
        provider_facts = _json_object(row[provider_payload_column])
        facts = _merge_fact_payloads(listing_facts, provider_facts)
        evidence_refs = {
            "listingObservationId": str(row["listing_observation_id"]),
            evidence_key: str(row[provider_observation_column]),
        }
        listing_id = str(row["listing_id"])
        facts, evidence_refs = self._merge_current_fact_snapshot(
            facts=facts,
            evidence_refs=evidence_refs,
            current_snapshot=current_snapshots.get(listing_id),
        )
        facts, evidence_refs = self._apply_fact_derivations(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            facts=facts,
            evidence_refs=evidence_refs,
            derivation_evaluator=derivation_evaluator,
        )
        fingerprint = _fingerprint(facts)
        fact_set_id = _stable_id(
            "fact-set",
            str(row["execution_id"]),
            str(row["listing_id"]),
            fingerprint,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO fact_sets (
                fact_set_id, execution_id, listing_id, evidence_refs_json,
                materialized_facts_json, fingerprint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact_set_id,
                row["execution_id"],
                row["listing_id"],
                _json_dumps(evidence_refs),
                _json_dumps(facts),
                fingerprint,
                now,
            ),
        )
        current_snapshots[listing_id] = (facts, evidence_refs)
        self._settle_enrichment_requests_from_facts(
            connection,
            consumers=waiting_consumers.get(
                (
                    str(row["execution_id"]),
                    str(row["listing_id"]),
                    str(row["provider_invocation_id"]),
                ),
                (),
            ),
            requirements=requirements,
            facts=facts,
            enrichment_state=enrichment_state,
        )
        selection = selection_evaluator(facts)
        if not selection.can_enrich:
            self._insert_evaluation(
                connection,
                execution_id=str(row["execution_id"]),
                listing_id=str(row["listing_id"]),
                fact_set_id=fact_set_id,
                stage="final",
                outcome="reject",
                reasons=selection.reasons,
            )
            return
        missing = tuple(
            requirement
            for requirement in requirements
            if not _comparison_matches(
                _fact_at_path(facts, str(requirement["required_fact_path"])),
                _json_object(requirement["comparison_json"]),
            )
        )
        if missing:
            outcome = "enrich"
            stage = "preliminary"
            reasons = tuple(f"missing:{row['required_fact_path']}" for row in missing)
        else:
            outcome = "keep" if selection.keep else "reject"
            stage = "final"
            reasons = selection.reasons
        connection.execute(
            """
            INSERT OR IGNORE INTO selection_evaluations (
                evaluation_id, execution_id, listing_id, fact_set_id,
                stage, outcome, reason_codes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _stable_id("evaluation", fact_set_id, stage),
                row["execution_id"],
                row["listing_id"],
                fact_set_id,
                stage,
                outcome,
                _json_dumps(reasons),
            ),
        )
        self._schedule_missing_fact_providers(
            connection,
            execution_id=str(row["execution_id"]),
            source_execution_id=str(row["source_execution_id"]),
            listing_id=str(row["listing_id"]),
            parent_invocation_id=str(row["provider_invocation_id"]),
            event=event,
            facts=facts,
            missing=missing,
            enrichment_state=enrichment_state,
            manifest_resolver=manifest_resolver,
            target_resolver=target_resolver,
            now=now,
        )
        self._finalize_if_all_dependencies_terminal(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            fact_set_id=fact_set_id,
            facts=facts,
            enrichment_state=enrichment_state,
            selection_evaluator=selection_evaluator,
        )

    @staticmethod
    def _settle_enrichment_requests_from_facts(
        connection: sqlite3.Connection,
        *,
        consumers: tuple[sqlite3.Row, ...],
        requirements: tuple[sqlite3.Row, ...],
        facts: JsonObject,
        enrichment_state: dict[str, tuple[str, bool, str | None]],
    ) -> None:
        requirements_by_provider = {
            str(requirement["provider_id"]): requirement for requirement in requirements
        }
        request_ids_by_satisfaction: dict[bool, list[str]] = {True: [], False: []}
        for consumer in consumers:
            provider_id = str(consumer["provider_id"])
            requirement = requirements_by_provider.get(provider_id)
            if requirement is None:
                raise ValueError(f"provider requirement is missing for {provider_id}")
            satisfied = _comparison_matches(
                _fact_at_path(facts, str(requirement["required_fact_path"])),
                _json_object(requirement["comparison_json"]),
            )
            request_ids_by_satisfaction[satisfied].append(
                str(consumer["enrichment_request_id"])
            )
            previous = enrichment_state.get(provider_id)
            if previous is None:
                raise ValueError(f"enrichment state is missing for {provider_id}")
            enrichment_state[provider_id] = (
                "satisfied" if satisfied else "terminal",
                previous[1],
                "satisfied" if satisfied else "provider_output_missing_fact",
            )
        for satisfied, request_ids in request_ids_by_satisfaction.items():
            if not request_ids:
                continue
            placeholders = ",".join("?" for _ in request_ids)
            connection.execute(
                f"""
                UPDATE listing_enrichment_requests
                SET status = ?, resolution_outcome = ?, terminal_reason = ?
                WHERE enrichment_request_id IN ({placeholders})
                """,
                (
                    "satisfied" if satisfied else "terminal",
                    "satisfied" if satisfied else "provider_output_missing_fact",
                    None if satisfied else "missing_declared_fact",
                    *request_ids,
                ),
            )

    @staticmethod
    def _merge_current_fact_snapshot(
        *,
        facts: JsonObject,
        evidence_refs: JsonObject,
        current_snapshot: tuple[JsonObject, JsonObject] | None,
    ) -> tuple[JsonObject, JsonObject]:
        if current_snapshot is None:
            return facts, evidence_refs
        current_facts, current_evidence = current_snapshot
        current_facts = dict(current_facts)
        current_facts.pop("derived_facts", None)
        merged_evidence = dict(current_evidence)
        merged_evidence.update(evidence_refs)
        return _merge_fact_payloads(current_facts, facts), merged_evidence

    @staticmethod
    def _settle_terminal_dependencies(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        terminal_invocation_ids: tuple[str, ...],
        affected_listing_ids: tuple[str, ...] | None,
        selection_evaluator: Callable[[JsonObject], SelectionDecision],
        now: float,
    ) -> None:
        if not terminal_invocation_ids or affected_listing_ids == ():
            return
        placeholders = ",".join("?" for _ in terminal_invocation_ids)
        listing_filter = ""
        listing_parameters: tuple[str, ...] = ()
        if affected_listing_ids is not None:
            listing_placeholders = ",".join("?" for _ in affected_listing_ids)
            listing_filter = f" AND listing_id IN ({listing_placeholders})"
            listing_parameters = affected_listing_ids
        affected_rows = connection.execute(
            f"""
            SELECT DISTINCT listing_id
            FROM listing_enrichment_requests
            WHERE execution_id = ?
              AND invocation_id IN ({placeholders})
              {listing_filter}
            """,
            (execution_id, *terminal_invocation_ids, *listing_parameters),
        ).fetchall()
        connection.execute(
            f"""
            UPDATE listing_enrichment_requests
            SET status = 'terminal', resolution_outcome = 'provider_terminal', terminal_reason = 'invocation_terminal'
            WHERE execution_id = ?
              AND invocation_id IN ({placeholders})
              {listing_filter}
              AND status = 'waiting'
            """,
            (execution_id, *terminal_invocation_ids, *listing_parameters),
        )
        settled_listing_ids = tuple(str(row["listing_id"]) for row in affected_rows)
        if not settled_listing_ids:
            return
        settled_placeholders = ",".join("?" for _ in settled_listing_ids)
        dependency_rows_by_listing: dict[str, list[sqlite3.Row]] = {}
        for dependency in connection.execute(
            f"""
            SELECT listing_id, status, required, invocation_id
            FROM listing_enrichment_requests
            WHERE execution_id = ? AND listing_id IN ({settled_placeholders})
            """,
            (execution_id, *settled_listing_ids),
        ).fetchall():
            dependency_rows_by_listing.setdefault(str(dependency["listing_id"]), []).append(
                dependency
            )
        current_snapshots = SqliteGraphRepository._current_fact_snapshots(
            connection,
            execution_id=execution_id,
            listing_ids=settled_listing_ids,
        )
        for listing_id in settled_listing_ids:
            dependency_rows = dependency_rows_by_listing.get(listing_id, [])
            if any(row["status"] == "waiting" for row in dependency_rows):
                continue
            current_snapshot = current_snapshots.get(listing_id)
            if current_snapshot is None:
                continue
            facts, current_evidence_refs = current_snapshot
            evidence_refs = dict(current_evidence_refs)
            terminal_ids = tuple(
                sorted(
                    str(row["invocation_id"])
                    for row in dependency_rows
                    if row["status"] == "terminal" and row["invocation_id"] is not None
                )
            )
            evidence_refs["terminalDependencyInvocationIds"] = terminal_ids
            fingerprint = _fingerprint(facts)
            fact_set_id = _stable_id("fact-set", execution_id, listing_id, fingerprint)
            connection.execute(
                """
                INSERT OR IGNORE INTO fact_sets (
                    fact_set_id, execution_id, listing_id, evidence_refs_json,
                    materialized_facts_json, fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_set_id,
                    execution_id,
                    listing_id,
                    _json_dumps(evidence_refs),
                    _json_dumps(facts),
                    fingerprint,
                    now,
                ),
            )
            required_failed = any(
                bool(row["required"]) and row["status"] == "terminal" for row in dependency_rows
            )
            selection = selection_evaluator(facts)
            outcome = "reject" if required_failed or not selection.keep else "keep"
            reasons = (
                ("required_provider_terminal",)
                if required_failed
                else selection.reasons or ("optional_provider_terminal",)
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO selection_evaluations (
                    evaluation_id, execution_id, listing_id, fact_set_id,
                    stage, outcome, reason_codes_json
                ) VALUES (?, ?, ?, ?, 'final', ?, ?)
                """,
                (
                    _stable_id("evaluation", fact_set_id, "final"),
                    execution_id,
                    listing_id,
                    fact_set_id,
                    outcome,
                    _json_dumps(reasons),
                ),
            )

    @staticmethod
    def _advance_events(
        connection: sqlite3.Connection,
        execution_id: str,
        events: tuple[StoredDomainEvent, ...],
        *,
        now: float,
    ) -> None:
        for event in events:
            connection.execute(
                """
                UPDATE domain_events
                SET processing_offset = processing_offset + ?,
                    processed_at = CASE WHEN ? THEN ? ELSE processed_at END
                WHERE execution_id = ? AND event_id = ? AND processed_at IS NULL
                """,
                (
                    event.processing_advance,
                    event.processing_complete,
                    now,
                    execution_id,
                    event.event_id,
                ),
            )

    @staticmethod
    def _source_plan(connection: sqlite3.Connection, source_plan_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM source_plans WHERE source_plan_id = ?",
            (source_plan_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown source plan: {source_plan_id}")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _execution_run_id(connection: sqlite3.Connection, execution_id: str) -> str:
        row = connection.execute(
            "SELECT run_id FROM search_executions WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown execution: {execution_id}")
        return str(row["run_id"])

    @staticmethod
    def _validate_search_result(
        parser_input: SearchListingInput,
        result: SearchListingResult,
        source_plan: sqlite3.Row,
    ) -> None:
        if int(source_plan["units_used"]) + result.collection_units_consumed > int(source_plan["unit_budget"]):
            raise ValueError("source plan collection-unit budget exceeded")
        for item in result.items:
            if item.source_id != parser_input.source_id:
                raise ValueError("listing source_id must match parser input")
            if item.target_provider_id != parser_input.target_provider_id:
                raise ValueError("listing target_provider_id must match parser input")
        for continuation in result.continuations:
            invariant = replace(continuation, cursor=parser_input.cursor, resolved_state=parser_input.resolved_state)
            if invariant != parser_input:
                raise ValueError("continuation may change only cursor and resolved_state")

    @staticmethod
    def _bounded_search_result(
        result: SearchListingResult,
        source_plan: sqlite3.Row,
    ) -> tuple[SearchListingResult, str | None]:
        remaining_items = max(int(source_plan["item_budget"]) - int(source_plan["items_used"]), 0)
        bounded_items = result.items[:remaining_items]
        item_limit_reached = len(bounded_items) < len(result.items) or (
            bool(result.continuations)
            and int(source_plan["items_used"]) + len(bounded_items) >= int(source_plan["item_budget"])
        )
        unit_limit_reached = bool(result.continuations) and (
            int(source_plan["units_used"]) + result.collection_units_consumed
            >= int(source_plan["unit_budget"])
        )
        limit_reason = "item_limit" if item_limit_reached else "collection_unit_limit" if unit_limit_reached else None
        bounded_continuations = () if limit_reason is not None else result.continuations
        bounded_outcome = result.outcome
        if not bounded_items and not bounded_continuations:
            bounded_outcome = SearchResultOutcome.NO_RESULTS
        return (
            replace(
                result,
                outcome=bounded_outcome,
                items=bounded_items,
                continuations=bounded_continuations,
            ),
            limit_reason,
        )

    @staticmethod
    def _resolve_vacancy_claims(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        claims: tuple[tuple[str, str | None, str], ...],
        now: float,
    ) -> tuple[str, ...]:
        if not claims:
            return ()
        normalized_urls = tuple(dict.fromkeys(claim[2] for claim in claims))
        url_placeholders = ",".join("?" for _ in normalized_urls)
        url_rows = connection.execute(
            f"""
            SELECT normalized_url, vacancy_id
            FROM vacancy_url_aliases
            WHERE run_id = ? AND normalized_url IN ({url_placeholders})
            """,
            (run_id, *normalized_urls),
        ).fetchall()
        vacancy_by_url = {
            str(row["normalized_url"]): str(row["vacancy_id"])
            for row in url_rows
        }
        provider_claims = tuple(
            dict.fromkeys(
                _provider_listing_claim(provider_id, source_listing_id)
                for provider_id, source_listing_id, _url in claims
                if source_listing_id is not None
            )
        )
        vacancy_by_provider_claim: dict[str, str] = {}
        if provider_claims:
            provider_placeholders = ",".join("?" for _ in provider_claims)
            provider_rows = connection.execute(
                f"""
                SELECT claim_value, vacancy_id
                FROM vacancy_provider_aliases
                WHERE run_id = ? AND claim_value IN ({provider_placeholders})
                """,
                (run_id, *provider_claims),
            ).fetchall()
            vacancy_by_provider_claim = {
                str(row["claim_value"]): str(row["vacancy_id"])
                for row in provider_rows
            }

        resources: dict[str, tuple[object, ...]] = {}
        url_aliases: dict[str, tuple[object, ...]] = {}
        provider_aliases: dict[str, tuple[object, ...]] = {}
        resolved: list[str] = []
        for provider_id, source_listing_id, canonical_url in claims:
            provider_claim = (
                None
                if source_listing_id is None
                else _provider_listing_claim(provider_id, source_listing_id)
            )
            url_vacancy_id = vacancy_by_url.get(canonical_url)
            provider_vacancy_id = (
                None
                if provider_claim is None
                else vacancy_by_provider_claim.get(provider_claim)
            )
            if (
                url_vacancy_id is not None
                and provider_vacancy_id is not None
                and url_vacancy_id != provider_vacancy_id
            ):
                raise ValueError("vacancy identity claims resolve to different resources")
            vacancy_id = (
                url_vacancy_id
                or provider_vacancy_id
                or _stable_id("vacancy", run_id, f"url:{canonical_url}")
            )
            vacancy_by_url[canonical_url] = vacancy_id
            if provider_claim is not None:
                vacancy_by_provider_claim[provider_claim] = vacancy_id
            resources.setdefault(
                vacancy_id,
                (
                    vacancy_id,
                    run_id,
                    provider_id,
                    source_listing_id,
                    canonical_url,
                    f"url:{canonical_url}",
                    2,
                    now,
                ),
            )
            url_aliases[canonical_url] = (
                _stable_id("vacancy-url-alias", run_id, canonical_url),
                run_id,
                vacancy_id,
                canonical_url,
                1,
            )
            if provider_claim is not None and source_listing_id is not None:
                provider_aliases[provider_claim] = (
                    _stable_id("vacancy-provider-alias", run_id, provider_claim),
                    run_id,
                    vacancy_id,
                    provider_id,
                    source_listing_id,
                    provider_claim,
                )
            resolved.append(vacancy_id)

        connection.executemany(
            """
            INSERT OR IGNORE INTO vacancy_resources (
                vacancy_id, run_id, target_provider_id, source_listing_id, canonical_url,
                identity_key, identity_schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            resources.values(),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO vacancy_url_aliases (
                vacancy_url_alias_id, run_id, vacancy_id, normalized_url, normalizer_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            url_aliases.values(),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO vacancy_provider_aliases (
                vacancy_provider_alias_id, run_id, vacancy_id, target_provider_id,
                source_listing_id, claim_value
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            provider_aliases.values(),
        )
        return tuple(resolved)

    @staticmethod
    def _store_listing_observations(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        invocation: LeasedParserInvocation,
        manifest: ParserManifest,
        items: tuple[SearchListingOutput, ...],
        now: float,
    ) -> tuple[str, ...]:
        company_groups = tuple(
            (
                None if item.company is None else item.company.name,
                _company_claims(item.company, default_provider_id=item.target_provider_id),
            )
            for item in items
        )
        company_ids = SqliteGraphRepository._resolve_company_ids(
            connection,
            run_id=run_id,
            groups=company_groups,
            now=now,
        )
        vacancy_ids = SqliteGraphRepository._resolve_vacancy_claims(
            connection,
            run_id=run_id,
            claims=tuple(
                (
                    item.target_provider_id,
                    item.source_listing_id,
                    _normalize_url(item.vacancy_url),
                )
                for item in items
            ),
            now=now,
        )
        listings: list[tuple[object, ...]] = []
        observations: list[tuple[object, ...]] = []
        company_claims: list[tuple[object, ...]] = []
        observation_ids: list[str] = []
        source_plan_id = invocation.spec.source_plan_id
        if source_plan_id is None:
            raise ValueError("listing observation requires source plan")
        for item, company_id, vacancy_id, (_, claims) in zip(
            items,
            company_ids,
            vacancy_ids,
            company_groups,
            strict=True,
        ):
            canonical_url = _normalize_url(item.vacancy_url)
            listing_identity = item.source_listing_id or canonical_url
            listing_id = _stable_id("listing", run_id, item.source_id, listing_identity)
            item_key = item.source_listing_id or canonical_url
            observation_id = _stable_id("listing-observation", invocation.invocation_id, item_key)
            listings.append(
                (
                    listing_id,
                    run_id,
                    vacancy_id,
                    company_id,
                    item.source_id,
                    item.source_listing_id,
                    listing_identity,
                    now,
                )
            )
            observations.append(
                (
                    observation_id,
                    listing_id,
                    invocation.spec.execution_id,
                    source_plan_id,
                    invocation.invocation_id,
                    manifest.output_schema_id,
                    item_key,
                    _json_dumps(item),
                    now,
                )
            )
            observation_ids.append(observation_id)
            if company_id is not None:
                company_claims.extend(
                    (
                        _stable_id("company-claim", run_id, claim_type, claim_value),
                        run_id,
                        company_id,
                        claim_type,
                        claim_value,
                        observation_id,
                    )
                    for claim_type, claim_value in claims
                )
        connection.executemany(
            """
            INSERT OR IGNORE INTO vacancy_listings (
                listing_id, run_id, vacancy_id, company_id, source_id,
                source_listing_id, identity_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            listings,
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO listing_observations (
                listing_observation_id, listing_id, execution_id, source_plan_id, invocation_id,
                output_schema_id, item_key, payload_json, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            observations,
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO company_identity_claims (
                company_claim_id, run_id, company_id, claim_type, claim_value,
                listing_observation_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            company_claims,
        )
        return tuple(observation_ids)

    def _store_continuations(
        self,
        connection: sqlite3.Connection,
        *,
        invocation: LeasedParserInvocation,
        manifest: ParserManifest,
        continuations: tuple[SearchListingInput, ...],
        source_plan: sqlite3.Row,
        now: float,
    ) -> tuple[int, bool]:
        existing_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM parser_invocations WHERE source_plan_id = ?",
                (source_plan["source_plan_id"],),
            ).fetchone()[0]
        )
        remaining = max(int(source_plan["invocation_budget"]) - existing_count, 0)
        bounded_continuations = continuations[:remaining]
        truncated = len(bounded_continuations) < len(continuations)
        inserted = 0
        for continuation in bounded_continuations:
            fingerprint = _fingerprint(continuation)
            task_key = f"search_listing:{manifest.parser_id}:{source_plan['source_plan_id']}:{fingerprint}"
            before = connection.total_changes
            self._enqueue_invocation(
                connection,
                ParserInvocationSpec(
                    execution_id=invocation.spec.execution_id,
                    source_plan_id=str(source_plan["source_plan_id"]),
                    parent_invocation_id=invocation.invocation_id,
                    cause_event_id=None,
                    parser_ref=manifest.ref,
                    parser_type=ParserType.SEARCH_LISTING,
                    input_schema_id=manifest.input_schema_id,
                    parser_input=continuation,
                    task_class=TaskClass.LISTING,
                    task_key=task_key,
                    available_at=now,
                    reserved_collection_units=manifest.max_units_per_invocation,
                    resource_key_resolved=False,
                ),
            )
            if connection.total_changes > before:
                inserted += 1
        return inserted, truncated

    @staticmethod
    def _update_source_plan_after_search_result(
        connection: sqlite3.Connection,
        *,
        source_plan: sqlite3.Row,
        result: SearchListingResult,
        continuation_count: int,
        plan_limit_reason: str | None,
    ) -> None:
        units_used = int(source_plan["units_used"]) + result.collection_units_consumed
        items_used = int(source_plan["items_used"]) + len(result.items)
        invocations_used = int(source_plan["invocations_used"]) + 1
        has_pending_invocations = connection.execute(
            """
            SELECT 1 FROM parser_invocations
            WHERE source_plan_id = ? AND status IN ('queued', 'leased', 'waiting')
            LIMIT 1
            """,
            (source_plan["source_plan_id"],),
        ).fetchone() is not None
        previous_reason = _optional_text(source_plan["terminal_reason"])
        failure_reason = (
            previous_reason
            if previous_reason is not None and previous_reason not in _SOURCE_LIMIT_REASONS
            else None
        )
        if result.outcome == SearchResultOutcome.PARTIAL_SUCCESS and failure_reason is None:
            failure_reason = "partial_success"
        persisted_limit_reason = plan_limit_reason or (
            previous_reason if previous_reason in _SOURCE_LIMIT_REASONS else None
        )
        terminal_reason: str | None
        if failure_reason is not None:
            status = "partial" if items_used else "failed"
            terminal_reason = failure_reason
        elif continuation_count or has_pending_invocations:
            status = "running"
            terminal_reason = persisted_limit_reason
        elif persisted_limit_reason is not None:
            status = "limit_reached"
            terminal_reason = persisted_limit_reason
        elif items_used:
            status = "succeeded"
            terminal_reason = None
        else:
            status = "no_results"
            terminal_reason = None
        connection.execute(
            """
            UPDATE source_plans
            SET units_used = ?, items_used = ?, invocations_used = ?, status = ?, terminal_reason = ?
            WHERE source_plan_id = ?
            """,
            (
                units_used,
                items_used,
                invocations_used,
                status,
                terminal_reason,
                source_plan["source_plan_id"],
            ),
        )

    class _Transaction:
        def __init__(self, repository: SqliteGraphRepository) -> None:
            self._repository = repository

        def __enter__(self) -> sqlite3.Connection:
            self._repository._lock.acquire()
            self._repository._ensure_open()
            self._repository._connection.execute("BEGIN IMMEDIATE")
            return self._repository._connection

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            try:
                if exc_type is None:
                    self._repository._connection.commit()
                else:
                    self._repository._connection.rollback()
            finally:
                self._repository._lock.release()

    def _transaction(self) -> _Transaction:
        return self._Transaction(self)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("graph repository is closed")


def _schema_sql() -> str:
    return Path(__file__).with_name("graph_schema.sql").read_text(encoding="utf-8")


def _invocation_spec(
    row: sqlite3.Row,
    *,
    reserved_collection_units: int | None = None,
) -> ParserInvocationSpec:
    parser_type = ParserType(str(row["parser_type"]))
    return ParserInvocationSpec(
        execution_id=str(row["execution_id"]),
        source_plan_id=_optional_text(row["source_plan_id"]),
        parent_invocation_id=_optional_text(row["parent_invocation_id"]),
        cause_event_id=_optional_text(row["cause_event_id"]),
        parser_ref=ParserRef(str(row["parser_id"]), str(row["parser_version"])),
        parser_type=parser_type,
        input_schema_id=str(row["input_schema_id"]),
        parser_input=_parser_input(parser_type, _json_object(row["input_json"])),
        task_class=TaskClass(str(row["task_class"])),
        task_key=str(row["task_key"]),
        available_at=float(row["available_at"]),
        resource_key=_optional_text(row["resource_key"]),
        resource_key_resolved=bool(row["resource_key_resolved"]),
        reserved_collection_units=(
            reserved_collection_units
            if reserved_collection_units is not None
            else (
                None
                if row["reserved_collection_units"] is None
                else int(row["reserved_collection_units"])
            )
        ),
    )


def _leased_invocation(
    row: sqlite3.Row,
    owner_id: str,
    token: str,
    lease_until: float,
    *,
    reserved_collection_units: int | None = None,
) -> LeasedParserInvocation:
    spec = _invocation_spec(
        row,
        reserved_collection_units=reserved_collection_units,
    )
    return LeasedParserInvocation(str(row["invocation_id"]), spec, owner_id, token, lease_until)


def _parser_input(
    parser_type: ParserType,
    payload: JsonObject,
) -> SearchListingInput | VacancyDetailInput | CompanyProfileInput | CompanySiteInput:
    if parser_type == ParserType.SEARCH_LISTING:
        return SearchListingInput(
            source_id=_text(payload, "source_id"),
            target_provider_id=_text(payload, "target_provider_id"),
            queries=tuple(_string_list(payload, "queries")),
            target=_object(payload, "target"),
            cursor=_object(payload, "cursor"),
            native_filters=_object(payload, "native_filters"),
            resolved_state=_optional_object(payload, "resolved_state"),
        )
    if parser_type == ParserType.VACANCY_DETAIL:
        return VacancyDetailInput(
            target_provider_id=_text(payload, "target_provider_id"),
            vacancy_url=_text(payload, "vacancy_url"),
            source_listing_id=_optional_payload_text(payload, "source_listing_id"),
        )
    if parser_type == ParserType.COMPANY_PROFILE:
        return CompanyProfileInput(
            target_provider_id=_text(payload, "target_provider_id"),
            company_profile_url=_text(payload, "company_profile_url"),
            source_company_id=_optional_payload_text(payload, "source_company_id"),
        )
    return CompanySiteInput(site_url=_text(payload, "site_url"))


def _stored_event(row: sqlite3.Row) -> StoredDomainEvent:
    return StoredDomainEvent(
        event_id=str(row["event_id"]),
        execution_id=str(row["execution_id"]),
        producer_invocation_id=_optional_text(row["producer_invocation_id"]),
        event_type=str(row["event_type"]),
        schema_version=int(row["schema_version"]),
        payload=_json_object(row["payload_json"]),
        occurred_at=float(row["occurred_at"]),
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{uuid5(NAMESPACE_URL, ':'.join(parts)).hex}"


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_json_dumps(value).encode()).hexdigest()


def _normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _provider_listing_claim(provider_id: str, source_listing_id: str) -> str:
    return _json_dumps((provider_id, source_listing_id))


def _company_claims(
    company: CompanyRef | None,
    *,
    default_provider_id: str,
) -> tuple[tuple[str, str], ...]:
    if company is None:
        return ()
    source_company_id = company.source_company_id
    target_provider_id = company.target_provider_id or default_provider_id
    profile_url = company.profile_url
    official_site_url = company.official_site_url
    claims: list[tuple[str, str]] = []
    if source_company_id is not None:
        claims.append(("provider_id", f"{target_provider_id}:{source_company_id}"))
    if profile_url is not None:
        claims.append(("profile_url", _normalize_url(profile_url)))
    if official_site_url is not None:
        claims.append(("verified_domain", _verified_domain(official_site_url)))
    return tuple(dict.fromkeys(claims))


def _verified_domain(url: str) -> str:
    host = urlsplit(url).hostname
    if host is None:
        raise ValueError("site URL is missing a host")
    normalized = host.casefold().rstrip(".")
    return normalized.removeprefix("www.")


def _json_dumps(value: object) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: object) -> JsonObject:
    parsed: Any = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("stored JSON must be an object")
    return parsed


def _json_string_list(value: object, name: str) -> tuple[str, ...]:
    parsed: Any = json.loads(str(value))
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError(f"{name} must be a JSON string list")
    return tuple(parsed)


def _text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_payload_text(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _active_runtime_ms(row: sqlite3.Row, *, now: float) -> int:
    active_runtime_ms = int(row["execution_runtime_ms"])
    session_started_at = row["execution_session_started_at"]
    if session_started_at is None:
        return active_runtime_ms
    return active_runtime_ms + max(
        0,
        round((now - float(session_started_at)) * 1000),
    )


def _object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _optional_object(payload: JsonObject, key: str) -> JsonObject | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object or null")
    return value


def _string_list(payload: JsonObject, key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a string list")
    return value


def _fact_at_path(payload: JsonObject, path: str) -> object:
    current: object = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _merge_fact_payloads(base: JsonObject, enrichment: JsonObject) -> JsonObject:
    merged = dict(base)
    for key, value in enrichment.items():
        if value in (None, "", [], {}):
            continue
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_fact_payloads(existing, value)
        else:
            merged[key] = value
    return merged


def _canonical_graph_facts(facts: JsonObject) -> JsonObject:
    company = facts.get("company")
    if not isinstance(company, dict) or facts.get("official_site_url"):
        return facts
    official_site_url = company.get("official_site_url")
    if not isinstance(official_site_url, str) or not official_site_url:
        return facts
    return {**facts, "official_site_url": official_site_url}


def _normalized_duplicate_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"[\w+#]+", value.casefold()))


def _normalized_duplicate_location(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return _normalized_duplicate_text(value.get("text"))


def _comparison_matches(value: object, comparison: JsonObject) -> bool:
    operator = comparison.get("operator")
    if operator == "exists":
        return value is not _MISSING and value not in (None, "", [], {})
    if operator == "known":
        return _known_fact(value)
    if value is _MISSING:
        return False
    expected = comparison.get("value")
    if operator == "equals":
        return value == expected
    if operator == "contains_any":
        candidates = comparison.get("values")
        return isinstance(value, list) and isinstance(candidates, list) and bool(set(value) & set(candidates))
    if operator == "gte":
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and isinstance(expected, int | float)
            and not isinstance(expected, bool)
            and value >= expected
        )
    raise ValueError(f"unsupported fact comparison operator: {operator}")


def _known_fact(value: object) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.casefold() != "unknown"
    if isinstance(value, dict):
        return any(_known_fact(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_known_fact(item) for item in value)
    return True


def _required_json_text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_json_text(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _keep_selection(_facts: JsonObject) -> SelectionDecision:
    return SelectionDecision(outcome=SelectionOutcome.KEEP, reasons=())


def read_graph_processed_payload(
    database_path: Path,
    *,
    projector: Callable[[JsonObject], JsonObject],
    append_sequence: int | None = None,
) -> JsonObject:
    if not database_path.exists():
        raise FileNotFoundError(f"run database does not exist: {database_path}")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        if append_sequence is None:
            execution = connection.execute(
                """
                SELECT execution.*, intent.intent_json
                FROM search_executions AS execution
                JOIN search_intents AS intent ON intent.intent_id = execution.intent_id
                WHERE execution.status = 'completed'
                  AND execution.execution_kind = 'search'
                ORDER BY execution.append_sequence DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            execution = connection.execute(
                """
                SELECT execution.*, intent.intent_json
                FROM search_executions AS execution
                JOIN search_intents AS intent ON intent.intent_id = execution.intent_id
                WHERE execution.status = 'completed'
                  AND execution.execution_kind = 'search'
                  AND execution.append_sequence = ?
                ORDER BY execution.created_at DESC
                LIMIT 1
                """,
                (append_sequence,),
            ).fetchone()
        if execution is None:
            raise FileNotFoundError("completed graph execution was not found")
        item_rows = connection.execute(
            """
            SELECT final.payload_json
            FROM final_vacancies AS final
            JOIN vacancy_listings AS listing ON listing.listing_id = final.listing_id
            WHERE final.execution_id = ?
            ORDER BY final.score DESC, listing.vacancy_id, final.listing_id
            """,
            (execution["execution_id"],),
        ).fetchall()
        filtered_rows = _filtered_vacancy_rows(
            connection,
            str(execution["execution_id"]),
        )
        observation_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM listing_observations WHERE execution_id = ?",
                (execution["execution_id"],),
            ).fetchone()[0]
        )
        source_status_counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM source_plans "
                "WHERE execution_id = ? GROUP BY status ORDER BY status",
                (execution["execution_id"],),
            )
        }
        required_enrichment_failures = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM listing_enrichment_requests
                WHERE execution_id = ? AND required = 1 AND status = 'terminal'
                  AND COALESCE(resolution_outcome, '') != 'satisfied'
                """,
                (execution["execution_id"],),
            ).fetchone()[0]
        )
    items = tuple(_json_object(row["payload_json"]) for row in item_rows)
    filtered_items = _project_filtered_vacancy_rows(filtered_rows, projector)
    execution_quality, source_coverage = _execution_quality_and_coverage(
        source_status_counts,
        observation_count=observation_count,
        required_enrichment_failures=required_enrichment_failures,
    )
    return {
        "schema_version": 2,
        "record_type": "processed_results",
        "phase": "final",
        "run_id": str(execution["run_id"]),
        "execution_id": str(execution["execution_id"]),
        "append_sequence": int(execution["append_sequence"]),
        "execution_quality": execution_quality,
        "source_coverage": source_coverage,
        "search_request": _json_object(execution["intent_json"]),
        "raw_records_read": observation_count,
        "result_count": len(items),
        "results": list(items),
        "filtered_out_results": list(filtered_items),
    }


def _execution_quality_and_coverage(
    source_status_counts: dict[str, int],
    *,
    observation_count: int,
    required_enrichment_failures: int = 0,
) -> tuple[str, JsonObject]:
    complete_source_count = sum(
        count
        for status, count in source_status_counts.items()
        if status in {"succeeded", "no_results", "limit_reached"}
    )
    planned_source_count = sum(source_status_counts.values())
    degraded_source_count = planned_source_count - complete_source_count
    failed_source_count = sum(
        count
        for status, count in source_status_counts.items()
        if status in {"failed", "cancelled"}
    )
    if degraded_source_count == 0 and required_enrichment_failures == 0:
        execution_quality = "complete"
    elif observation_count == 0 and complete_source_count == 0:
        execution_quality = "failed"
    else:
        execution_quality = "degraded"
    return execution_quality, {
        "planned": planned_source_count,
        "complete": complete_source_count,
        "degraded": degraded_source_count,
        "failed": failed_source_count,
        "status_counts": source_status_counts,
    }


def _filtered_vacancy_rows(
    connection: sqlite3.Connection,
    execution_id: str,
) -> tuple[sqlite3.Row, ...]:
    return tuple(
        connection.execute(
            """
            WITH ranked AS (
                SELECT
                    evaluation.listing_id,
                    evaluation.reason_codes_json,
                    fact_set.materialized_facts_json,
                    ROW_NUMBER() OVER (
                        PARTITION BY evaluation.listing_id
                        ORDER BY fact_set.created_at DESC, evaluation.rowid DESC
                    ) AS snapshot_rank
                FROM selection_evaluations AS evaluation
                JOIN fact_sets AS fact_set ON fact_set.fact_set_id = evaluation.fact_set_id
                WHERE evaluation.execution_id = ?
                  AND evaluation.stage = 'final'
                  AND evaluation.outcome = 'reject'
            )
            SELECT materialized_facts_json, reason_codes_json
            FROM ranked
            WHERE snapshot_rank = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM json_each(ranked.reason_codes_json)
                  WHERE json_each.value = 'query_mismatch'
              )
            ORDER BY listing_id
            """,
            (execution_id,),
        ).fetchall()
    )


def _project_filtered_vacancy_rows(
    rows: tuple[sqlite3.Row, ...],
    projector: Callable[[JsonObject], JsonObject],
) -> tuple[JsonObject, ...]:
    projected: list[JsonObject] = []
    for row in rows:
        item = projector(_json_object(row["materialized_facts_json"]))
        item["decision"] = "filtered_out"
        item["decision_reasons"] = list(_json_string_list(row["reason_codes_json"], "reasons"))
        projected.append(item)
    return tuple(projected)


def _provider_task_key(
    stage: ProviderStage,
    parser_ref: ParserRef,
    target: _ProviderTarget,
) -> str:
    if stage == ProviderStage.SITE_OUTPUT:
        return f"company_site:{parser_ref.parser_id}:{target.normalized_url}"
    prefix = (
        "vacancy_detail"
        if stage == ProviderStage.DETAIL_OUTPUT
        else "company_profile"
    )
    return (
        f"{prefix}:{parser_ref.parser_id}:"
        f"{target.provider_hint}:{target.normalized_url}"
    )
