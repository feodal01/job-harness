"""On-disk JSONL run journal — the durability contract.

Every listing the engine parses is appended to `raw.jsonl` and fsync'd
before the engine accepts the next listing. `summary.json` is rewritten
atomically (tmp + rename) so a reader either sees the previous full
version or the new full version, never a partial one.

Used by:
  • SearchEngine to record events as they happen
  • RunRegistry / MCP `search_status` and `search_results` to read state
    without depending on the engine task still being alive

The reader (`RunJournal.read`) is the source of truth: status and results
tools work after process restart, `kill -9`, or eviction of the engine
task.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_harness.types import (
    RunState,
    SearchRequest,
    SourceState,
    SourceStatus,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Event record types written to raw.jsonl
# ---------------------------------------------------------------------------

EVENT_RUN_STARTED = "run_started"
EVENT_SOURCE_STARTED = "source_started"
EVENT_SOURCE_PROGRESS = "source_progress"
EVENT_LISTING = "listing"
EVENT_FILTER_DECISION = "filter_decision"
EVENT_DEDUPE_DECISION = "dedupe_decision"
EVENT_ENGINE_PROGRESS = "engine_progress"
EVENT_SOURCE_STATUS = "source_status"
EVENT_RUN_FINISHED = "run_finished"
EVENT_LISTINGS_PURGED = "listings_purged"
EVENT_RUN_RETRY_STARTED = "run_retry_started"

ALL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_RUN_STARTED,
        EVENT_SOURCE_STARTED,
        EVENT_SOURCE_PROGRESS,
        EVENT_LISTING,
        EVENT_FILTER_DECISION,
        EVENT_DEDUPE_DECISION,
        EVENT_ENGINE_PROGRESS,
        EVENT_SOURCE_STATUS,
        EVENT_RUN_FINISHED,
        EVENT_LISTINGS_PURGED,
        EVENT_RUN_RETRY_STARTED,
    }
)


# ---------------------------------------------------------------------------
# Snapshot dataclass returned by the reader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JournalSnapshot:
    """Materialised view of a journal at a point in time.

    Produced by RunJournalReader.snapshot() and embedded in summary.json.
    """

    run_id: str
    state: RunState
    started_at: str
    ended_at: str | None
    elapsed_ms: int
    request: dict[str, Any]
    sources: dict[str, SourceStatus]
    listings: list[dict[str, Any]]
    listings_count: int
    errors: list[str]
    flag_enforcement: dict[str, Any] = field(default_factory=dict)
    result_sanity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "elapsed_ms": self.elapsed_ms,
            "request": self.request,
            "sources": {name: status.to_dict() for name, status in self.sources.items()},
            "listings_count": self.listings_count,
            "errors": list(self.errors),
            "flag_enforcement": dict(self.flag_enforcement),
            "result_sanity": dict(self.result_sanity),
        }


# ---------------------------------------------------------------------------
# Run id helpers
# ---------------------------------------------------------------------------


def generate_run_id(*, now: datetime | None = None) -> str:
    """Produce a sortable, unique run id of the form r-YYYYMMDD-HHMMSS-<6hex>.

    `now` injectable for tests.
    """
    moment = now if now is not None else datetime.now(UTC).replace(tzinfo=None)
    suffix = secrets.token_hex(3)
    return f"r-{moment.strftime('%Y%m%d-%H%M%S')}-{suffix}"


def is_run_id(value: str) -> bool:
    """Cheap shape check used by the run registry to reject obvious garbage."""
    if not value.startswith("r-"):
        return False
    parts = value.split("-")
    if len(parts) != 4:
        return False
    _, date, time, suffix = parts
    return (
        len(date) == 8
        and date.isdigit()
        and len(time) == 6
        and time.isdigit()
        and len(suffix) == 6
        and all(c in "0123456789abcdef" for c in suffix)
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class RunJournalWriter:
    """Append-only, fsync'd writer for one run.

    Thread-safe under a per-instance lock — the engine may dispatch
    record writes from multiple coroutines/threads concurrently.

    Each call to a write_* method does:
        1. os.write(fd, line)
        2. os.fsync(fd)
    before returning. fsync per record is in microseconds on local SSDs.

    summary.json is rewritten atomically via the `rewrite_summary` method.
    """

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = Path(run_dir)
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._raw_path = self._run_dir / "raw.jsonl"
        self._summary_path = self._run_dir / "summary.json"
        self._fd = os.open(
            str(self._raw_path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        self._lock = threading.Lock()
        self._closed = False
        self._disk_full = False

    # --- properties --------------------------------------------------------

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def raw_path(self) -> Path:
        return self._raw_path

    @property
    def summary_path(self) -> Path:
        return self._summary_path

    @property
    def disk_full(self) -> bool:
        """True if a previous write hit ENOSPC. The engine should give up."""
        return self._disk_full

    # --- internal ---------------------------------------------------------

    def _append(self, record: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError(f"journal {self._raw_path} already closed")
        # Single-line JSON, UTF-8, \n terminator. ensure_ascii=False keeps
        # cyrillic readable for humans tailing the file.
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        encoded = payload.encode("utf-8")
        with self._lock:
            if self._disk_full:
                # Surface ENOSPC deterministically without writing partial bytes.
                raise OSError(28, "No space left on device")
            try:
                os.write(self._fd, encoded)
                os.fsync(self._fd)
            except OSError as exc:
                if exc.errno == 28:
                    self._disk_full = True
                raise

    # --- public write API -------------------------------------------------

    def write_run_started(self, *, run_id: str, request: SearchRequest) -> None:
        self._append(
            {
                "type": EVENT_RUN_STARTED,
                "ts": utc_now_iso(),
                "run_id": run_id,
                "request": request.to_dict(),
            }
        )

    def write_source_started(
        self,
        *,
        source: str,
        display_name: str,
        transport: str,
        deadline_ms: int,
    ) -> None:
        self._append(
            {
                "type": EVENT_SOURCE_STARTED,
                "ts": utc_now_iso(),
                "source": source,
                "display_name": display_name,
                "transport": transport,
                "deadline_ms": deadline_ms,
            }
        )

    def write_source_progress(
        self,
        *,
        source: str,
        raw_count: int,
        note: str | None = None,
    ) -> None:
        self._append(
            {
                "type": EVENT_SOURCE_PROGRESS,
                "ts": utc_now_iso(),
                "source": source,
                "raw_count": raw_count,
                "note": note,
            }
        )

    def write_listing(self, *, source: str, listing: dict[str, Any]) -> None:
        self._append(
            {
                "type": EVENT_LISTING,
                "ts": utc_now_iso(),
                "source": source,
                "listing": listing,
            }
        )

    def write_filter_decision(
        self,
        *,
        listing_url: str,
        kept: bool,
        reason: str,
    ) -> None:
        self._append(
            {
                "type": EVENT_FILTER_DECISION,
                "ts": utc_now_iso(),
                "listing_url": listing_url,
                "kept": kept,
                "reason": reason,
            }
        )

    def write_dedupe_decision(self, *, kept: str, dropped: list[str]) -> None:
        self._append(
            {
                "type": EVENT_DEDUPE_DECISION,
                "ts": utc_now_iso(),
                "kept": kept,
                "dropped": list(dropped),
            }
        )

    def write_engine_progress(self, *, sources_done: int, sources_total: int) -> None:
        self._append(
            {
                "type": EVENT_ENGINE_PROGRESS,
                "ts": utc_now_iso(),
                "sources_done": sources_done,
                "sources_total": sources_total,
            }
        )

    def write_source_status(self, status: SourceStatus) -> None:
        payload = {"type": EVENT_SOURCE_STATUS, "ts": utc_now_iso()}
        payload.update(status.to_dict())
        self._append(payload)

    def write_run_finished(
        self,
        *,
        state: RunState,
        final_listings_count: int,
        errors: list[str],
    ) -> None:
        if state == RunState.RUNNING:
            raise ValueError("run_finished cannot record state=running")
        self._append(
            {
                "type": EVENT_RUN_FINISHED,
                "ts": utc_now_iso(),
                "state": state.value,
                "final_listings_count": final_listings_count,
                "errors": list(errors),
            }
        )

    def write_listings_purged(self, *, sources: list[str]) -> None:
        self._append(
            {
                "type": EVENT_LISTINGS_PURGED,
                "ts": utc_now_iso(),
                "sources": list(sources),
            }
        )

    def write_run_retry_started(self, *, sources: list[str]) -> None:
        self._append(
            {
                "type": EVENT_RUN_RETRY_STARTED,
                "ts": utc_now_iso(),
                "sources": list(sources),
            }
        )

    # --- summary.json -----------------------------------------------------

    def rewrite_summary(self, snapshot: JournalSnapshot) -> None:
        """Atomically rewrite summary.json from the given snapshot.

        Writes `summary.json.tmp` with fsync, then `os.replace`s it onto
        `summary.json`. A reader sees either the old file or the new one,
        never a partial write — survives kill -9 between write and rename.
        """
        tmp = self._summary_path.with_suffix(".json.tmp")
        payload = json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        # Open with O_TRUNC since we always rewrite from scratch.
        fd = os.open(
            str(tmp),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o644,
        )
        try:
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            except OSError as exc:
                if exc.errno == 28:
                    self._disk_full = True
                raise
        finally:
            os.close(fd)
        os.replace(tmp, self._summary_path)

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the journal file descriptor. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                os.close(self._fd)
            except OSError:
                pass

    def __enter__(self) -> RunJournalWriter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class RunJournalReader:
    """Stateless reader for one run's journal.

    Never holds an open fd — `snapshot()` and `iter_events()` open the
    file, materialise/iterate, and close. Safe to call concurrently with
    the writer because we use line-terminated records and skip a
    half-written trailing line.
    """

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = Path(run_dir)
        self._raw_path = self._run_dir / "raw.jsonl"
        self._summary_path = self._run_dir / "summary.json"
        self._results_path = self._run_dir / "results.json"

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def raw_path(self) -> Path:
        return self._raw_path

    @property
    def summary_path(self) -> Path:
        return self._summary_path

    @property
    def results_path(self) -> Path:
        return self._results_path

    def exists(self) -> bool:
        return self._run_dir.exists()

    # --- raw event iteration ---------------------------------------------

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Yield one decoded JSON record per complete line.

        A trailing line without a `\\n` (a torn write from a crash between
        write and fsync) is skipped silently. A line that fails to parse
        is also skipped — the writer never produces malformed JSON, so
        bad lines are by definition torn.
        """
        if not self._raw_path.exists():
            return
        with self._raw_path.open("rb") as fh:
            for raw in fh:
                # Reject torn last line: writer always emits \n.
                if not raw.endswith(b"\n"):
                    continue
                try:
                    yield json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    # Defensive: should never happen with our writer.
                    continue

    # --- materialisation ---------------------------------------------------

    def snapshot(self) -> JournalSnapshot:
        """Replay the journal into a snapshot.

        Works on running, completed, cancelled, and failed runs alike.
        For a still-running run the snapshot reflects whatever has been
        recorded so far.
        """
        run_id = ""
        started_at: str = ""
        ended_at: str | None = None
        elapsed_started_ms: int | None = None
        request: dict[str, Any] = {}
        # Source bookkeeping is small; we materialise everything.
        sources: dict[str, SourceStatus] = {}
        listing_entries: list[tuple[str, dict[str, Any]]] = []
        state = RunState.RUNNING

        # We don't have wall-clock from the journal alone; use ts deltas.
        first_ts: str | None = None
        last_ts: str | None = None

        for record in self.iter_events():
            etype = record.get("type")
            if etype not in ALL_EVENT_TYPES:
                continue
            ts = record.get("ts")
            if isinstance(ts, str):
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if etype == EVENT_RUN_STARTED:
                run_id = record.get("run_id", run_id)
                started_at = record.get("ts", "")
                request = record.get("request") or {}
            elif etype == EVENT_LISTING:
                listing = record.get("listing")
                if isinstance(listing, dict):
                    source = str(record.get("source") or listing.get("source") or "")
                    listing_entries.append((source, listing))
            elif etype == EVENT_LISTINGS_PURGED:
                purge = set(record.get("sources") or ())
                listing_entries = [
                    (source, listing)
                    for source, listing in listing_entries
                    if source not in purge
                    and str(listing.get("source") or "") not in purge
                ]
            elif etype == EVENT_SOURCE_STATUS:
                try:
                    status = SourceStatus.from_dict(record)
                except (KeyError, ValueError):
                    continue
                sources[status.source] = status
            elif etype == EVENT_RUN_RETRY_STARTED:
                state = RunState.RUNNING
                ended_at = None
            elif etype == EVENT_RUN_FINISHED:
                state = RunState(record.get("state", RunState.FAILED.value))
                ended_at = record.get("ts")

        listings = [listing for _, listing in listing_entries]
        errors = [
            f"{status.source}: {status.error_message}"
            for status in sources.values()
            if status.error_message and status.state != SourceState.OK
        ]

        # Compute elapsed from first/last ts when possible. Tests use the
        # exact ISO format produced by utc_now_iso(), so a round-trip
        # parse is safe; fall back to 0 if anything is off.
        elapsed_ms = 0
        if first_ts and last_ts:
            elapsed_ms = max(0, _iso_delta_ms(first_ts, last_ts))
        # Keep the linter happy about the unused intermediate.
        _ = elapsed_started_ms

        return JournalSnapshot(
            run_id=run_id,
            state=state,
            started_at=started_at,
            ended_at=ended_at,
            elapsed_ms=elapsed_ms,
            request=request,
            sources=sources,
            listings=listings,
            listings_count=len(listings),
            errors=errors,
        )

    # --- summary.json ------------------------------------------------------

    def read_summary(self) -> dict[str, Any] | None:
        """Return the parsed summary.json, or None if it does not exist."""
        if not self._summary_path.exists():
            return None
        try:
            return json.loads(self._summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # --- results.json ------------------------------------------------------

    def write_results(self, payload: dict[str, Any]) -> Path:
        """Atomically write the agent-facing results export."""
        _atomic_write_json(self._results_path, payload)
        return self._results_path.resolve()


def materialize_listings(snap: JournalSnapshot) -> list[dict[str, Any]]:
    """Build the agent-facing listing set with optional dedupe."""
    from job_harness.dedupe_filter import (
        apply_filter_plan,
        build_filter_plan,
        dedupe_listings,
        order_by_experience_match,
    )
    from job_harness.models import JobListing

    listings: list[JobListing] = []
    fields = JobListing.__dataclass_fields__
    for raw in snap.listings:
        try:
            listings.append(
                JobListing(**{k: v for k, v in raw.items() if k in fields})
            )
        except TypeError:
            continue

    experience_levels = tuple(str(item) for item in snap.request.get("experience_levels") or ())
    filter_plan = build_filter_plan(
        remote_only=bool(snap.request.get("remote_only", False)),
        has_salary=bool(snap.request.get("has_salary", False)),
        exclude_companies=",".join(snap.request.get("exclude_companies") or ()) or None,
        experience_levels=experience_levels,
        exclude_keywords=",".join(snap.request.get("exclude_keywords") or ()) or None,
        exclude_keywords_context=",".join(snap.request.get("exclude_keywords_context") or ()) or None,
        location=snap.request.get("location"),
    )
    listings = apply_filter_plan(listings, filter_plan)
    listings = order_by_experience_match(listings, experience_levels)

    if snap.request.get("dedupe", True):
        listings = dedupe_listings(listings)
        listings = order_by_experience_match(listings, experience_levels)

    max_results = int(snap.request.get("max_results", 20))
    return [item.to_dict() for item in listings[:max_results]]


def build_results_payload(
    snap: JournalSnapshot,
    *,
    include_sources: bool = False,
) -> dict[str, Any]:
    """Build the envelope written to results.json."""
    listings = materialize_listings(snap)
    payload: dict[str, Any] = {
        "run_id": snap.run_id,
        "state": snap.state.value,
        "started_at": snap.started_at,
        "ended_at": snap.ended_at,
        "elapsed_ms": snap.elapsed_ms,
        "listings_count": len(listings),
        "request": snap.request,
        "errors": list(snap.errors),
        "listings": listings,
    }
    if include_sources:
        payload["sources"] = {
            name: status.to_dict() for name, status in snap.sources.items()
        }
    return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically via tmp + replace + fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _iso_delta_ms(a: str, b: str) -> int:
    """Best-effort millisecond delta between two ISO timestamps.

    Returns 0 if either timestamp cannot be parsed. The writer always
    emits utc_now_iso(), so the format is stable.
    """
    try:
        ta = datetime.fromisoformat(a.rstrip("Z"))
        tb = datetime.fromisoformat(b.rstrip("Z"))
    except ValueError:
        return 0
    delta = tb - ta
    return int(delta.total_seconds() * 1000)


def iter_run_dirs(runs_root: Path) -> Iterable[Path]:
    """Yield run directories under `runs_root`, oldest first.

    Used by the registry GC and `list_active_runs`. The order is taken
    from the directory name (which contains a sortable timestamp), not
    from `os.stat`, so a clock skew on the filesystem does not reshuffle
    runs.
    """
    if not runs_root.exists():
        return
    for entry in sorted(runs_root.iterdir(), key=lambda p: p.name):
        if entry.is_dir() and is_run_id(entry.name):
            yield entry
