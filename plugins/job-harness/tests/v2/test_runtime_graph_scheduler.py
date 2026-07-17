from __future__ import annotations

import asyncio
import time
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from job_harness.v2.contracts import LeasedParserInvocation
from job_harness.v2.runtime.graph_scheduler import GraphSchedulerDriver, GraphTaskScheduler


@dataclass(frozen=True)
class _Spec:
    resource_key: str | None = None


@dataclass(frozen=True)
class _Lease:
    invocation_id: str
    lease_token: str
    spec: _Spec = field(default_factory=_Spec)


class _Coordinator:
    final_selection_evaluator = None
    owner_id = "coordinator"

    def process_once(
        self,
        _execution_id: str,
        *,
        limit: int,
        lease_seconds: float,
        now: float,
    ) -> int:
        del limit, lease_seconds, now
        return 0


class _Repository:
    def __init__(self, *, wakeup_at: float | None = None) -> None:
        self.wakeup_at = wakeup_at
        self.heartbeats: list[tuple[tuple[tuple[str, str], ...], float]] = []

    def settle_deadline(self, _execution_id: str, *, now: float, selection_evaluator: object) -> bool:
        del now, selection_evaluator
        return False

    def next_scheduler_wakeup_at(
        self,
        _execution_id: str,
        *,
        now: float,
    ) -> float | None:
        del now
        return self.wakeup_at

    def begin_execution_sessions(
        self,
        execution_ids: tuple[str, ...],
        *,
        now: float,
    ) -> int:
        del now
        return len(execution_ids)

    def heartbeat_execution_sessions(
        self,
        execution_ids: tuple[str, ...],
        *,
        now: float,
    ) -> int:
        del now
        return len(execution_ids)

    def end_execution_sessions(
        self,
        execution_ids: tuple[str, ...],
        *,
        now: float,
    ) -> int:
        del now
        return len(execution_ids)

    def release_coordinator_leases(
        self,
        _owners: tuple[tuple[str, str], ...],
    ) -> int:
        return 0

    def renew_invocation_leases(
        self,
        *,
        owner_id: str,
        leases: tuple[tuple[str, str], ...],
        lease_seconds: float,
        now: float,
    ) -> int:
        del owner_id
        self.heartbeats.append((leases, now + lease_seconds))
        return len(leases)


class _Runner:
    owner_id = "worker"

    def __init__(
        self,
        *,
        ready_at: float,
        clock: Callable[[], float],
        task_seconds: float = 0.0,
    ) -> None:
        self.ready_at = ready_at
        self.clock = clock
        self.task_seconds = task_seconds
        self.leased = False
        self.executed = 0

    def lease_ready(
        self,
        _execution_id: str,
        *,
        limit: int,
        now: float,
        excluded_resource_keys: tuple[str, ...] = (),
    ) -> tuple[LeasedParserInvocation, ...]:
        del excluded_resource_keys
        if self.leased or limit < 1 or now < self.ready_at:
            return ()
        self.leased = True
        return (cast(LeasedParserInvocation, _Lease("page-2", "token-2")),)

    async def execute(self, _invocation: LeasedParserInvocation, *, now: float) -> None:
        del now
        if self.task_seconds:
            await asyncio.sleep(self.task_seconds)
        self.executed += 1


class _TwoStageRunner:
    owner_id = "worker"

    def __init__(
        self,
        *,
        future_ready_at: float,
        repository: _Repository,
    ) -> None:
        self.future_ready_at = future_ready_at
        self.repository = repository
        self.slow_leased = False
        self.future_leased = False
        self.slow_release = asyncio.Event()
        self.future_started = asyncio.Event()

    def lease_ready(
        self,
        _execution_id: str,
        *,
        limit: int,
        now: float,
        excluded_resource_keys: tuple[str, ...] = (),
    ) -> tuple[LeasedParserInvocation, ...]:
        del excluded_resource_keys
        if limit < 1:
            return ()
        if not self.slow_leased:
            self.slow_leased = True
            return (cast(LeasedParserInvocation, _Lease("slow", "slow-token")),)
        if not self.future_leased and now >= self.future_ready_at:
            self.future_leased = True
            self.repository.wakeup_at = None
            return (cast(LeasedParserInvocation, _Lease("future", "future-token")),)
        return ()

    async def execute(self, invocation: LeasedParserInvocation, *, now: float) -> None:
        del now
        if invocation.invocation_id == "slow":
            await self.slow_release.wait()
            return
        self.future_started.set()
        self.slow_release.set()


class _PriorityRunner:
    owner_id = "worker"

    def __init__(self, name: str, resource_key: str, starts: list[str]) -> None:
        self.name = name
        self.resource_key = resource_key
        self.starts = starts
        self.leased = False
        self.exclusions: list[tuple[str, ...]] = []

    def lease_ready(
        self,
        _execution_id: str,
        *,
        limit: int,
        now: float,
        excluded_resource_keys: tuple[str, ...] = (),
    ) -> tuple[LeasedParserInvocation, ...]:
        del now
        self.exclusions.append(excluded_resource_keys)
        if self.leased or limit < 1 or self.resource_key in excluded_resource_keys:
            return ()
        self.leased = True
        return (
            cast(
                LeasedParserInvocation,
                _Lease(
                    self.name,
                    f"{self.name}-token",
                    _Spec(self.resource_key),
                ),
            ),
        )

    async def execute(self, _invocation: LeasedParserInvocation, *, now: float) -> None:
        del now
        self.starts.append(self.name)


class GraphTaskSchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_resource_is_leased_before_optional_resource(self) -> None:
        repository = _Repository()
        starts: list[str] = []
        search = _PriorityRunner("search", "hh", starts)
        optional = _PriorityRunner("optional", "hh", starts)
        scheduler = GraphTaskScheduler(
            repository=repository,  # type: ignore[arg-type]
            drivers=(
                GraphSchedulerDriver(
                    "search-execution",
                    search,  # type: ignore[arg-type]
                    _Coordinator(),  # type: ignore[arg-type]
                ),
                GraphSchedulerDriver(
                    "enrichment-execution",
                    optional,  # type: ignore[arg-type]
                    _Coordinator(),  # type: ignore[arg-type]
                    optional=True,
                ),
            ),
            concurrency=2,
            event_batch_size=10,
            lease_seconds=30.0,
            lease_heartbeat_seconds=10.0,
            clock=time.monotonic,
            progress_callback=None,
            progress_interval_seconds=0.0,
        )

        await scheduler.run_until_quiescent()

        self.assertEqual(starts, ["search", "optional"])
        self.assertIn(("hh",), optional.exclusions)

    async def test_future_ready_task_refills_free_slot_without_waiting_for_heartbeat(self) -> None:
        started_at = time.monotonic()
        future_ready_at = started_at + 0.01
        repository = _Repository(wakeup_at=future_ready_at)
        runner = _TwoStageRunner(
            future_ready_at=future_ready_at,
            repository=repository,
        )
        scheduler = GraphTaskScheduler(
            repository=repository,  # type: ignore[arg-type]
            drivers=(
                GraphSchedulerDriver(
                    "execution",
                    runner,  # type: ignore[arg-type]
                    _Coordinator(),  # type: ignore[arg-type]
                ),
            ),
            concurrency=2,
            event_batch_size=10,
            lease_seconds=0.2,
            lease_heartbeat_seconds=0.08,
            clock=time.monotonic,
            progress_callback=None,
            progress_interval_seconds=0.0,
        )

        run = asyncio.create_task(scheduler.run_until_quiescent())
        try:
            await asyncio.wait_for(runner.future_started.wait(), timeout=0.05)
        finally:
            runner.slow_release.set()
            await run

        self.assertTrue(runner.future_leased)

    async def test_waits_for_future_retry_instead_of_declaring_quiescence(self) -> None:
        current = [100.0]
        wakeup_at = 105.0
        repository = _Repository(wakeup_at=105.0)
        runner = _Runner(ready_at=105.0, clock=lambda: current[0])

        async def advance(seconds: float) -> None:
            current[0] += seconds
            repository.wakeup_at = None if current[0] >= wakeup_at else wakeup_at

        scheduler = GraphTaskScheduler(
            repository=repository,  # type: ignore[arg-type]
            drivers=(
                GraphSchedulerDriver(
                    "execution",
                    runner,  # type: ignore[arg-type]
                    _Coordinator(),  # type: ignore[arg-type]
                ),
            ),
            concurrency=1,
            event_batch_size=10,
            lease_seconds=30.0,
            lease_heartbeat_seconds=10.0,
            clock=lambda: current[0],
            sleep=advance,
            progress_callback=None,
            progress_interval_seconds=0.0,
        )

        stats = await scheduler.run_until_quiescent()

        self.assertEqual(runner.executed, 1)
        self.assertEqual(stats.tasks_completed, 1)
        self.assertEqual(current[0], 105.0)

    async def test_heartbeats_active_leases_in_one_batch(self) -> None:
        repository = _Repository()
        runner = _Runner(ready_at=0.0, clock=time.monotonic, task_seconds=0.04)
        scheduler = GraphTaskScheduler(
            repository=repository,  # type: ignore[arg-type]
            drivers=(
                GraphSchedulerDriver(
                    "execution",
                    runner,  # type: ignore[arg-type]
                    _Coordinator(),  # type: ignore[arg-type]
                ),
            ),
            concurrency=1,
            event_batch_size=10,
            lease_seconds=0.09,
            lease_heartbeat_seconds=0.01,
            clock=time.monotonic,
            progress_callback=None,
            progress_interval_seconds=0.0,
        )

        await scheduler.run_until_quiescent()

        self.assertGreaterEqual(len(repository.heartbeats), 2)
        self.assertTrue(
            all(batch == (("page-2", "token-2"),) for batch, _ in repository.heartbeats)
        )


if __name__ == "__main__":
    unittest.main()
