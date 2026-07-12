"""SQLite repository for the durable independent-scraper execution graph."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from job_harness.v2.contracts import (
    CompanyProfileInput,
    CompanyProfileResult,
    CompanyRef,
    CompanySiteInput,
    CompanySiteResult,
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
    StaleLeaseError,
    StoredDomainEvent,
    TargetResolution,
    TaskClass,
    VacancyDetailInput,
    VacancyDetailResult,
)
from job_harness.v2.serialization import JsonObject, to_jsonable

_MISSING = object()
_MIN_DUPLICATE_MEMBERS = 2


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
        deadline_at: float,
        discovery_plan_budget: int = 20,
        now: float = 0.0,
    ) -> str:
        _require_text(run_id, "run_id")
        _require_text(policy_version, "policy_version")
        _require_text(runtime_config_version, "runtime_config_version")
        if append_sequence < 0:
            raise ValueError("append_sequence must be >= 0")
        if discovery_plan_budget < 0:
            raise ValueError("discovery_plan_budget must be >= 0")
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
                    status,
                    policy_version,
                    runtime_config_version,
                    deadline_at,
                    discovery_plan_budget,
                    created_at
                )
                VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    run_id,
                    intent_id,
                    append_sequence,
                    policy_version,
                    runtime_config_version,
                    deadline_at,
                    discovery_plan_budget,
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
    ) -> str:
        _require_text(criterion, "criterion")
        _require_text(fact_path, "fact_path")
        requirement_id = _stable_id("requirement", source_plan_id, criterion, fact_path)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO criterion_requirements (
                    requirement_id, source_plan_id, criterion, required_fact_path,
                    comparison_json, unsupported_reason
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (requirement_id, source_plan_id, criterion, fact_path, _json_dumps(comparison)),
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
        return requirement_id

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
    ) -> tuple[LeasedParserInvocation, ...]:
        _require_text(owner_id, "owner_id")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        leased: list[LeasedParserInvocation] = []
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE parser_attempts
                SET finished_at = ?, outcome = 'lease_expired',
                    failure_kind = 'lease_expired', retryable = 1
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
                    COALESCE(SUM(
                        CASE
                            WHEN invocation.status = 'leased' AND invocation.lease_until > ?
                            THEN invocation.reserved_collection_units
                            ELSE 0
                        END
                    ), 0) AS active_reservations
                FROM source_plans AS plan
                LEFT JOIN parser_invocations AS invocation
                  ON invocation.source_plan_id = plan.source_plan_id
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
            rows = connection.execute(
                """
                SELECT *
                FROM parser_invocations
                WHERE execution_id = ? AND status IN ('queued', 'retry_wait') AND available_at <= ?
                ORDER BY
                    CASE task_class
                        WHEN 'detail' THEN 0
                        WHEN 'profile' THEN 1
                        WHEN 'site' THEN 2
                        ELSE 3
                    END,
                    created_at,
                    invocation_id
                LIMIT ?
                """,
                (execution_id, now, limit),
            ).fetchall()
            for row in rows:
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
        return tuple(leased)

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

    def commit_retry(
        self,
        invocation: LeasedParserInvocation,
        *,
        attempt_id: str,
        failure_kind: str,
        available_at: float,
        now: float,
    ) -> None:
        if available_at <= now:
            raise ValueError("retry available_at must be later than now")
        with self._transaction() as connection:
            self._assert_current_lease(connection, invocation, now)
            self._finish_parser_attempt(
                connection,
                invocation_id=invocation.invocation_id,
                attempt_id=attempt_id,
                outcome=failure_kind,
                failure_kind=failure_kind,
                retryable=True,
                now=now,
            )
            connection.execute(
                """
                UPDATE parser_invocations
                SET status = 'retry_wait', available_at = ?,
                    lease_owner = NULL, lease_token = NULL, lease_until = NULL
                WHERE invocation_id = ?
                """,
                (available_at, invocation.invocation_id),
            )

    def commit_search_result(
        self,
        invocation: LeasedParserInvocation,
        result: SearchListingResult,
        manifest: ParserManifest,
        *,
        attempt_id: str | None = None,
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
            self._validate_search_result(parser_input, result, source_plan)

            run_id = self._execution_run_id(connection, invocation.spec.execution_id)
            observation_ids = self._store_listing_observations(
                connection,
                run_id=run_id,
                invocation=invocation,
                manifest=manifest,
                items=result.items,
                now=now,
            )
            continuation_count = self._store_continuations(
                connection,
                invocation=invocation,
                manifest=manifest,
                continuations=result.continuations,
                source_plan=source_plan,
                now=now,
            )
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
                self._finish_parser_attempt_success(connection, invocation, attempt_id, now)
            self._update_source_plan_after_search_result(
                connection,
                source_plan=source_plan,
                result=result,
                continuation_count=continuation_count,
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
                identity_key = (
                    f"provider:{item.target_provider_id}:{item.source_listing_id}"
                    if item.source_listing_id
                    else f"url:{canonical_url}"
                )
                vacancy_id = _stable_id("vacancy", run_id, identity_key)
                observation_id = _stable_id("detail-observation", invocation.invocation_id)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO vacancy_resources (
                        vacancy_id, run_id, target_provider_id, source_listing_id, canonical_url,
                        identity_key, identity_schema_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        vacancy_id,
                        run_id,
                        item.target_provider_id,
                        item.source_listing_id,
                        canonical_url,
                        identity_key,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO vacancy_url_aliases (
                        vacancy_url_alias_id, vacancy_id, normalized_url, normalizer_version
                    ) VALUES (?, ?, ?, 1)
                    """,
                    (_stable_id("vacancy-url-alias", vacancy_id, canonical_url), vacancy_id, canonical_url),
                )
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
                self._finish_parser_attempt_success(connection, invocation, attempt_id, now)
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
                self._finish_parser_attempt_success(connection, invocation, attempt_id, now)
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
                self._finish_parser_attempt_success(connection, invocation, attempt_id, now)
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
        retryable: bool,
        public_notice: str | None,
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
                    retryable=retryable,
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
                    "retryable": retryable,
                    "publicNotice": public_notice,
                    "eventSchemaVersion": 1,
                },
                now=now,
            )

    def settle_deadline(self, execution_id: str, *, now: float) -> bool:
        with self._transaction() as connection:
            execution = connection.execute(
                "SELECT status, deadline_at FROM search_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if execution is None:
                raise KeyError(f"unknown execution: {execution_id}")
            if float(execution["deadline_at"]) > now:
                return False
            if execution["status"] in {"completed", "failed", "assembling"}:
                return False
            cancelled_rows = connection.execute(
                """
                SELECT invocation_id FROM parser_invocations
                WHERE execution_id = ? AND status IN ('queued', 'leased', 'retry_wait')
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
                        failure_kind = 'execution_deadline', retryable = 0
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
                WHERE execution_id = ? AND status IN ('queued', 'leased', 'retry_wait')
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
                now=now,
            )
            connection.execute(
                """
                UPDATE search_executions
                SET status = 'stopping', completion_reason = 'deadline',
                    coordinator_owner = NULL, coordinator_token = NULL,
                    coordinator_lease_until = NULL
                WHERE execution_id = ?
                """,
                (execution_id,),
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

    def read_unprocessed_events(self, execution_id: str, *, limit: int) -> tuple[StoredDomainEvent, ...]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT * FROM domain_events
                WHERE execution_id = ? AND processed_at IS NULL
                ORDER BY occurred_at, event_id
                LIMIT ?
                """,
                (execution_id, limit),
            ).fetchall()
        return tuple(_stored_event(row) for row in rows)

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
        selection_evaluator: Callable[[JsonObject], SelectionDecision],
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
        target_resolver: Callable[[ParserType, str], TargetResolution],
        discovered_planner: Callable[
            [ParserRef, JsonObject], tuple[SearchListingInput, ...]
        ],
        *,
        now: float,
    ) -> None:
        event_ids = tuple(event.event_id for event in events)
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
            detail_consumers = self._detail_consumer_rows(connection, tuple(detail_events))
            profile_consumers = self._profile_consumer_rows(connection, tuple(profile_events))
            site_consumers = self._site_consumer_rows(connection, tuple(site_events))
            source_plan_ids = {
                str(row["source_plan_id"])
                for row in (
                    *latest_by_listing.values(),
                    *detail_consumers,
                    *profile_consumers,
                    *site_consumers,
                )
            }
            requirements = self._requirements_for_source_plans(
                connection,
                tuple(source_plan_ids),
            )
            for row in latest_by_listing.values():
                event = observation_events[str(row["listing_observation_id"])]
                self._materialize_and_schedule_listing(
                    connection,
                    row=row,
                    event=event,
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    manifest_resolver=manifest_resolver,
                    selection_evaluator=selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            completed_provider_ids, failed_provider_ids = self._terminal_waiting_provider_invocations(
                connection,
                execution_id=coordinator.execution_id,
                listing_ids=tuple(latest_by_listing),
            )
            for event in self._provider_observation_events(connection, completed_provider_ids):
                if event.producer_invocation_id is None:
                    raise ValueError("provider observation event is missing producer invocation")
                if event.event_type == "vacancy_detail_observation_stored":
                    detail_events[event.producer_invocation_id] = event
                elif event.event_type == "company_profile_observation_stored":
                    profile_events[event.producer_invocation_id] = event
                elif event.event_type == "company_site_observation_stored":
                    site_events[event.producer_invocation_id] = event
            detail_consumers = self._detail_consumer_rows(connection, tuple(detail_events))
            profile_consumers = self._profile_consumer_rows(connection, tuple(profile_events))
            site_consumers = self._site_consumer_rows(connection, tuple(site_events))
            for row in detail_consumers:
                event = detail_events[str(row["detail_invocation_id"])]
                self._materialize_detail_consumer(
                    connection,
                    row=row,
                    event=event,
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    selection_evaluator=selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            for row in profile_consumers:
                self._materialize_company_consumer(
                    connection,
                    row=row,
                    event=profile_events[str(row["provider_invocation_id"])],
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    provider_payload_column="profile_payload_json",
                    provider_observation_column="profile_observation_id",
                    evidence_key="profileObservationId",
                    expected_event_type="company_profile_observation_stored",
                    selection_evaluator=selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            for row in site_consumers:
                self._materialize_company_consumer(
                    connection,
                    row=row,
                    event=site_events[str(row["provider_invocation_id"])],
                    requirements=requirements.get(str(row["source_plan_id"]), ()),
                    provider_payload_column="site_payload_json",
                    provider_observation_column="site_observation_id",
                    evidence_key="siteObservationId",
                    expected_event_type="company_site_observation_stored",
                    selection_evaluator=selection_evaluator,
                    derivation_evaluator=derivation_evaluator,
                    now=now,
                )
            self._route_discovered_endpoints(
                connection,
                execution_id=coordinator.execution_id,
                profile_events=profile_events,
                site_events=site_events,
                manifest_resolver=manifest_resolver,
                target_resolver=target_resolver,
                discovered_planner=discovered_planner,
                now=now,
            )
            self._settle_terminal_dependencies(
                connection,
                execution_id=coordinator.execution_id,
                terminal_invocation_ids=tuple({*terminal_events, *failed_provider_ids}),
                now=now,
            )
            self._mark_event_ids_processed(connection, coordinator.execution_id, event_ids, now)

    def assemble_final(
        self,
        execution_id: str,
        *,
        projector: Callable[[JsonObject], JsonObject],
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
                SELECT
                    listing.listing_id,
                    listing.vacancy_id,
                    listing.company_id,
                    listing.source_id,
                    evaluation.evaluation_id,
                    fact_set.materialized_facts_json,
                    fact_set.created_at
                FROM selection_evaluations AS evaluation
                JOIN fact_sets AS fact_set ON fact_set.fact_set_id = evaluation.fact_set_id
                JOIN vacancy_listings AS listing ON listing.listing_id = evaluation.listing_id
                WHERE evaluation.execution_id = ?
                  AND evaluation.stage = 'final'
                  AND evaluation.outcome = 'keep'
                  AND fact_set.created_at = (
                      SELECT MAX(newest.created_at)
                      FROM fact_sets AS newest
                      WHERE newest.execution_id = evaluation.execution_id
                        AND newest.listing_id = evaluation.listing_id
                  )
                ORDER BY listing.vacancy_id, listing.source_id, listing.listing_id
                """,
                (execution_id,),
            ).fetchall()
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(str(row["vacancy_id"]), []).append(row)

            records: list[tuple[sqlite3.Row, list[sqlite3.Row], JsonObject, str | None]] = []
            for vacancy_id, members in grouped.items():
                representative = members[0]
                duplicate_group_id = self._store_exact_duplicate_group(
                    connection,
                    execution_id=execution_id,
                    vacancy_id=vacancy_id,
                    members=members,
                    now=now,
                )
                payload = projector(_json_object(representative["materialized_facts_json"]))
                payload["sourceVariants"] = tuple(str(member["source_id"]) for member in members)
                if duplicate_group_id is not None:
                    payload["duplicateConfidence"] = "exact"
                records.append((representative, members, payload, duplicate_group_id))

            probable_groups = self._store_probable_duplicate_groups(
                connection,
                execution_id=execution_id,
                records=records,
                now=now,
            )
            items: list[JsonObject] = []
            for representative, _members, payload, exact_group_id in records:
                listing_id = str(representative["listing_id"])
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
                    ) VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                    """,
                    (
                        final_vacancy_id,
                        execution_id,
                        representative["listing_id"],
                        representative["evaluation_id"],
                        duplicate_group_id,
                        _json_dumps(payload),
                    ),
                )
                items.append(payload)
            connection.execute(
                """
                UPDATE search_executions
                SET status = 'completed',
                    completion_reason = COALESCE(completion_reason, 'drained')
                WHERE execution_id = ?
                """,
                (execution_id,),
            )
        return tuple(items)

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
                      status IN ('queued', 'retry_wait')
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
                reserved_collection_units,
                status,
                available_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
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
        now: float,
    ) -> None:
        cls._finish_parser_attempt(
            connection,
            invocation_id=invocation.invocation_id,
            attempt_id=attempt_id,
            outcome="success",
            failure_kind=None,
            retryable=False,
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
        retryable: bool,
        now: float,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE parser_attempts
            SET finished_at = ?, outcome = ?, failure_kind = ?, retryable = ?
            WHERE parser_attempt_id = ? AND invocation_id = ? AND finished_at IS NULL
            """,
            (now, outcome, failure_kind, int(retryable), attempt_id, invocation_id),
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
                   execution.deadline_at AS execution_deadline_at
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
            or float(row["execution_deadline_at"]) <= now
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
                provider.provider_id,
                provider.provider_stage,
                provider.parser_id,
                provider.parser_version,
                provider.fact_path,
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
    def _detail_consumer_rows(
        connection: sqlite3.Connection,
        detail_invocation_ids: tuple[str, ...],
    ) -> tuple[sqlite3.Row, ...]:
        if not detail_invocation_ids:
            return ()
        placeholders = ",".join("?" for _ in detail_invocation_ids)
        rows = connection.execute(
            f"""
            SELECT
                enrichment.execution_id,
                enrichment.listing_id,
                enrichment.invocation_id AS detail_invocation_id,
                enrichment.enrichment_request_id,
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
             AND listing_observation.execution_id = enrichment.execution_id
            WHERE enrichment.invocation_id IN ({placeholders})
              AND enrichment.status = 'waiting'
              AND listing_observation.listing_observation_id = (
                  SELECT newest.listing_observation_id
                  FROM listing_observations AS newest
                  WHERE newest.listing_id = enrichment.listing_id
                    AND newest.execution_id = enrichment.execution_id
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
                SELECT
                    enrichment.execution_id,
                    enrichment.listing_id,
                    enrichment.invocation_id AS provider_invocation_id,
                    enrichment.enrichment_request_id,
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
                 AND listing_observation.execution_id = enrichment.execution_id
                WHERE enrichment.invocation_id IN ({placeholders})
                  AND enrichment.status = 'waiting'
                  AND listing_observation.listing_observation_id = (
                      SELECT newest.listing_observation_id
                      FROM listing_observations AS newest
                      WHERE newest.listing_id = enrichment.listing_id
                        AND newest.execution_id = enrichment.execution_id
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
                SELECT
                    enrichment.execution_id,
                    enrichment.listing_id,
                    enrichment.invocation_id AS provider_invocation_id,
                    enrichment.enrichment_request_id,
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
                 AND listing_observation.execution_id = enrichment.execution_id
                WHERE enrichment.invocation_id IN ({placeholders})
                  AND enrichment.status = 'waiting'
                  AND listing_observation.listing_observation_id = (
                      SELECT newest.listing_observation_id
                      FROM listing_observations AS newest
                      WHERE newest.listing_id = enrichment.listing_id
                        AND newest.execution_id = enrichment.execution_id
                      ORDER BY newest.observed_at DESC, newest.listing_observation_id DESC
                      LIMIT 1
                  )
                ORDER BY enrichment.listing_id
                """,
                invocation_ids,
            ).fetchall()
        )

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
        execution_id: str,
        profile_events: dict[str, StoredDomainEvent],
        site_events: dict[str, StoredDomainEvent],
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        target_resolver: Callable[[ParserType, str], TargetResolution],
        discovered_planner: Callable[
            [ParserRef, JsonObject], tuple[SearchListingInput, ...]
        ],
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
            SELECT discovery_plan_budget, discovery_plans_created
            FROM search_executions WHERE execution_id = ?
            """,
            (execution_id,),
        ).fetchone()
        if execution is None:
            raise KeyError(f"unknown execution: {execution_id}")
        remaining_discovery_plans = max(
            int(execution["discovery_plan_budget"])
            - int(execution["discovery_plans_created"]),
            0,
        )
        for endpoint in endpoints:
            endpoint_id = str(endpoint["endpoint_id"])
            url = str(endpoint["normalized_url"])
            resolution = target_resolver(ParserType.SEARCH_LISTING, url)
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
            source_plan_id = _stable_id("source-plan", execution_id, endpoint_id, parser_ref.parser_id)
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
                    execution_id,
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
                    (execution_id,),
                )
                remaining_discovery_plans -= 1
            for parser_input in initial_inputs:
                fingerprint = _fingerprint(parser_input)
                self._enqueue_invocation(
                    connection,
                    ParserInvocationSpec(
                        execution_id=execution_id,
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
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        selection_evaluator: Callable[[JsonObject], SelectionDecision],
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
        now: float,
    ) -> None:
        facts = _json_object(row["payload_json"])
        evidence_refs = {"listingObservationId": str(row["listing_observation_id"])}
        facts, evidence_refs = self._apply_fact_derivations(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            facts=facts,
            evidence_refs=evidence_refs,
            derivation_evaluator=derivation_evaluator,
        )
        fingerprint = _fingerprint({"facts": facts, "evidence": evidence_refs})
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
        selection = selection_evaluator(facts)
        if not selection.keep:
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
        outcome = "enrich" if missing else "keep"
        stage = "preliminary" if missing else "final"
        reason_codes = tuple(
            f"missing:{requirement['required_fact_path']}" for requirement in missing
        )
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
        scheduled_provider_ids: set[str] = set()
        for requirement in missing:
            provider_id = str(requirement["provider_id"])
            if provider_id in scheduled_provider_ids:
                continue
            self._schedule_listing_provider(
                connection,
                row=row,
                event=event,
                facts=facts,
                provider=requirement,
                manifest_resolver=manifest_resolver,
                now=now,
            )
            scheduled_provider_ids.add(provider_id)
        self._finalize_if_all_dependencies_terminal(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            fact_set_id=fact_set_id,
        )

    def _schedule_listing_provider(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event: StoredDomainEvent,
        facts: JsonObject,
        provider: sqlite3.Row,
        manifest_resolver: Callable[[ParserRef], ParserManifest],
        now: float,
    ) -> None:
        stage = ProviderStage(str(provider["provider_stage"]))
        if stage not in {
            ProviderStage.DETAIL_OUTPUT,
            ProviderStage.PROFILE_OUTPUT,
            ProviderStage.SITE_OUTPUT,
        }:
            self._insert_enrichment_request(
                connection,
                row=row,
                provider=provider,
                invocation_id=None,
                status="terminal",
                resolution_outcome="provider_unavailable",
                terminal_reason=f"unsupported_provider_stage:{stage.value}",
            )
            return
        parser_id = provider["parser_id"]
        parser_version = provider["parser_version"]
        if not isinstance(parser_id, str) or not isinstance(parser_version, str):
            raise ValueError("network fact provider is missing parser reference")
        parser_ref = ParserRef(parser_id, parser_version)
        manifest = manifest_resolver(parser_ref)
        expected_type = {
            ProviderStage.DETAIL_OUTPUT: ParserType.VACANCY_DETAIL,
            ProviderStage.PROFILE_OUTPUT: ParserType.COMPANY_PROFILE,
            ProviderStage.SITE_OUTPUT: ParserType.COMPANY_SITE,
        }[stage]
        if manifest.parser_type != expected_type:
            raise ValueError("fact provider parser type does not match provider stage")

        parser_input: VacancyDetailInput | CompanyProfileInput | CompanySiteInput
        task_class: TaskClass
        if stage == ProviderStage.DETAIL_OUTPUT:
            target_provider_id = _required_json_text(facts, "target_provider_id")
            vacancy_url = _required_json_text(facts, "vacancy_url")
            source_listing_id = _optional_json_text(facts, "source_listing_id")
            target_identity = source_listing_id or _normalize_url(vacancy_url)
            task_key = f"vacancy_detail:{parser_id}:{target_provider_id}:{target_identity}"
            parser_input = VacancyDetailInput(
                target_provider_id=target_provider_id,
                vacancy_url=vacancy_url,
                source_listing_id=source_listing_id,
            )
            task_class = TaskClass.DETAIL
        elif stage == ProviderStage.PROFILE_OUTPUT:
            company = _optional_object(facts, "company")
            profile_url = None if company is None else _optional_json_text(company, "profile_url")
            if profile_url is None:
                self._insert_unresolved_enrichment(connection, row=row, provider=provider)
                return
            if company is None:
                raise RuntimeError("profile URL cannot exist without company facts")
            target_provider_id = (
                _optional_json_text(company, "target_provider_id")
                or _required_json_text(facts, "target_provider_id")
            )
            source_company_id = _optional_json_text(company, "source_company_id")
            task_key = f"company_profile:{parser_id}:{target_provider_id}:{_normalize_url(profile_url)}"
            parser_input = CompanyProfileInput(
                target_provider_id=target_provider_id,
                company_profile_url=profile_url,
                source_company_id=source_company_id,
            )
            task_class = TaskClass.PROFILE
        else:
            company = _optional_object(facts, "company")
            site_url = None if company is None else _optional_json_text(company, "official_site_url")
            if site_url is None:
                self._insert_unresolved_enrichment(connection, row=row, provider=provider)
                return
            task_key = f"company_site:{parser_id}:{_normalize_url(site_url)}"
            parser_input = CompanySiteInput(site_url=site_url)
            task_class = TaskClass.SITE

        invocation_id = self._enqueue_invocation(
            connection,
            ParserInvocationSpec(
                execution_id=str(row["execution_id"]),
                source_plan_id=None,
                parent_invocation_id=str(row["invocation_id"]),
                cause_event_id=event.event_id,
                parser_ref=parser_ref,
                parser_type=expected_type,
                input_schema_id=manifest.input_schema_id,
                parser_input=parser_input,
                task_class=task_class,
                task_key=task_key,
                available_at=now,
                reserved_collection_units=None,
            ),
        )
        self._insert_enrichment_request(
            connection,
            row=row,
            provider=provider,
            invocation_id=invocation_id,
            status="waiting",
            resolution_outcome="resolved",
            terminal_reason=None,
        )

    @staticmethod
    def _insert_unresolved_enrichment(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        provider: sqlite3.Row,
    ) -> None:
        SqliteGraphRepository._insert_enrichment_request(
            connection,
            row=row,
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
        row: sqlite3.Row,
        provider: sqlite3.Row,
        invocation_id: str | None,
        status: str,
        resolution_outcome: str,
        terminal_reason: str | None,
    ) -> None:
        enrichment_request_id = _stable_id(
            "enrichment-request",
            str(row["execution_id"]),
            str(row["listing_id"]),
            str(provider["provider_id"]),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO listing_enrichment_requests (
                enrichment_request_id, execution_id, listing_id, invocation_id,
                provider_id, required, status, resolution_outcome, terminal_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                enrichment_request_id,
                row["execution_id"],
                row["listing_id"],
                invocation_id,
                provider["provider_id"],
                provider["required_for_final"],
                status,
                resolution_outcome,
                terminal_reason,
            ),
        )

    @staticmethod
    def _finalize_if_all_dependencies_terminal(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        listing_id: str,
        fact_set_id: str,
    ) -> None:
        dependencies = connection.execute(
            """
            SELECT status, required, resolution_outcome
            FROM listing_enrichment_requests
            WHERE execution_id = ? AND listing_id = ?
            """,
            (execution_id, listing_id),
        ).fetchall()
        if not dependencies or any(row["status"] == "waiting" for row in dependencies):
            return
        required_failed = any(bool(row["required"]) for row in dependencies)
        outcome = "reject" if required_failed else "keep"
        reasons = tuple(sorted(str(row["resolution_outcome"]) for row in dependencies))
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
        input_evidence_refs = dict(evidence_refs)
        input_fingerprint = _fingerprint(
            {"facts": facts, "evidence": input_evidence_refs}
        )
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

    @staticmethod
    def _materialize_detail_consumer(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event: StoredDomainEvent,
        requirements: tuple[sqlite3.Row, ...],
        selection_evaluator: Callable[[JsonObject], SelectionDecision],
        derivation_evaluator: Callable[[JsonObject], tuple[FactDerivation, ...]],
        now: float,
    ) -> None:
        listing_facts = _json_object(row["listing_payload_json"])
        detail_facts = _json_object(row["detail_payload_json"])
        facts = _merge_fact_payloads(listing_facts, detail_facts)
        evidence_refs = {
            "listingObservationId": str(row["listing_observation_id"]),
            "detailObservationId": str(row["detail_observation_id"]),
        }
        facts, evidence_refs = SqliteGraphRepository._apply_fact_derivations(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            facts=facts,
            evidence_refs=evidence_refs,
            derivation_evaluator=derivation_evaluator,
        )
        fingerprint = _fingerprint({"facts": facts, "evidence": evidence_refs})
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
        selection = selection_evaluator(facts)
        if not selection.keep:
            SqliteGraphRepository._insert_evaluation(
                connection,
                execution_id=str(row["execution_id"]),
                listing_id=str(row["listing_id"]),
                fact_set_id=fact_set_id,
                stage="final",
                outcome="reject",
                reasons=selection.reasons,
            )
            connection.execute(
                "UPDATE listing_enrichment_requests SET status = 'satisfied', resolution_outcome = 'satisfied' "
                "WHERE enrichment_request_id = ?",
                (row["enrichment_request_id"],),
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
        outcome = "keep" if not missing else "enrich"
        stage = "final" if not missing else "preliminary"
        reason_codes = tuple(
            f"missing:{requirement['required_fact_path']}" for requirement in missing
        )
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
        connection.execute(
            """
            UPDATE listing_enrichment_requests
            SET status = 'satisfied', resolution_outcome = 'satisfied', terminal_reason = NULL
            WHERE enrichment_request_id = ?
            """,
            (row["enrichment_request_id"],),
        )
        if event.event_type != "vacancy_detail_observation_stored":
            raise ValueError("detail consumer requires a detail observation event")

    @staticmethod
    def _materialize_company_consumer(
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        event: StoredDomainEvent,
        requirements: tuple[sqlite3.Row, ...],
        provider_payload_column: str,
        provider_observation_column: str,
        evidence_key: str,
        expected_event_type: str,
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
        facts, evidence_refs = SqliteGraphRepository._apply_fact_derivations(
            connection,
            execution_id=str(row["execution_id"]),
            listing_id=str(row["listing_id"]),
            facts=facts,
            evidence_refs=evidence_refs,
            derivation_evaluator=derivation_evaluator,
        )
        fingerprint = _fingerprint({"facts": facts, "evidence": evidence_refs})
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
        selection = selection_evaluator(facts)
        if not selection.keep:
            SqliteGraphRepository._insert_evaluation(
                connection,
                execution_id=str(row["execution_id"]),
                listing_id=str(row["listing_id"]),
                fact_set_id=fact_set_id,
                stage="final",
                outcome="reject",
                reasons=selection.reasons,
            )
            connection.execute(
                "UPDATE listing_enrichment_requests SET status = 'satisfied', resolution_outcome = 'satisfied' "
                "WHERE enrichment_request_id = ?",
                (row["enrichment_request_id"],),
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
        outcome = "keep" if not missing else "enrich"
        stage = "final" if not missing else "preliminary"
        reasons = tuple(f"missing:{row['required_fact_path']}" for row in missing)
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
        connection.execute(
            """
            UPDATE listing_enrichment_requests
            SET status = 'satisfied', resolution_outcome = 'satisfied', terminal_reason = NULL
            WHERE enrichment_request_id = ?
            """,
            (row["enrichment_request_id"],),
        )

    @staticmethod
    def _settle_terminal_dependencies(
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        terminal_invocation_ids: tuple[str, ...],
        now: float,
    ) -> None:
        if not terminal_invocation_ids:
            return
        placeholders = ",".join("?" for _ in terminal_invocation_ids)
        affected_rows = connection.execute(
            f"""
            SELECT DISTINCT listing_id
            FROM listing_enrichment_requests
            WHERE execution_id = ? AND invocation_id IN ({placeholders})
            """,
            (execution_id, *terminal_invocation_ids),
        ).fetchall()
        connection.execute(
            f"""
            UPDATE listing_enrichment_requests
            SET status = 'terminal', resolution_outcome = 'provider_terminal', terminal_reason = 'invocation_terminal'
            WHERE execution_id = ? AND invocation_id IN ({placeholders}) AND status = 'waiting'
            """,
            (execution_id, *terminal_invocation_ids),
        )
        for affected in affected_rows:
            listing_id = str(affected["listing_id"])
            dependency_rows = connection.execute(
                """
                SELECT status, required, invocation_id
                FROM listing_enrichment_requests
                WHERE execution_id = ? AND listing_id = ?
                """,
                (execution_id, listing_id),
            ).fetchall()
            if any(row["status"] == "waiting" for row in dependency_rows):
                continue
            latest = connection.execute(
                """
                SELECT * FROM fact_sets
                WHERE execution_id = ? AND listing_id = ?
                ORDER BY created_at DESC, fact_set_id DESC
                LIMIT 1
                """,
                (execution_id, listing_id),
            ).fetchone()
            if latest is None:
                continue
            evidence_refs = _json_object(latest["evidence_refs_json"])
            terminal_ids = tuple(
                sorted(
                    str(row["invocation_id"])
                    for row in dependency_rows
                    if row["status"] == "terminal" and row["invocation_id"] is not None
                )
            )
            evidence_refs["terminalDependencyInvocationIds"] = terminal_ids
            facts = _json_object(latest["materialized_facts_json"])
            fingerprint = _fingerprint({"facts": facts, "evidence": evidence_refs})
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
            outcome = "reject" if required_failed else "keep"
            reasons = ("required_provider_terminal",) if required_failed else ("optional_provider_terminal",)
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
    def _mark_event_ids_processed(
        connection: sqlite3.Connection,
        execution_id: str,
        event_ids: tuple[str, ...],
        now: float,
    ) -> None:
        if not event_ids:
            return
        placeholders = ",".join("?" for _ in event_ids)
        connection.execute(
            f"""
            UPDATE domain_events SET processed_at = ?
            WHERE execution_id = ? AND event_id IN ({placeholders}) AND processed_at IS NULL
            """,
            (now, execution_id, *event_ids),
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
        if int(source_plan["items_used"]) + len(result.items) > int(source_plan["item_budget"]):
            raise ValueError("source plan item budget exceeded")
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
        resources: list[tuple[object, ...]] = []
        aliases: list[tuple[object, ...]] = []
        listings: list[tuple[object, ...]] = []
        observations: list[tuple[object, ...]] = []
        company_claims: list[tuple[object, ...]] = []
        observation_ids: list[str] = []
        source_plan_id = invocation.spec.source_plan_id
        if source_plan_id is None:
            raise ValueError("listing observation requires source plan")
        for item, company_id, (_, claims) in zip(items, company_ids, company_groups, strict=True):
            canonical_url = _normalize_url(item.vacancy_url)
            identity_key = (
                f"provider:{item.target_provider_id}:{item.source_listing_id}"
                if item.source_listing_id
                else f"url:{canonical_url}"
            )
            vacancy_id = _stable_id("vacancy", run_id, identity_key)
            listing_identity = item.source_listing_id or canonical_url
            listing_id = _stable_id("listing", run_id, item.source_id, listing_identity)
            item_key = item.source_listing_id or canonical_url
            observation_id = _stable_id("listing-observation", invocation.invocation_id, item_key)
            resources.append(
                (
                    vacancy_id,
                    run_id,
                    item.target_provider_id,
                    item.source_listing_id,
                    canonical_url,
                    identity_key,
                    1,
                    now,
                )
            )
            aliases.append(
                (
                    _stable_id("vacancy-url-alias", vacancy_id, canonical_url),
                    vacancy_id,
                    canonical_url,
                    1,
                )
            )
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
            INSERT OR IGNORE INTO vacancy_resources (
                vacancy_id, run_id, target_provider_id, source_listing_id, canonical_url,
                identity_key, identity_schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            resources,
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO vacancy_url_aliases (
                vacancy_url_alias_id, vacancy_id, normalized_url, normalizer_version
            ) VALUES (?, ?, ?, ?)
            """,
            aliases,
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
    ) -> int:
        existing_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM parser_invocations WHERE source_plan_id = ?",
                (source_plan["source_plan_id"],),
            ).fetchone()[0]
        )
        if existing_count + len(continuations) > int(source_plan["invocation_budget"]):
            raise ValueError("source plan invocation budget exceeded")
        inserted = 0
        for continuation in continuations:
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
                ),
            )
            if connection.total_changes > before:
                inserted += 1
        return inserted

    @staticmethod
    def _update_source_plan_after_search_result(
        connection: sqlite3.Connection,
        *,
        source_plan: sqlite3.Row,
        result: SearchListingResult,
        continuation_count: int,
    ) -> None:
        units_used = int(source_plan["units_used"]) + result.collection_units_consumed
        items_used = int(source_plan["items_used"]) + len(result.items)
        invocations_used = int(source_plan["invocations_used"]) + 1
        has_pending_invocations = connection.execute(
            """
            SELECT 1 FROM parser_invocations
            WHERE source_plan_id = ? AND status IN ('queued', 'leased', 'retry_wait')
            LIMIT 1
            """,
            (source_plan["source_plan_id"],),
        ).fetchone() is not None
        if continuation_count or has_pending_invocations:
            status = "running"
        elif result.outcome == SearchResultOutcome.PARTIAL_SUCCESS:
            status = "partial"
        elif units_used >= int(source_plan["unit_budget"]) or items_used >= int(source_plan["item_budget"]):
            status = "limit_reached"
        elif items_used:
            status = "succeeded"
        else:
            status = "no_results"
        connection.execute(
            """
            UPDATE source_plans
            SET units_used = ?, items_used = ?, invocations_used = ?, status = ?
            WHERE source_plan_id = ?
            """,
            (units_used, items_used, invocations_used, status, source_plan["source_plan_id"]),
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


def _leased_invocation(
    row: sqlite3.Row,
    owner_id: str,
    token: str,
    lease_until: float,
    *,
    reserved_collection_units: int | None = None,
) -> LeasedParserInvocation:
    parser_type = ParserType(str(row["parser_type"]))
    spec = ParserInvocationSpec(
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


def read_graph_processed_payload(
    database_path: Path,
    *,
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
                WHERE execution.status = 'completed' AND execution.append_sequence = ?
                ORDER BY execution.created_at DESC
                LIMIT 1
                """,
                (append_sequence,),
            ).fetchone()
        if execution is None:
            raise FileNotFoundError("completed graph execution was not found")
        item_rows = connection.execute(
            """
            SELECT payload_json FROM final_vacancies
            WHERE execution_id = ?
            ORDER BY score DESC, final_vacancy_id
            """,
            (execution["execution_id"],),
        ).fetchall()
        observation_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM listing_observations WHERE execution_id = ?",
                (execution["execution_id"],),
            ).fetchone()[0]
        )
    items = tuple(_json_object(row["payload_json"]) for row in item_rows)
    return {
        "schema_version": 2,
        "record_type": "processed_results",
        "phase": "final",
        "run_id": str(execution["run_id"]),
        "execution_id": str(execution["execution_id"]),
        "append_sequence": int(execution["append_sequence"]),
        "search_request": _json_object(execution["intent_json"]),
        "raw_records_read": observation_count,
        "result_count": len(items),
        "results": list(items),
        "filtered_out_results": [],
    }
