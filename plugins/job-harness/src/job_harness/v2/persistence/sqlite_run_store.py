"""SQLite-backed persistence for v2 search runs."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from types import TracebackType

from job_harness.v2.contracts import DescriptionAvailability, RawListing, RawSearchRecord, SourceAttemptRecord
from job_harness.v2.ports import StoredRawRecord
from job_harness.v2.serialization import JsonObject, to_jsonable

_VALID_APPEND_STATUSES = frozenset({"in_progress", "completed", "failed"})
_SCHEMA_RESOURCE = "schema.sql"


class SqliteRunStore:
    """Single-file durable store for one v2 run."""

    def __init__(self, database_path: Path, *, run_id: str) -> None:
        clean_run_id = run_id.strip()
        if not clean_run_id:
            raise ValueError("run_id must be non-empty")
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = clean_run_id
        self._lock = threading.Lock()
        self._closed = False
        self._append_sequence: int | None = None
        self._connection = sqlite3.connect(
            str(self._database_path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def append_sequence(self) -> int:
        if self._append_sequence is None:
            raise RuntimeError("append attempt has not been reserved")
        return self._append_sequence

    def reserve_append_attempt(self, request: Mapping[str, object]) -> int:
        payload_json = _json_dumps(dict(request))
        now = _now()
        with self._lock:
            self._ensure_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO runs (run_id, created_at, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET updated_at = excluded.updated_at
                    """,
                    (self._run_id, now, now),
                )
                row = self._connection.execute(
                    """
                    SELECT COALESCE(MAX(append_sequence), -1) + 1 AS append_sequence
                    FROM append_attempts
                    WHERE run_id = ?
                    """,
                    (self._run_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("failed to allocate append_sequence")
                append_sequence = int(row["append_sequence"])
                self._connection.execute(
                    """
                    INSERT INTO append_attempts (
                        run_id,
                        append_sequence,
                        request_json,
                        status,
                        started_at
                    )
                    VALUES (?, ?, ?, 'in_progress', ?)
                    """,
                    (self._run_id, append_sequence, payload_json, now),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            self._append_sequence = append_sequence
            return append_sequence

    def mark_append_attempt_completed(self) -> None:
        self._mark_append_attempt("completed")

    def mark_append_attempt_failed(self) -> None:
        self._mark_append_attempt("failed")

    def append_raw_record(self, record: RawSearchRecord) -> None:
        if record.run_id != self._run_id:
            raise ValueError("raw record run_id must match run store")
        self._require_reserved_append(record.append_sequence)
        payload = to_jsonable(record)
        listing = payload.get("listing")
        if not isinstance(listing, dict):
            raise ValueError("raw record payload is missing listing object")
        with self._lock:
            self._ensure_open()
            self._connection.execute(
                """
                INSERT INTO raw_listings (
                    run_id,
                    append_sequence,
                    query_variant,
                    source,
                    source_type,
                    collected_at,
                    description_availability,
                    detail_fetched,
                    detail_parse_error,
                    source_url,
                    listing_json,
                    record_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.append_sequence,
                    record.query_variant,
                    record.source,
                    record.source_type.value,
                    payload["collected_at"],
                    record.description_availability.value,
                    int(record.detail_fetched),
                    record.detail_parse_error,
                    record.source_url,
                    _json_dumps(listing),
                    _json_dumps(payload),
                ),
            )
            self._touch_run()

    def append_attempt_record(self, record: SourceAttemptRecord) -> None:
        append_sequence = self.append_sequence
        payload = to_jsonable(record)
        payload["record_type"] = "source_attempt"
        payload["append_sequence"] = append_sequence
        with self._lock:
            self._ensure_open()
            self._connection.execute(
                """
                INSERT INTO source_attempts (
                    run_id,
                    append_sequence,
                    source,
                    query_variant,
                    attempt,
                    outcome,
                    raw_listings_written,
                    pages_visited,
                    elapsed_ms,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._run_id,
                    append_sequence,
                    record.source,
                    record.query_variant,
                    record.attempt,
                    record.outcome.value,
                    record.counts.raw_listings_written,
                    record.counts.pages_visited,
                    record.elapsed_ms,
                    _json_dumps(payload),
                ),
            )
            self._touch_run()

    def replace_run_manifest(self, manifest: Mapping[str, object]) -> None:
        payload = to_jsonable(dict(manifest))
        run_id = payload.get("run_id")
        if run_id != self._run_id:
            raise ValueError("run manifest run_id must match run store")
        now = _now()
        with self._lock:
            self._ensure_open()
            self._connection.execute(
                """
                INSERT INTO run_manifest (run_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (self._run_id, _json_dumps(payload), now),
            )
            self._touch_run(now)

    def write_processed_results(self, payload: Mapping[str, object]) -> None:
        jsonable = to_jsonable(dict(payload))
        if jsonable.get("record_type") != "processed_results":
            raise ValueError("expected processed_results payload")
        if jsonable.get("run_id") != self._run_id:
            raise ValueError("processed results run_id must match run store")
        append_sequence = jsonable.get("append_sequence")
        if not isinstance(append_sequence, int) or append_sequence < 0:
            raise ValueError("processed results append_sequence must be >= 0")
        phase = jsonable.get("phase")
        if not isinstance(phase, str) or not phase:
            raise ValueError("processed results phase must be non-empty")
        with self._lock:
            self._ensure_open()
            self._connection.execute(
                """
                INSERT INTO processed_results (
                    run_id,
                    append_sequence,
                    phase,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, append_sequence, phase) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (self._run_id, append_sequence, phase, _json_dumps(jsonable), _now()),
            )
            self._touch_run()

    def read_raw_records(self) -> tuple[JsonObject, ...]:
        return self._read_payloads("raw_listings", "record_json")

    def read_raw_record_rows(self) -> tuple[StoredRawRecord, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT id, record_json
                FROM raw_listings
                WHERE run_id = ?
                ORDER BY append_sequence, id
                """,
                (self._run_id,),
            ).fetchall()
        return tuple(
            StoredRawRecord(
                raw_record_id=int(row["id"]),
                payload=_json_object(row["record_json"], "raw_listings.record_json"),
            )
            for row in rows
        )

    def read_source_attempts(self) -> tuple[JsonObject, ...]:
        return self._read_payloads("source_attempts", "payload_json")

    def read_run_manifest(self) -> JsonObject:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                """
                SELECT payload_json
                FROM run_manifest
                WHERE run_id = ?
                """,
                (self._run_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"run manifest has not been written: {self._database_path}")
        return _json_object(row["payload_json"], "run_manifest.payload_json")

    def read_processed_results(
        self,
        *,
        append_sequence: int | None = None,
        phase: str = "final",
    ) -> JsonObject:
        return read_processed_results_payload(
            self._database_path,
            append_sequence=append_sequence,
            phase=phase,
        )

    def update_raw_record_detail(
        self,
        *,
        raw_record_id: int,
        listing: RawListing,
        description_availability: DescriptionAvailability,
        detail_fetched: bool,
        detail_parse_error: str | None,
    ) -> None:
        if raw_record_id < 1:
            raise ValueError("raw_record_id must be >= 1")
        listing_json = to_jsonable(listing)
        if not isinstance(listing_json, dict):
            raise ValueError("listing payload must be a JSON object")
        with self._lock:
            self._ensure_open()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT record_json
                    FROM raw_listings
                    WHERE id = ? AND run_id = ?
                    """,
                    (raw_record_id, self._run_id),
                ).fetchone()
                if row is None:
                    raise KeyError(f"raw listing row does not exist: {raw_record_id}")
                record_json = _json_object(row["record_json"], "raw_listings.record_json")
                record_json["listing"] = listing_json
                record_json["description_availability"] = description_availability.value
                record_json["detail_fetched"] = detail_fetched
                record_json["detail_parse_error"] = detail_parse_error
                cursor = self._connection.execute(
                    """
                    UPDATE raw_listings
                    SET
                        description_availability = ?,
                        detail_fetched = ?,
                        detail_parse_error = ?,
                        listing_json = ?,
                        record_json = ?
                    WHERE id = ? AND run_id = ?
                    """,
                    (
                        description_availability.value,
                        int(detail_fetched),
                        detail_parse_error,
                        _json_dumps(listing_json),
                        _json_dumps(record_json),
                        raw_record_id,
                        self._run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("raw listing detail row was not updated")
                self._touch_run()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> SqliteRunStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(_schema_sql())

    def _mark_append_attempt(self, status: str) -> None:
        if status not in _VALID_APPEND_STATUSES:
            raise ValueError(f"invalid append attempt status: {status}")
        append_sequence = self.append_sequence
        with self._lock:
            self._ensure_open()
            cursor = self._connection.execute(
                """
                UPDATE append_attempts
                SET status = ?, finished_at = ?
                WHERE run_id = ? AND append_sequence = ?
                """,
                (status, _now(), self._run_id, append_sequence),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("append attempt row was not updated")
            self._touch_run()

    def _read_payloads(self, table: str, column: str) -> tuple[JsonObject, ...]:
        if table not in {"raw_listings", "source_attempts"}:
            raise ValueError(f"unsupported payload table: {table}")
        if column not in {"record_json", "payload_json"}:
            raise ValueError(f"unsupported payload column: {column}")
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                f"""
                SELECT {column}
                FROM {table}
                WHERE run_id = ?
                ORDER BY append_sequence, id
                """,
                (self._run_id,),
            ).fetchall()
        return tuple(_json_object(row[column], f"{table}.{column}") for row in rows)

    def _touch_run(self, now: str | None = None) -> None:
        self._connection.execute(
            """
            UPDATE runs
            SET updated_at = ?
            WHERE run_id = ?
            """,
            (now or _now(), self._run_id),
        )

    def _require_reserved_append(self, append_sequence: int) -> None:
        if self._append_sequence is None:
            raise RuntimeError("append attempt has not been reserved")
        if append_sequence != self._append_sequence:
            raise ValueError("record append_sequence must match reserved append attempt")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("run store is closed")


def read_processed_results_payload(
    database_path: Path,
    *,
    append_sequence: int | None = None,
    phase: str = "final",
) -> JsonObject:
    if not phase.strip():
        raise ValueError("phase must be non-empty")
    if not database_path.exists():
        raise FileNotFoundError(f"run database does not exist: {database_path}")
    connection = sqlite3.connect(str(database_path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        if append_sequence is None:
            row = connection.execute(
                """
                SELECT payload_json
                FROM processed_results
                WHERE phase = ?
                ORDER BY append_sequence DESC
                LIMIT 1
                """,
                (phase,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT payload_json
                FROM processed_results
                WHERE append_sequence = ? AND phase = ?
                """,
                (append_sequence, phase),
            ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise FileNotFoundError(f"processed results have not been written: {database_path}")
    return _json_object(row["payload_json"], "processed_results.payload_json")


def _json_dumps(value: object) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True)


def _schema_sql() -> str:
    return files("job_harness.v2.persistence").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8")


def _json_object(value: str, field_name: str) -> JsonObject:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} is not a JSON object")
    return parsed


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
