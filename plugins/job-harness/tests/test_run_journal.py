"""Tests for the on-disk run journal — durability contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.models import JobListing
from job_harness.run_journal import (
    EVENT_LISTING,
    EVENT_RUN_STARTED,
    EVENT_SOURCE_STATUS,
    JournalSnapshot,
    RunJournalReader,
    RunJournalWriter,
    generate_run_id,
    is_run_id,
    iter_run_dirs,
    materialize_listings,
)
from job_harness.types import (
    FAILURE_MODE_TO_STATE,
    FailureMode,
    FilterSupport,
    RunState,
    SearchRequest,
    SourceState,
    SourceStatus,
    Transport,
)


def _make_request(**overrides):
    overrides.setdefault("query", "QA engineer")
    return SearchRequest(**overrides)


def _ok_status(source="hh_ru", raw_count=12):
    return SourceStatus(
        source=source,
        display_name=source,
        transport=Transport.BROWSER,
        state=SourceState.OK,
        failure_mode=None,
        duration_ms=8234,
        raw_count=raw_count,
        after_filter_count=raw_count,
        after_dedupe_count=raw_count,
        flag_enforcement={"remote_only": FilterSupport.SERVER},
    )


def _blocked_status(source="hh_ru"):
    return SourceStatus(
        source=source,
        display_name=source,
        transport=Transport.BROWSER,
        state=SourceState.BLOCKED,
        failure_mode=FailureMode.ANTI_BOT_PAGE,
        duration_ms=2000,
        anti_bot_signal="Доступ ограничен",
        error_class="AntiBotBlocked",
        error_message="anti-bot interstitial",
    )


class RunIdTest(unittest.TestCase):
    def test_generate_run_id_matches_shape(self):
        rid = generate_run_id()
        self.assertTrue(is_run_id(rid), rid)

    def test_run_id_validation_rejects_garbage(self):
        for bad in ("", "abc", "r-1-2-3", "r-foo-bar-baz", "r-20260605-000000-ZZZZZZ"):
            self.assertFalse(is_run_id(bad), bad)


class SourceStatusInvariantsTest(unittest.TestCase):
    """The (state, failure_mode) invariants must hold at construction time."""

    def test_ok_requires_no_failure_mode(self):
        with self.assertRaises(ValueError):
            SourceStatus(
                source="x", display_name="x", transport=Transport.HTTP,
                state=SourceState.OK, failure_mode=FailureMode.PARSE_ERROR,
                duration_ms=1,
            )

    def test_non_ok_requires_failure_mode(self):
        with self.assertRaises(ValueError):
            SourceStatus(
                source="x", display_name="x", transport=Transport.HTTP,
                state=SourceState.ERROR, failure_mode=None,
                duration_ms=1,
            )

    def test_failure_mode_state_mismatch_rejected(self):
        # GOTO_TIMEOUT belongs under TIMEOUT, not ERROR.
        with self.assertRaises(ValueError):
            SourceStatus(
                source="x", display_name="x", transport=Transport.BROWSER,
                state=SourceState.ERROR, failure_mode=FailureMode.GOTO_TIMEOUT,
                duration_ms=1,
            )

    def test_every_failure_mode_resolves_to_a_state(self):
        for mode in FailureMode:
            self.assertIn(mode, FAILURE_MODE_TO_STATE, mode)

    def test_failure_mode_state_table_is_closed(self):
        # Every entry in the table must round-trip through SourceStatus.
        for mode, state in FAILURE_MODE_TO_STATE.items():
            status = SourceStatus(
                source="x", display_name="x", transport=Transport.HTTP,
                state=state, failure_mode=mode, duration_ms=1,
            )
            self.assertEqual(status.failure_mode, mode)


class WriterBasicsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _events(self):
        return list(RunJournalReader(self.run_dir).iter_events())

    def test_run_started_is_first_record(self):
        req = _make_request()
        with RunJournalWriter(self.run_dir) as w:
            w.write_run_started(run_id="r-test", request=req)
        events = self._events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], EVENT_RUN_STARTED)
        self.assertEqual(events[0]["request"]["query"], "QA engineer")

    def test_each_record_is_a_single_terminated_line(self):
        req = _make_request()
        with RunJournalWriter(self.run_dir) as w:
            w.write_run_started(run_id="r-x", request=req)
            w.write_listing(source="hh_ru", listing={"title": "QA", "url": "https://x"})
            w.write_source_status(_ok_status())
        raw = (self.run_dir / "raw.jsonl").read_bytes()
        self.assertEqual(raw.count(b"\n"), 3)
        # No record may contain an unescaped newline in the middle.
        for line in raw.splitlines():
            self.assertGreater(len(line), 2)
            self.assertEqual(json.loads(line)["type"] in (EVENT_RUN_STARTED, EVENT_LISTING, EVENT_SOURCE_STATUS), True)

    def test_fsync_called_per_record(self):
        req = _make_request()
        with patch("job_harness.run_journal.os.fsync") as fsync_spy:
            with RunJournalWriter(self.run_dir) as w:
                w.write_run_started(run_id="r-x", request=req)
                w.write_listing(source="hh_ru", listing={"title": "A"})
                w.write_listing(source="hh_ru", listing={"title": "B"})
                w.write_source_status(_ok_status())
        # 4 record writes → 4 fsync calls.
        self.assertEqual(fsync_spy.call_count, 4)

    def test_run_finished_requires_terminal_state(self):
        with RunJournalWriter(self.run_dir) as w:
            with self.assertRaises(ValueError):
                w.write_run_finished(state=RunState.RUNNING, final_listings_count=0, errors=[])

    def test_unicode_records_round_trip(self):
        req = _make_request(query="тестировщик")
        with RunJournalWriter(self.run_dir) as w:
            w.write_run_started(run_id="r-x", request=req)
            w.write_source_status(_blocked_status())
        snap = RunJournalReader(self.run_dir).snapshot()
        self.assertEqual(snap.request["query"], "тестировщик")
        self.assertEqual(snap.sources["hh_ru"].anti_bot_signal, "Доступ ограничен")

    def test_writer_close_is_idempotent(self):
        w = RunJournalWriter(self.run_dir)
        w.close()
        w.close()
        # Writing after close must fail loudly, not silently corrupt.
        with self.assertRaises(RuntimeError):
            w.write_run_started(run_id="r", request=_make_request())


class WriterConcurrencyTest(unittest.TestCase):
    def test_concurrent_writes_produce_well_formed_lines(self):
        import threading

        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            N = 50

            def write_listings(prefix):
                for i in range(N):
                    w.write_listing(
                        source="hh_ru",
                        listing={"title": f"{prefix}-{i}", "url": f"https://x/{prefix}/{i}"},
                    )

            threads = [threading.Thread(target=write_listings, args=(p,)) for p in ("A", "B", "C")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            w.close()

            events = list(RunJournalReader(run_dir).iter_events())
            listings = [e for e in events if e["type"] == EVENT_LISTING]
            self.assertEqual(len(listings), 3 * N)
            urls = {e["listing"]["url"] for e in listings}
            self.assertEqual(len(urls), 3 * N, "no two writes share a listing url")


class SummaryAtomicityTest(unittest.TestCase):
    def test_summary_rewrite_is_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            w.write_source_status(_ok_status())
            snap = RunJournalReader(run_dir).snapshot()
            # First rewrite.
            w.rewrite_summary(snap)
            s1 = (run_dir / "summary.json").read_text(encoding="utf-8")
            # Second rewrite — different content.
            w.write_source_status(
                SourceStatus(
                    source="habr_career",
                    display_name="Habr Career",
                    transport=Transport.HTTP,
                    state=SourceState.OK,
                    failure_mode=None,
                    duration_ms=200,
                    raw_count=3,
                )
            )
            snap2 = RunJournalReader(run_dir).snapshot()
            w.rewrite_summary(snap2)
            s2 = (run_dir / "summary.json").read_text(encoding="utf-8")
            self.assertNotEqual(s1, s2)
            # The tmp file should not linger.
            self.assertFalse((run_dir / "summary.json.tmp").exists())
            w.close()


class ReaderTest(unittest.TestCase):
    def test_snapshot_of_completed_run(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            w.write_source_started(
                source="hh_ru", display_name="hh.ru", transport="browser", deadline_ms=30000,
            )
            w.write_listing(source="hh_ru", listing={"title": "QA", "url": "https://hh/1"})
            w.write_listing(source="hh_ru", listing={"title": "QA2", "url": "https://hh/2"})
            w.write_source_status(_ok_status(raw_count=2))
            w.write_run_finished(state=RunState.COMPLETED, final_listings_count=2, errors=[])
            w.close()

            snap = RunJournalReader(run_dir).snapshot()
            self.assertEqual(snap.state, RunState.COMPLETED)
            self.assertEqual(snap.listings_count, 2)
            self.assertEqual(snap.run_id, "r-x")
            self.assertIn("hh_ru", snap.sources)
            self.assertEqual(snap.sources["hh_ru"].state, SourceState.OK)

    def test_snapshot_of_running_run_returns_partial(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            w.write_listing(source="hh_ru", listing={"title": "A", "url": "https://hh/1"})
            # No source_status, no run_finished yet — engine is still going.
            snap = RunJournalReader(run_dir).snapshot()
            self.assertEqual(snap.state, RunState.RUNNING)
            self.assertEqual(snap.listings_count, 1)
            w.close()

    def test_snapshot_skips_torn_last_line(self):
        # Simulate a crash that wrote a partial line without \n.
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            w.write_listing(source="hh_ru", listing={"title": "A", "url": "https://hh/1"})
            w.close()
            # Append a half-record manually (no terminator).
            with (run_dir / "raw.jsonl").open("ab") as fh:
                fh.write(b'{"type":"listing","ts":"2026-06-05T00:00:00Z","source":"hh_ru","listing":{"title":"B"')
            snap = RunJournalReader(run_dir).snapshot()
            self.assertEqual(snap.listings_count, 1, "torn last line must be ignored")

    def test_blocked_status_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            w.write_source_status(_blocked_status())
            w.write_run_finished(state=RunState.COMPLETED, final_listings_count=0, errors=[])
            w.close()
            snap = RunJournalReader(run_dir).snapshot()
            status = snap.sources["hh_ru"]
            self.assertEqual(status.state, SourceState.BLOCKED)
            self.assertEqual(status.failure_mode, FailureMode.ANTI_BOT_PAGE)
            self.assertEqual(status.anti_bot_signal, "Доступ ограничен")

    def test_iter_run_dirs_orders_by_name(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ids = [
                "r-20260101-000000-aaaaaa",
                "r-20260605-120000-bbbbbb",
                "r-20260605-120100-cccccc",
            ]
            for rid in reversed(ids):  # create out of order
                (root / rid).mkdir()
            (root / "not-a-run").mkdir()  # ignored
            found = [d.name for d in iter_run_dirs(root)]
            self.assertEqual(found, ids)


class SimulatedKillTest(unittest.TestCase):
    """The acceptance test: kill -9 during a write must leave the journal
    in a state from which `RunJournalReader.snapshot()` returns the records
    that were fsync'd, and never returns garbage."""

    def test_subprocess_killed_mid_write_preserves_fsynced_records(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            script = f"""
import os, sys, time
sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / "src")!r})
from pathlib import Path
from job_harness.run_journal import RunJournalWriter
from job_harness.types import SearchRequest

w = RunJournalWriter(Path({str(run_dir)!r}))
w.write_run_started(run_id="r-kill", request=SearchRequest(query="QA"))
for i in range(5):
    w.write_listing(source="hh_ru", listing={{"title": f"L{{i}}", "url": f"https://x/{{i}}"}})
# Signal parent we're done with the durable writes.
sys.stdout.write("ready\\n"); sys.stdout.flush()
# Now hang forever; parent will SIGKILL us.
time.sleep(60)
"""
            proc = subprocess.Popen(
                [sys.executable, "-u", "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            assert proc.stdout is not None and proc.stderr is not None
            try:
                # Wait for the "ready" signal that all 5 listings were fsync'd.
                line = proc.stdout.readline()
                self.assertEqual(line.strip(), "ready")
            finally:
                # Hard kill and drain pipes to avoid leaks.
                proc.kill()
                proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()
            # Reader sees exactly: 1 run_started + 5 listings.
            events = list(RunJournalReader(run_dir).iter_events())
            self.assertEqual(events[0]["type"], EVENT_RUN_STARTED)
            self.assertEqual(sum(1 for e in events if e["type"] == EVENT_LISTING), 5)
            snap = RunJournalReader(run_dir).snapshot()
            # The run is RUNNING because no run_finished was written.
            self.assertEqual(snap.state, RunState.RUNNING)
            self.assertEqual(snap.listings_count, 5)


class RetryReplayTest(unittest.TestCase):
    def test_listings_purged_removes_source_listings(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            w.write_listing(
                source="bad",
                listing={"title": "A", "url": "https://x/1", "company": "Co", "source": "bad"},
            )
            w.write_listing(
                source="ok",
                listing={"title": "B", "url": "https://x/2", "company": "Co", "source": "ok"},
            )
            w.write_listings_purged(sources=["bad"])
            w.write_listing(
                source="bad",
                listing={"title": "A2", "url": "https://x/3", "company": "Co", "source": "bad"},
            )
            w.write_run_finished(state=RunState.COMPLETED, final_listings_count=2, errors=[])
            w.close()

            snap = RunJournalReader(run_dir).snapshot()
            titles = {item["title"] for item in snap.listings}
            self.assertEqual(titles, {"B", "A2"})

    def test_run_retry_started_reopens_running_state(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            w.write_run_finished(state=RunState.COMPLETED, final_listings_count=0, errors=[])
            w.write_run_retry_started(sources=["bad"])
            snap = RunJournalReader(run_dir).snapshot()
            self.assertEqual(snap.state, RunState.RUNNING)
            self.assertIsNone(snap.ended_at)
            w.write_run_finished(state=RunState.COMPLETED, final_listings_count=0, errors=[])
            w.close()
            snap2 = RunJournalReader(run_dir).snapshot()
            self.assertEqual(snap2.state, RunState.COMPLETED)

    def test_materialize_listings_dedupes(self):
        listing = JobListing(
            title="QA",
            url="https://hh.ru/vacancy/123",
            company="Acme",
            source="hh_ru",
        ).to_dict()
        snap = JournalSnapshot(
            run_id="r-x",
            state=RunState.COMPLETED,
            started_at="",
            ended_at="",
            elapsed_ms=0,
            request={"query": "QA", "dedupe": True, "max_results": 20},
            sources={},
            listings=[listing, dict(listing, source="habr_career")],
            listings_count=2,
            errors=[],
        )
        materialized = materialize_listings(snap)
        self.assertEqual(len(materialized), 1)


class DiskFullSimulationTest(unittest.TestCase):
    def test_enospc_marks_writer_disk_full(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            w = RunJournalWriter(run_dir)
            w.write_run_started(run_id="r-x", request=_make_request())
            # Inject ENOSPC via os.write spy.
            with patch("job_harness.run_journal.os.write", side_effect=OSError(28, "No space left on device")):
                with self.assertRaises(OSError) as ctx:
                    w.write_listing(source="hh_ru", listing={"title": "X"})
                self.assertEqual(ctx.exception.errno, 28)
            self.assertTrue(w.disk_full)
            # Subsequent writes also raise without trying to hit the disk again.
            with self.assertRaises(OSError):
                w.write_listing(source="hh_ru", listing={"title": "Y"})
            # The prior record is still readable.
            events = list(RunJournalReader(run_dir).iter_events())
            self.assertEqual(events[0]["type"], EVENT_RUN_STARTED)
            w.close()


if __name__ == "__main__":
    unittest.main()
