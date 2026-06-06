"""Run lifecycle for the non-blocking MCP surface.

`search_start` creates a Run; `search_status` / `search_results` read
from the journal without touching the engine task; `search_cancel`
cancels the task; `list_active_runs` snapshots the current registry +
disk for after-restart discovery.

Enforced limits (configurable on the constructor):

  * MAX_CONCURRENT_RUNS — `start` returns a structured error past this.
  * RUN_DISK_CAP_MB     — `start` GCs oldest non-active runs to fit.
  * RUN_RETENTION_HOURS — at start, runs older than this are deleted.
  * RUN_IDLE_TIMEOUT_S  — a background sweep self-cancels runs that
    nobody has polled for this long.

Crash recovery: at construction, any pre-existing run with
`state=running` in `summary.json` but no live task is rewritten to
`state=failed, failure_mode=server_restart`. The journal stays so
`search_results(run_id)` still works.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from job_harness.run_journal import (
    RunJournalReader,
    RunJournalWriter,
    generate_run_id,
    is_run_id,
    iter_run_dirs,
)
from job_harness.types import (
    RunState,
    SearchRequest,
)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MaxConcurrentRunsReached(Exception):
    """Raised by `start` when too many active runs already exist."""

    def __init__(self, active: list[RunSummary]):
        self.active = active
        super().__init__(f"max_concurrent_runs_reached ({len(active)} active)")


class UnknownRunId(Exception):
    """Raised when a caller references a run_id we don't know."""


class RunStillActive(Exception):
    """Raised when retry is requested while the run task is still live."""


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------


@dataclass
class Run:
    run_id: str
    run_dir: Path
    request: SearchRequest
    task: asyncio.Task[Any] | None
    started_at: datetime
    last_poll_at: datetime
    journal: RunJournalWriter | None = None
    retry_sources: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RunSummary:
    """Lightweight snapshot used by `list_active_runs` and the
    structured `max_concurrent_runs_reached` response."""

    run_id: str
    query: str
    state: RunState
    started_at: str
    elapsed_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "query": self.query,
            "state": self.state.value,
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms,
        }


# Callback signature for `start` / `retry`: given (request, journal, run_id,
# retry_sources), return an awaitable that runs the engine and writes the
# journal. `retry_sources` is None for a normal start.
EngineRunner = Callable[
    [SearchRequest, RunJournalWriter, str, tuple[str, ...] | None],
    Awaitable[Any],
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RunRegistry:
    """In-memory registry backed by `runs_root/` on disk.

    Owns the asyncio.Task for each active run. Exposed methods are
    intentionally minimal so the MCP layer in Phase 6 can wrap them
    one-to-one.
    """

    def __init__(
        self,
        *,
        runs_root: Path,
        engine_runner: EngineRunner,
        max_concurrent_runs: int = 4,
        run_disk_cap_mb: int = 500,
        run_retention_hours: int = 24,
        run_idle_timeout_s: int = 600,
        idle_sweep_interval_s: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runs_root = Path(runs_root)
        self._runs_root.mkdir(parents=True, exist_ok=True)
        self._engine_runner = engine_runner
        self._max_concurrent_runs = max_concurrent_runs
        self._run_disk_cap_mb = run_disk_cap_mb
        self._run_retention_hours = run_retention_hours
        self._run_idle_timeout_s = run_idle_timeout_s
        self._idle_sweep_interval_s = idle_sweep_interval_s
        self._clock = clock or (lambda: datetime.now(UTC))

        self._runs: dict[str, Run] = {}
        self._lock = asyncio.Lock()
        self._sweep_task: asyncio.Task[None] | None = None

        # Crash recovery + GC on startup.
        self._mark_orphaned_runs_failed()
        self._gc_disk()

    # --- lifecycle -------------------------------------------------------

    async def start_sweep(self) -> None:
        """Begin the background idle-timeout sweep. Idempotent."""
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(
                self._idle_sweep(), name="run-registry:idle-sweep"
            )

    async def stop_sweep(self) -> None:
        if self._sweep_task is not None and not self._sweep_task.done():
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except (asyncio.CancelledError, Exception):
                pass
        self._sweep_task = None

    async def shutdown(self) -> None:
        """Cancel every active task and wait for the journals to flush.

        Called from FastMCP SessionEnd in the MCP layer.
        """
        await self.stop_sweep()
        async with self._lock:
            tasks = [r.task for r in self._runs.values() if r.task and not r.task.done()]
            for r in self._runs.values():
                if r.task and not r.task.done():
                    r.task.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    # --- core API --------------------------------------------------------

    async def start(self, request: SearchRequest) -> Run:
        """Spin up a new run. Returns the Run immediately (≤ 100 ms)."""
        async with self._lock:
            active = [r for r in self._runs.values() if _is_active(r)]
            if len(active) >= self._max_concurrent_runs:
                summaries = [_summary_for(r, self._clock()) for r in active]
                raise MaxConcurrentRunsReached(summaries)

            self._gc_disk_locked()  # ensure cap before adding a new dir

            now = self._clock()
            run_id = generate_run_id(now=now.replace(tzinfo=None))
            run_dir = self._runs_root / run_id
            journal = RunJournalWriter(run_dir)
            run = Run(
                run_id=run_id,
                run_dir=run_dir,
                request=request,
                task=None,
                started_at=now,
                last_poll_at=now,
                journal=journal,
            )
            run.task = asyncio.create_task(
                self._supervise(run), name=f"run:{run_id}"
            )
            self._runs[run_id] = run
            return run

    def get(self, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise UnknownRunId(run_id)
        return run

    async def retry(
        self,
        run_id: str,
        *,
        retried_sources: tuple[str, ...],
        strict_flags: bool | None = None,
    ) -> Run:
        """Re-dispatch failed sources into an existing run journal."""
        from dataclasses import replace

        from job_harness.types import SearchRequest

        if not is_run_id(run_id):
            raise UnknownRunId(run_id)
        run_dir = self._runs_root / run_id
        if not run_dir.exists():
            raise UnknownRunId(run_id)

        snap = RunJournalReader(run_dir).snapshot()
        if snap.state == RunState.RUNNING:
            raise RunStillActive(run_id)

        base_request = SearchRequest.from_dict(snap.request)
        if strict_flags is not None:
            base_request = replace(base_request, strict_flags=strict_flags)
        retry_request = replace(base_request, sources=retried_sources)

        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None and _is_active(existing):
                raise RunStillActive(run_id)

            active = [r for r in self._runs.values() if _is_active(r)]
            if len(active) >= self._max_concurrent_runs:
                summaries = [_summary_for(r, self._clock()) for r in active]
                raise MaxConcurrentRunsReached(summaries)

            now = self._clock()
            journal = RunJournalWriter(run_dir)
            journal.write_listings_purged(sources=list(retried_sources))
            journal.write_run_retry_started(sources=list(retried_sources))
            started_at = existing.started_at if existing is not None else now
            run = Run(
                run_id=run_id,
                run_dir=run_dir,
                request=retry_request,
                task=None,
                started_at=started_at,
                last_poll_at=now,
                journal=journal,
                retry_sources=retried_sources,
            )
            run.task = asyncio.create_task(
                self._supervise(run), name=f"run-retry:{run_id}"
            )
            self._runs[run_id] = run
            return run

    async def touch(self, run_id: str) -> Run:
        """Bump `last_poll_at` for idle timeout tracking."""
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                # Touching a run that was evicted (e.g. after server restart)
                # is allowed — we just ignore it.
                raise UnknownRunId(run_id)
            run.last_poll_at = self._clock()
            return run

    async def cancel(self, run_id: str) -> Run:
        """Signal cancellation. Idempotent.

        Returns immediately; the engine task observes CancelledError
        on the next await checkpoint and writes the final journal.
        """
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise UnknownRunId(run_id)
            run.last_poll_at = self._clock()
            if run.task and not run.task.done():
                run.task.cancel()
            return run

    def list_recent(self, limit: int = 20) -> list[RunSummary]:
        """Snapshot of in-memory + on-disk runs ordered newest first."""
        summaries: list[RunSummary] = []
        now = self._clock()
        seen: set[str] = set()
        for run in self._runs.values():
            summaries.append(_summary_for(run, now))
            seen.add(run.run_id)
        for run_dir in iter_run_dirs(self._runs_root):
            if run_dir.name in seen:
                continue
            summary_data = _read_summary(run_dir)
            if summary_data is None:
                continue
            try:
                state = RunState(summary_data.get("state", RunState.FAILED.value))
            except ValueError:
                state = RunState.FAILED
            summaries.append(
                RunSummary(
                    run_id=run_dir.name,
                    query=(summary_data.get("request") or {}).get("query", ""),
                    state=state,
                    started_at=summary_data.get("started_at", ""),
                    elapsed_ms=int(summary_data.get("elapsed_ms", 0)),
                )
            )
        summaries.sort(key=lambda s: s.started_at, reverse=True)
        return summaries[:limit]

    def read_journal(self, run_id: str) -> RunJournalReader:
        """Return a fresh stateless reader for the run's directory.

        Works for runs that have been evicted from memory (e.g. after
        server restart) — only the directory needs to exist.
        """
        if not is_run_id(run_id):
            raise UnknownRunId(run_id)
        run_dir = self._runs_root / run_id
        if not run_dir.exists():
            raise UnknownRunId(run_id)
        return RunJournalReader(run_dir)

    # --- internal -------------------------------------------------------

    async def _supervise(self, run: Run) -> None:
        """Run the engine and ensure the journal is closed on the way out."""
        assert run.journal is not None
        try:
            try:
                await self._engine_runner(
                    run.request,
                    run.journal,
                    run.run_id,
                    run.retry_sources,
                )
            except asyncio.CancelledError:
                # Engine already wrote state=cancelled before re-raising.
                raise
            except Exception as exc:
                # Defensive: any unexpected error should still leave a
                # readable journal. Write a final run_finished if the
                # engine forgot.
                try:
                    snap = RunJournalReader(run.run_dir).snapshot()
                    if snap.state == RunState.RUNNING:
                        run.journal.write_run_finished(
                            state=RunState.FAILED,
                            final_listings_count=snap.listings_count,
                            errors=[f"engine-crash: {type(exc).__name__}: {exc}"],
                        )
                        run.journal.rewrite_summary(
                            RunJournalReader(run.run_dir).snapshot()
                        )
                except Exception:
                    pass
        finally:
            try:
                run.journal.close()
            except Exception:
                pass

    async def _idle_sweep(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._idle_sweep_interval_s)
                await self._sweep_once()
        except asyncio.CancelledError:
            return

    async def _sweep_once(self) -> None:
        now = self._clock()
        async with self._lock:
            to_cancel: list[Run] = []
            for run in self._runs.values():
                if not _is_active(run):
                    continue
                idle = (now - run.last_poll_at).total_seconds()
                if idle > self._run_idle_timeout_s:
                    to_cancel.append(run)
        for run in to_cancel:
            if run.task and not run.task.done():
                run.task.cancel()

    def _mark_orphaned_runs_failed(self) -> None:
        """Any run on disk whose summary says state=running but has no
        in-memory task gets marked (failed, server_restart)."""
        for run_dir in iter_run_dirs(self._runs_root):
            summary = _read_summary(run_dir)
            if summary is None:
                continue
            if summary.get("state") != RunState.RUNNING.value:
                continue
            # Append a synthetic run_finished record + rewrite summary.
            try:
                writer = RunJournalWriter(run_dir)
                writer.write_run_finished(
                    state=RunState.FAILED,
                    final_listings_count=int(summary.get("listings_count", 0)),
                    errors=["server_restart"],
                )
                # Patch the in-memory snapshot so the rewritten summary
                # carries failure_mode at the run level for readers.
                snap = RunJournalReader(run_dir).snapshot()
                writer.rewrite_summary(snap)
                writer.close()
                # Best-effort: append a runtime hint about server_restart.
                _annotate_summary_with_restart_reason(run_dir)
            except Exception:
                continue

    def _gc_disk(self) -> None:
        # Called at construction time, no lock needed.
        _gc_runs(
            self._runs_root,
            now=self._clock(),
            retention_hours=self._run_retention_hours,
            cap_mb=self._run_disk_cap_mb,
            keep_active=set(),
        )

    def _gc_disk_locked(self) -> None:
        # Lock-aware variant. Caller must hold self._lock.
        active = {r.run_id for r in self._runs.values() if _is_active(r)}
        _gc_runs(
            self._runs_root,
            now=self._clock(),
            retention_hours=self._run_retention_hours,
            cap_mb=self._run_disk_cap_mb,
            keep_active=active,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_active(run: Run) -> bool:
    return run.task is not None and not run.task.done()


def _summary_for(run: Run, now: datetime) -> RunSummary:
    if _is_active(run):
        state = RunState.RUNNING
    elif run.task is not None and run.task.cancelled():
        state = RunState.CANCELLED
    elif run.task is not None and run.task.exception() is not None:
        state = RunState.FAILED
    else:
        state = RunState.COMPLETED
    return RunSummary(
        run_id=run.run_id,
        query=run.request.query,
        state=state,
        started_at=run.started_at.isoformat(),
        elapsed_ms=int((now - run.started_at).total_seconds() * 1000),
    )


def _read_summary(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _annotate_summary_with_restart_reason(run_dir: Path) -> None:
    """Add a top-level `failure_reason=server_restart` to summary.json.

    The journal already recorded `errors=["server_restart"]`; this
    sets an explicit field that's easier to read at a glance.
    """
    path = run_dir / "summary.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data["failure_reason"] = "server_restart"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _gc_runs(
    runs_root: Path,
    *,
    now: datetime,
    retention_hours: int,
    cap_mb: int,
    keep_active: set[str],
) -> None:
    if not runs_root.exists():
        return
    dirs = list(iter_run_dirs(runs_root))
    if not dirs:
        return
    # 1. Retention sweep — drop runs older than retention_hours, oldest first.
    cutoff = now - timedelta(hours=retention_hours)
    for run_dir in dirs:
        if run_dir.name in keep_active:
            continue
        ts = _started_at_from_name(run_dir.name)
        if ts is None or ts >= cutoff:
            continue
        shutil.rmtree(run_dir, ignore_errors=True)
    # 2. Disk-cap sweep — drop oldest runs while the directory exceeds the cap.
    cap_bytes = cap_mb * 1024 * 1024
    dirs = list(iter_run_dirs(runs_root))
    total = sum(_dir_size(d) for d in dirs)
    for run_dir in dirs:
        if total <= cap_bytes:
            break
        if run_dir.name in keep_active:
            continue
        size = _dir_size(run_dir)
        shutil.rmtree(run_dir, ignore_errors=True)
        total -= size


def _started_at_from_name(name: str) -> datetime | None:
    """Parse `r-YYYYMMDD-HHMMSS-xxxxxx` back into a UTC timestamp."""
    try:
        _, date, t, _ = name.split("-")
        return datetime.strptime(f"{date}{t}", "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except (ValueError, IndexError):
        return None


def _dir_size(run_dir: Path) -> int:
    total = 0
    for path in run_dir.rglob("*"):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


_ = time  # forward-use placeholder so the linter does not strip the import
