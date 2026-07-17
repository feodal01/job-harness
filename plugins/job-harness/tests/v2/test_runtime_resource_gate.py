from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from job_harness.v2.ports import OperationContext
from job_harness.v2.runtime.resource_gate import (
    AcquireDecision,
    ResourceGate,
    ResourcePolicy,
    SqliteResourceGateBackend,
)


class SqliteResourceGateBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.database_path = Path(self._temporary_directory.name) / "resource-gate.sqlite"
        self.policy = ResourcePolicy(
            max_concurrency=1,
            min_interval_seconds=0.0,
            lease_seconds=10.0,
        )

    def test_independent_backends_share_one_concurrency_limit(self) -> None:
        first = SqliteResourceGateBackend(self.database_path)
        second = SqliteResourceGateBackend(self.database_path)

        acquired = first.try_acquire(
            resource_key="hh.ru",
            policy=self.policy,
            operation_id="managed-1",
            owner_id="worker-a",
            now=100.0,
        )
        blocked = second.try_acquire(
            resource_key="hh.ru",
            policy=self.policy,
            operation_id="direct-1",
            owner_id="worker-b",
            now=100.0,
        )

        self.assertIsNotNone(acquired.permit)
        self.assertIsNone(blocked.permit)
        self.assertEqual(blocked.retry_after_seconds, 0.25)

    def test_expired_slot_is_recovered_after_owner_crash(self) -> None:
        backend = SqliteResourceGateBackend(self.database_path)
        first = backend.try_acquire(
            resource_key="hh.ru",
            policy=self.policy,
            operation_id="crashed",
            owner_id="worker-a",
            now=100.0,
        )

        recovered = backend.try_acquire(
            resource_key="hh.ru",
            policy=self.policy,
            operation_id="replacement",
            owner_id="worker-b",
            now=111.0,
        )

        self.assertIsNotNone(first.permit)
        self.assertIsNotNone(recovered.permit)
        recovered_permit = recovered.permit
        if recovered_permit is None:
            self.fail("expired slot was not recovered")
        self.assertEqual(recovered_permit.operation_id, "replacement")

    def test_pacing_survives_release(self) -> None:
        policy = ResourcePolicy(
            max_concurrency=1,
            min_interval_seconds=2.0,
            lease_seconds=10.0,
        )
        backend = SqliteResourceGateBackend(self.database_path)
        first = backend.try_acquire(
            resource_key="hh.ru",
            policy=policy,
            operation_id="one",
            owner_id="worker",
            now=100.0,
        )
        if first.permit is None:
            self.fail("first slot was not acquired")
        backend.release(first.permit)

        early = backend.try_acquire(
            resource_key="hh.ru",
            policy=policy,
            operation_id="two",
            owner_id="worker",
            now=101.0,
        )
        on_time = backend.try_acquire(
            resource_key="hh.ru",
            policy=policy,
            operation_id="two",
            owner_id="worker",
            now=102.0,
        )

        self.assertIsNone(early.permit)
        self.assertEqual(early.retry_after_seconds, 1.0)
        self.assertIsNotNone(on_time.permit)

    def test_slot_table_does_not_grow_per_request(self) -> None:
        backend = SqliteResourceGateBackend(self.database_path)
        for index in range(25):
            decision = backend.try_acquire(
                resource_key="hh.ru",
                policy=self.policy,
                operation_id=f"operation-{index}",
                owner_id="worker",
                now=100.0 + index,
            )
            if decision.permit is None:
                self.fail("slot was not acquired")
            backend.release(decision.permit)

        with closing(sqlite3.connect(self.database_path)) as connection:
            state_count = connection.execute("SELECT COUNT(*) FROM resource_state").fetchone()[0]
            slot_count = connection.execute("SELECT COUNT(*) FROM resource_slots").fetchone()[0]

        self.assertEqual(state_count, 1)
        self.assertEqual(slot_count, 1)

    def test_backend_closes_every_short_lived_connection(self) -> None:
        connections: list[sqlite3.Connection] = []
        original_connect = sqlite3.connect

        def tracked_connect(database: Path, *, timeout: float) -> sqlite3.Connection:
            connection = original_connect(database, timeout=timeout)
            connections.append(connection)
            return connection

        with patch("job_harness.v2.runtime.resource_gate.sqlite3.connect", side_effect=tracked_connect):
            backend = SqliteResourceGateBackend(self.database_path)
            decision = backend.try_acquire(
                resource_key="hh.ru",
                policy=self.policy,
                operation_id="operation",
                owner_id="worker",
                now=100.0,
            )
            if decision.permit is None:
                self.fail("slot was not acquired")
            backend.release(decision.permit)

        self.assertEqual(len(connections), 3)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")


class ResourceGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancellation_cannot_cancel_a_queued_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SqliteResourceGateBackend(Path(directory) / "resource-gate.sqlite")
            gate = ResourceGate(backend=backend, owner_id="process-1")
            policy = ResourcePolicy(max_concurrency=1, min_interval_seconds=0.0, lease_seconds=10.0)
            acquired = gate.try_admit(
                "hh.ru",
                policy,
                OperationContext("active", execution_id="e-1", invocation_id="i-1"),
            )
            if acquired.permit is None:
                self.fail("initial permit was not acquired")

            blocker_started = threading.Event()
            blocker_proceed = threading.Event()
            executor = ThreadPoolExecutor(max_workers=1)
            asyncio.get_running_loop().set_default_executor(executor)

            def occupy_only_worker() -> None:
                blocker_started.set()
                blocker_proceed.wait(timeout=2.0)

            occupier = asyncio.create_task(asyncio.to_thread(occupy_only_worker))
            for _ in range(100):
                if blocker_started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(blocker_started.is_set())
            release = asyncio.create_task(gate.release_async(acquired.permit))
            await asyncio.sleep(0)
            release.cancel()
            await asyncio.sleep(0)
            release.cancel()
            blocker_proceed.set()

            with self.assertRaises(asyncio.CancelledError):
                await release
            await occupier

            replacement = gate.try_admit(
                "hh.ru",
                policy,
                OperationContext("replacement", execution_id="e-1", invocation_id="i-2"),
            )

            self.assertIsNotNone(replacement.permit)
            if replacement.permit is not None:
                gate.release(replacement.permit)

    async def test_repeated_cancellation_cannot_lose_a_late_permit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SqliteResourceGateBackend(Path(directory) / "resource-gate.sqlite")
            gate = ResourceGate(backend=backend, owner_id="process-1")
            policy = ResourcePolicy(max_concurrency=1, min_interval_seconds=0.0, lease_seconds=10.0)
            started = threading.Event()
            proceed = threading.Event()
            finished = threading.Event()
            original_try_acquire = backend.try_acquire

            def blocking_try_acquire(
                *,
                resource_key: str,
                policy: ResourcePolicy,
                operation_id: str,
                owner_id: str,
                now: float,
            ) -> AcquireDecision:
                started.set()
                proceed.wait(timeout=1.0)
                try:
                    return original_try_acquire(
                        resource_key=resource_key,
                        policy=policy,
                        operation_id=operation_id,
                        owner_id=owner_id,
                        now=now,
                    )
                finally:
                    finished.set()

            with patch.object(backend, "try_acquire", side_effect=blocking_try_acquire):
                admission = asyncio.create_task(
                    gate.admit(
                        "hh.ru",
                        policy,
                        OperationContext("cancelled", execution_id="e-1", invocation_id="i-1"),
                    )
                )
                self.assertTrue(await asyncio.to_thread(started.wait, 1.0))
                admission.cancel()
                await asyncio.sleep(0)
                admission.cancel()
                proceed.set()
                with self.assertRaises(asyncio.CancelledError):
                    await admission
                self.assertTrue(await asyncio.to_thread(finished.wait, 1.0))

            replacement = gate.try_admit(
                "hh.ru",
                policy,
                OperationContext("replacement", execution_id="e-1", invocation_id="i-2"),
            )

            self.assertIsNotNone(replacement.permit)
            if replacement.permit is not None:
                gate.release(replacement.permit)

    async def test_cancelled_admission_releases_permit_acquired_by_worker_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SqliteResourceGateBackend(Path(directory) / "resource-gate.sqlite")
            gate = ResourceGate(backend=backend, owner_id="process-1")
            policy = ResourcePolicy(max_concurrency=1, min_interval_seconds=0.0, lease_seconds=10.0)
            started = threading.Event()
            proceed = threading.Event()
            finished = threading.Event()
            original_try_acquire = backend.try_acquire

            def blocking_try_acquire(
                *,
                resource_key: str,
                policy: ResourcePolicy,
                operation_id: str,
                owner_id: str,
                now: float,
            ) -> AcquireDecision:
                started.set()
                proceed.wait(timeout=1.0)
                try:
                    return original_try_acquire(
                        resource_key=resource_key,
                        policy=policy,
                        operation_id=operation_id,
                        owner_id=owner_id,
                        now=now,
                    )
                finally:
                    finished.set()

            with patch.object(backend, "try_acquire", side_effect=blocking_try_acquire):
                admission = asyncio.create_task(
                    gate.admit(
                        "hh.ru",
                        policy,
                        OperationContext("cancelled", execution_id="e-1", invocation_id="i-1"),
                    )
                )
                self.assertTrue(await asyncio.to_thread(started.wait, 1.0))
                admission.cancel()
                proceed.set()
                with self.assertRaises(asyncio.CancelledError):
                    await admission
                self.assertTrue(await asyncio.to_thread(finished.wait, 1.0))

            replacement = gate.try_admit(
                "hh.ru",
                policy,
                OperationContext("replacement", execution_id="e-1", invocation_id="i-2"),
            )

            self.assertIsNotNone(replacement.permit)
            if replacement.permit is not None:
                gate.release(replacement.permit)

    async def test_direct_and_managed_contexts_use_the_same_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SqliteResourceGateBackend(Path(directory) / "resource-gate.sqlite")
            gate = ResourceGate(backend=backend, owner_id="process-1")
            policy = ResourcePolicy(max_concurrency=2, min_interval_seconds=0.0, lease_seconds=10.0)

            direct = await gate.admit(
                "hh.ru",
                policy,
                OperationContext("direct", execution_id=None, invocation_id=None),
            )
            managed = await gate.admit(
                "hh.ru",
                policy,
                OperationContext("managed", execution_id="e-1", invocation_id="i-1"),
            )

            self.assertNotEqual(direct.slot_number, managed.slot_number)
            gate.release(direct)
            gate.release(managed)

    async def test_busy_resource_returns_a_delay_without_sleeping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = SqliteResourceGateBackend(Path(directory) / "resource-gate.sqlite")
            gate = ResourceGate(backend=backend, owner_id="process-1")
            policy = ResourcePolicy(max_concurrency=1, min_interval_seconds=0.0, lease_seconds=10.0)
            acquired = await gate.admit(
                "hh.ru",
                policy,
                OperationContext("active", execution_id="e-1", invocation_id="i-1"),
            )

            decision = gate.try_admit(
                "hh.ru",
                policy,
                OperationContext("waiting", execution_id="e-1", invocation_id="i-2"),
            )

            self.assertIsNone(decision.permit)
            self.assertGreater(decision.retry_after_seconds, 0)
            gate.release(acquired)


if __name__ == "__main__":
    unittest.main()
