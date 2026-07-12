"""Deployment-scoped SQLite resource admission control."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from job_harness.v2.ports import OperationContext

_SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_state (
    resource_key TEXT PRIMARY KEY,
    max_concurrency INTEGER NOT NULL CHECK (max_concurrency >= 1),
    min_interval_seconds REAL NOT NULL CHECK (min_interval_seconds >= 0),
    lease_seconds REAL NOT NULL CHECK (lease_seconds > 0),
    next_start_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_slots (
    resource_key TEXT NOT NULL REFERENCES resource_state(resource_key) ON DELETE CASCADE,
    slot_number INTEGER NOT NULL CHECK (slot_number >= 1),
    operation_id TEXT,
    owner_id TEXT,
    lease_until REAL,
    PRIMARY KEY (resource_key, slot_number)
);

CREATE INDEX IF NOT EXISTS resource_slots_available_idx
    ON resource_slots (resource_key, slot_number, lease_until);
"""


@dataclass(frozen=True)
class ResourcePolicy:
    max_concurrency: int
    min_interval_seconds: float
    lease_seconds: float

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be >= 0")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")


@dataclass(frozen=True)
class ResourceSlotPermit:
    resource_key: str
    slot_number: int
    operation_id: str
    owner_id: str
    lease_until: float


@dataclass(frozen=True)
class AcquireDecision:
    permit: ResourceSlotPermit | None
    retry_after_seconds: float

    def __post_init__(self) -> None:
        if self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be >= 0")


class SqliteResourceGateBackend:
    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)
            connection.commit()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def try_acquire(
        self,
        *,
        resource_key: str,
        policy: ResourcePolicy,
        operation_id: str,
        owner_id: str,
        now: float,
    ) -> AcquireDecision:
        _require_text(resource_key, "resource_key")
        _require_text(operation_id, "operation_id")
        _require_text(owner_id, "owner_id")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_policy(connection, resource_key, policy, now)
                next_start_at = self._next_start_at(connection, resource_key)
                if next_start_at > now:
                    connection.commit()
                    return AcquireDecision(None, next_start_at - now)

                slot = connection.execute(
                    """
                    SELECT slot_number, lease_until
                    FROM resource_slots
                    WHERE resource_key = ?
                      AND slot_number <= ?
                      AND (operation_id IS NULL OR lease_until <= ?)
                    ORDER BY slot_number
                    LIMIT 1
                    """,
                    (resource_key, policy.max_concurrency, now),
                ).fetchone()
                if slot is None:
                    retry_after = self._slot_retry_after(connection, resource_key, policy, now)
                    connection.commit()
                    return AcquireDecision(None, retry_after)

                slot_number = int(slot["slot_number"])
                lease_until = now + policy.lease_seconds
                connection.execute(
                    """
                    UPDATE resource_slots
                    SET operation_id = ?, owner_id = ?, lease_until = ?
                    WHERE resource_key = ? AND slot_number = ?
                    """,
                    (operation_id, owner_id, lease_until, resource_key, slot_number),
                )
                connection.execute(
                    """
                    UPDATE resource_state
                    SET next_start_at = ?, updated_at = ?
                    WHERE resource_key = ?
                    """,
                    (now + policy.min_interval_seconds, now, resource_key),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return AcquireDecision(
            ResourceSlotPermit(
                resource_key=resource_key,
                slot_number=slot_number,
                operation_id=operation_id,
                owner_id=owner_id,
                lease_until=lease_until,
            ),
            0.0,
        )

    def release(self, permit: ResourceSlotPermit) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE resource_slots
                SET operation_id = NULL, owner_id = NULL, lease_until = NULL
                WHERE resource_key = ?
                  AND slot_number = ?
                  AND operation_id = ?
                  AND owner_id = ?
                """,
                (
                    permit.resource_key,
                    permit.slot_number,
                    permit.operation_id,
                    permit.owner_id,
                ),
            )
            connection.commit()

    def _ensure_policy(
        self,
        connection: sqlite3.Connection,
        resource_key: str,
        policy: ResourcePolicy,
        now: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO resource_state (
                resource_key,
                max_concurrency,
                min_interval_seconds,
                lease_seconds,
                next_start_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(resource_key) DO UPDATE SET
                max_concurrency = excluded.max_concurrency,
                min_interval_seconds = excluded.min_interval_seconds,
                lease_seconds = excluded.lease_seconds,
                updated_at = excluded.updated_at
            WHERE resource_state.max_concurrency != excluded.max_concurrency
               OR resource_state.min_interval_seconds != excluded.min_interval_seconds
               OR resource_state.lease_seconds != excluded.lease_seconds
            """,
            (
                resource_key,
                policy.max_concurrency,
                policy.min_interval_seconds,
                policy.lease_seconds,
                now,
            ),
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO resource_slots (resource_key, slot_number)
            VALUES (?, ?)
            """,
            ((resource_key, slot_number) for slot_number in range(1, policy.max_concurrency + 1)),
        )

    @staticmethod
    def _next_start_at(connection: sqlite3.Connection, resource_key: str) -> float:
        row = connection.execute(
            "SELECT next_start_at FROM resource_state WHERE resource_key = ?",
            (resource_key,),
        ).fetchone()
        if row is None:
            raise RuntimeError("resource policy row was not created")
        return float(row["next_start_at"])

    @staticmethod
    def _slot_retry_after(
        connection: sqlite3.Connection,
        resource_key: str,
        policy: ResourcePolicy,
        now: float,
    ) -> float:
        row = connection.execute(
            """
            SELECT MIN(lease_until) AS earliest_lease
            FROM resource_slots
            WHERE resource_key = ?
              AND slot_number <= ?
              AND operation_id IS NOT NULL
            """,
            (resource_key, policy.max_concurrency),
        ).fetchone()
        earliest_lease = None if row is None else row["earliest_lease"]
        if earliest_lease is None:
            return 0.01
        return max(float(earliest_lease) - now, 0.01)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection


class ResourceGate:
    def __init__(self, *, backend: SqliteResourceGateBackend, owner_id: str) -> None:
        _require_text(owner_id, "owner_id")
        self._backend = backend
        self._owner_id = owner_id

    async def admit(
        self,
        resource_key: str,
        policy: ResourcePolicy,
        context: OperationContext,
    ) -> ResourceSlotPermit:
        while True:
            decision = self._backend.try_acquire(
                resource_key=resource_key,
                policy=policy,
                operation_id=context.operation_id,
                owner_id=self._owner_id,
                now=time.time(),
            )
            if decision.permit is not None:
                return decision.permit
            await asyncio.sleep(decision.retry_after_seconds)

    def release(self, permit: ResourceSlotPermit) -> None:
        self._backend.release(permit)


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
