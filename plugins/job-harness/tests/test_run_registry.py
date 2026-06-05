"""Tests for RunRegistry — lifecycle, caps, GC, idle timeout, restart."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from job_harness.run_journal import RunJournalReader, RunJournalWriter
from job_harness.run_registry import (
    MaxConcurrentRunsReached,
    RunRegistry,
    UnknownRunId,
)
from job_harness.types import RunState, SearchRequest

# ---------------------------------------------------------------------------
# Fake engine_runner stubs
# ---------------------------------------------------------------------------


async def _success_runner(request, journal, run_id):
    journal.write_run_started(run_id=run_id, request=request)
    journal.write_run_finished(state=RunState.COMPLETED, final_listings_count=0, errors=[])
    journal.rewrite_summary(RunJournalReader(journal.run_dir).snapshot())


def _make_slow_runner(start_evt: asyncio.Event, release_evt: asyncio.Event):
    async def runner(request, journal, run_id):
        journal.write_run_started(run_id=run_id, request=request)
        start_evt.set()
        try:
            await release_evt.wait()
        except asyncio.CancelledError:
            journal.write_run_finished(
                state=RunState.CANCELLED, final_listings_count=0, errors=["cancelled"],
            )
            journal.rewrite_summary(RunJournalReader(journal.run_dir).snapshot())
            raise
        journal.write_run_finished(
            state=RunState.COMPLETED, final_listings_count=0, errors=[],
        )
        journal.rewrite_summary(RunJournalReader(journal.run_dir).snapshot())

    return runner


async def _wait_until(predicate, *, timeout: float = 2.0, step: float = 0.02):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if predicate():
            return True
        await asyncio.sleep(step)
    return False


def _request(query="QA") -> SearchRequest:
    return SearchRequest(query=query)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class StartTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_returns_run_id_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(runs_root=Path(d), engine_runner=_success_runner)
            run = await reg.start(_request())
            self.assertTrue(run.run_id.startswith("r-"))
            self.assertTrue(run.run_dir.exists())
            await reg.shutdown()

    async def test_run_completes_and_writes_journal(self):
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(runs_root=Path(d), engine_runner=_success_runner)
            run = await reg.start(_request())
            assert run.task is not None
            await run.task
            snap = RunJournalReader(run.run_dir).snapshot()
            self.assertEqual(snap.state, RunState.COMPLETED)
            await reg.shutdown()


class ConcurrencyLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_max_concurrent_runs_enforced(self):
        start = asyncio.Event()
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(
                runs_root=Path(d),
                engine_runner=_make_slow_runner(start, release),
                max_concurrent_runs=2,
            )
            r1 = await reg.start(_request())
            r2 = await reg.start(_request())
            with self.assertRaises(MaxConcurrentRunsReached) as ctx:
                await reg.start(_request())
            active_ids = {s.run_id for s in ctx.exception.active}
            self.assertEqual(active_ids, {r1.run_id, r2.run_id})
            release.set()
            assert r1.task is not None and r2.task is not None
            await asyncio.gather(r1.task, r2.task)
            await reg.shutdown()


class CancelTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_propagates_and_writes_final_state(self):
        start = asyncio.Event()
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(
                runs_root=Path(d),
                engine_runner=_make_slow_runner(start, release),
            )
            run = await reg.start(_request())
            await asyncio.wait_for(start.wait(), timeout=2.0)
            await reg.cancel(run.run_id)
            assert run.task is not None
            with self.assertRaises(asyncio.CancelledError):
                await run.task
            snap = RunJournalReader(run.run_dir).snapshot()
            self.assertEqual(snap.state, RunState.CANCELLED)
            await reg.shutdown()

    async def test_cancel_is_idempotent(self):
        start = asyncio.Event()
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(
                runs_root=Path(d),
                engine_runner=_make_slow_runner(start, release),
            )
            run = await reg.start(_request())
            await asyncio.wait_for(start.wait(), timeout=2.0)
            await reg.cancel(run.run_id)
            # Second cancel must not raise.
            await reg.cancel(run.run_id)
            assert run.task is not None
            with self.assertRaises(asyncio.CancelledError):
                await run.task
            await reg.shutdown()

    async def test_cancel_unknown_id_raises(self):
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(runs_root=Path(d), engine_runner=_success_runner)
            with self.assertRaises(UnknownRunId):
                await reg.cancel("r-20260605-000000-aaaaaa")
            await reg.shutdown()


class TouchTest(unittest.IsolatedAsyncioTestCase):
    async def test_touch_bumps_last_poll_at(self):
        start = asyncio.Event()
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(
                runs_root=Path(d),
                engine_runner=_make_slow_runner(start, release),
            )
            run = await reg.start(_request())
            t0 = run.last_poll_at
            await asyncio.sleep(0.05)
            await reg.touch(run.run_id)
            self.assertGreater(reg.get(run.run_id).last_poll_at, t0)
            release.set()
            assert run.task is not None
            await run.task
            await reg.shutdown()


class IdleTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_idle_sweep_cancels_unpolled_runs(self):
        start = asyncio.Event()
        release = asyncio.Event()
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(
                runs_root=Path(d),
                engine_runner=_make_slow_runner(start, release),
                run_idle_timeout_s=0,           # any positive idle triggers
                idle_sweep_interval_s=0.05,     # sweep often
            )
            await reg.start_sweep()
            run = await reg.start(_request())
            await asyncio.wait_for(start.wait(), timeout=2.0)
            assert run.task is not None
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(run.task, timeout=2.0)
            snap = RunJournalReader(run.run_dir).snapshot()
            self.assertEqual(snap.state, RunState.CANCELLED)
            await reg.shutdown()


class ListRecentTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_recent_returns_active_and_disk_runs(self):
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(runs_root=Path(d), engine_runner=_success_runner)
            r1 = await reg.start(_request(query="alpha"))
            assert r1.task is not None
            await r1.task
            r2 = await reg.start(_request(query="beta"))
            assert r2.task is not None
            await r2.task
            recents = reg.list_recent(limit=10)
            queries = [s.query for s in recents]
            self.assertEqual(set(queries), {"alpha", "beta"})
            await reg.shutdown()


class RestartRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_orphan_running_run_marked_failed_on_startup(self):
        with tempfile.TemporaryDirectory() as d:
            run_id = "r-20260605-000000-deadbe"
            run_dir = Path(d) / run_id
            run_dir.mkdir()
            # Simulate a pre-restart run: summary.json says running, no
            # run_finished event.
            writer = RunJournalWriter(run_dir)
            writer.write_run_started(run_id=run_id, request=_request())
            writer.rewrite_summary(RunJournalReader(run_dir).snapshot())
            writer.close()
            # Construct a new registry pointed at this directory — should
            # flag the orphan as failed.
            reg = RunRegistry(runs_root=Path(d), engine_runner=_success_runner)
            data = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(data["state"], RunState.FAILED.value)
            self.assertEqual(data.get("failure_reason"), "server_restart")
            await reg.shutdown()


class RetentionGCTest(unittest.IsolatedAsyncioTestCase):
    async def test_runs_older_than_retention_horizon_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            old_id = "r-20200101-000000-cafeba"
            (Path(d) / old_id).mkdir()
            # Construct a stub journal so iter_run_dirs picks it up.
            (Path(d) / old_id / "raw.jsonl").write_text("", encoding="utf-8")
            # Retention 24h: this run is several years old, must be gone.
            reg = RunRegistry(
                runs_root=Path(d),
                engine_runner=_success_runner,
                run_retention_hours=24,
            )
            self.assertFalse((Path(d) / old_id).exists())
            await reg.shutdown()


class JournalReaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_journal_works_for_completed_run(self):
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(runs_root=Path(d), engine_runner=_success_runner)
            run = await reg.start(_request())
            assert run.task is not None
            await run.task
            reader = reg.read_journal(run.run_id)
            snap = reader.snapshot()
            self.assertEqual(snap.state, RunState.COMPLETED)
            await reg.shutdown()

    async def test_read_journal_unknown_id_raises(self):
        with tempfile.TemporaryDirectory() as d:
            reg = RunRegistry(runs_root=Path(d), engine_runner=_success_runner)
            with self.assertRaises(UnknownRunId):
                reg.read_journal("not-a-run-id")
            await reg.shutdown()


if __name__ == "__main__":
    unittest.main()
