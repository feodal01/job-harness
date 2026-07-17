"""Streaming worker pool for durable graph parser invocations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from job_harness.v2.contracts import LeasedParserInvocation
from job_harness.v2.persistence.graph_repository import SqliteGraphRepository
from job_harness.v2.runtime.executors import ManagedTaskRunner
from job_harness.v2.runtime.graph_coordinator import GraphCoordinator


@dataclass(frozen=True)
class GraphSearchProgress:
    tasks_completed: int
    events_processed: int
    elapsed_seconds: float
    done: bool = False


@dataclass(frozen=True)
class GraphSchedulerStats:
    tasks_completed: int
    events_processed: int
    started_at: float


@dataclass(frozen=True)
class GraphSchedulerDriver:
    execution_id: str
    runner: ManagedTaskRunner
    coordinator: GraphCoordinator
    optional: bool = False


class GraphTaskScheduler:
    def __init__(
        self,
        *,
        repository: SqliteGraphRepository,
        drivers: tuple[GraphSchedulerDriver, ...],
        concurrency: int,
        event_batch_size: int,
        lease_seconds: float,
        lease_heartbeat_seconds: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        progress_callback: Callable[[GraphSearchProgress], None] | None,
        progress_interval_seconds: float,
    ) -> None:
        if not drivers:
            raise ValueError("graph scheduler requires at least one execution driver")
        execution_ids = tuple(driver.execution_id for driver in drivers)
        if len(execution_ids) != len(set(execution_ids)):
            raise ValueError("graph scheduler execution drivers must be unique")
        self._repository = repository
        self._drivers = drivers
        self._concurrency = concurrency
        self._event_batch_size = event_batch_size
        self._lease_seconds = lease_seconds
        self._lease_heartbeat_seconds = lease_heartbeat_seconds
        self._clock = clock
        self._sleep = sleep
        self._progress_callback = progress_callback
        self._progress_interval_seconds = progress_interval_seconds

    async def run_until_quiescent(self) -> GraphSchedulerStats:
        execution_ids = tuple(driver.execution_id for driver in self._drivers)
        self._repository.begin_execution_sessions(
            execution_ids,
            now=self._clock(),
        )
        try:
            return await self._run_until_quiescent()
        finally:
            self._repository.end_execution_sessions(
                execution_ids,
                now=self._clock(),
            )
            self._repository.release_coordinator_leases(
                tuple(
                    (driver.execution_id, driver.coordinator.owner_id)
                    for driver in self._drivers
                )
            )

    async def _run_until_quiescent(self) -> GraphSchedulerStats:
        started_at = self._clock()
        last_progress_at = started_at
        tasks_completed = 0
        events_processed = 0
        deadline_reached: set[str] = set()
        pending: dict[
            asyncio.Task[None],
            tuple[LeasedParserInvocation, ManagedTaskRunner],
        ] = {}
        last_heartbeat_at = started_at

        while True:
            now = self._clock()
            await self._settle_deadlines(
                pending,
                deadline_reached=deadline_reached,
                now=now,
            )

            if now - last_heartbeat_at >= self._lease_heartbeat_seconds:
                self._repository.heartbeat_execution_sessions(
                    tuple(driver.execution_id for driver in self._drivers),
                    now=now,
                )
                if pending:
                    for driver in self._drivers:
                        leases = tuple(
                            (invocation.invocation_id, invocation.lease_token)
                            for invocation, task_runner in pending.values()
                            if task_runner is driver.runner
                        )
                        if leases:
                            self._repository.renew_invocation_leases(
                                owner_id=driver.runner.owner_id,
                                leases=leases,
                                lease_seconds=self._lease_seconds,
                                now=now,
                            )
                last_heartbeat_at = now

            processed = sum(
                driver.coordinator.process_once(
                    driver.execution_id,
                    limit=self._event_batch_size,
                    lease_seconds=self._lease_seconds,
                    now=now,
                )
                for driver in self._drivers
            )
            events_processed += processed
            self._fill_slots(
                pending,
                now=now,
                deadline_reached=deadline_reached,
            )

            if not pending:
                if processed == 0:
                    wakeup_at = self._next_wakeup_at(now)
                    if wakeup_at is None:
                        return GraphSchedulerStats(tasks_completed, events_processed, started_at)
                    await self._sleep(
                        max(
                            0.001,
                            min(
                                wakeup_at - self._clock(),
                                self._lease_heartbeat_seconds,
                            ),
                        )
                    )
                    continue
                last_progress_at = self._emit_progress(
                    tasks_completed,
                    events_processed,
                    started_at,
                    last_progress_at,
                )
                continue

            timeout = self._wait_timeout(
                now,
                last_progress_at,
                last_heartbeat_at,
                has_free_slot=len(pending) < self._concurrency,
            )
            done, _ = await asyncio.wait(
                pending.keys(),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                for task in done:
                    pending.pop(task, None)
                await asyncio.gather(*done)
                tasks_completed += len(done)
            last_progress_at = self._emit_progress(
                tasks_completed,
                events_processed,
                started_at,
                last_progress_at,
            )

    async def _settle_deadlines(
        self,
        pending: dict[
            asyncio.Task[None],
            tuple[LeasedParserInvocation, ManagedTaskRunner],
        ],
        *,
        deadline_reached: set[str],
        now: float,
    ) -> None:
        for driver in self._drivers:
            if driver.execution_id in deadline_reached:
                continue
            if not self._repository.settle_deadline(
                driver.execution_id,
                now=now,
                selection_evaluator=driver.coordinator.final_selection_evaluator,
            ):
                continue
            deadline_reached.add(driver.execution_id)
            await _cancel_pending_execution(
                pending,
                execution_id=driver.execution_id,
            )

    def _fill_slots(
        self,
        pending: dict[
            asyncio.Task[None],
            tuple[LeasedParserInvocation, ManagedTaskRunner],
        ],
        *,
        now: float,
        deadline_reached: set[str],
    ) -> None:
        available_slots = self._concurrency - len(pending)
        if available_slots < 1:
            return
        blocked_optional_resources: set[str] = set()
        for driver in self._drivers:
            if driver.execution_id in deadline_reached:
                continue
            invocations = driver.runner.lease_ready(
                driver.execution_id,
                limit=available_slots,
                now=now,
                excluded_resource_keys=(
                    tuple(sorted(blocked_optional_resources))
                    if driver.optional
                    else ()
                ),
            )
            if not driver.optional:
                blocked_optional_resources.update(
                    invocation.spec.resource_key
                    for invocation in invocations
                    if invocation.spec.resource_key is not None
                )
            for invocation in invocations:
                pending[self._task(driver.runner, invocation, now=now)] = (
                    invocation,
                    driver.runner,
                )
            available_slots -= len(invocations)
            if available_slots == 0:
                return

    @staticmethod
    def _task(
        runner: ManagedTaskRunner,
        invocation: LeasedParserInvocation,
        *,
        now: float,
    ) -> asyncio.Task[None]:
        return asyncio.create_task(runner.execute(invocation, now=now))

    def _next_wakeup_at(self, now: float) -> float | None:
        wakeups: list[float] = []
        for driver in self._drivers:
            wakeup = self._repository.next_scheduler_wakeup_at(
                driver.execution_id,
                now=now,
            )
            if wakeup is not None:
                wakeups.append(wakeup)
        return None if not wakeups else min(wakeups)

    def _wait_timeout(
        self,
        now: float,
        last_progress_at: float,
        last_heartbeat_at: float,
        *,
        has_free_slot: bool,
    ) -> float:
        heartbeat_wait = self._lease_heartbeat_seconds - (now - last_heartbeat_at)
        waits = [heartbeat_wait]
        if has_free_slot:
            wakeup_at = self._next_wakeup_at(now)
            if wakeup_at is not None:
                waits.append(wakeup_at - now)
        if self._progress_callback is not None and self._progress_interval_seconds > 0:
            waits.append(self._progress_interval_seconds - (now - last_progress_at))
        return max(0.001, min(waits))

    def _emit_progress(
        self,
        tasks_completed: int,
        events_processed: int,
        started_at: float,
        last_progress_at: float,
    ) -> float:
        now = self._clock()
        if self._progress_callback is None:
            return last_progress_at
        if self._progress_interval_seconds > 0 and now - last_progress_at < self._progress_interval_seconds:
            return last_progress_at
        self._progress_callback(
            GraphSearchProgress(
                tasks_completed=tasks_completed,
                events_processed=events_processed,
                elapsed_seconds=max(0.0, now - started_at),
            )
        )
        return now

async def _cancel_pending_execution(
    pending: dict[
        asyncio.Task[None],
        tuple[LeasedParserInvocation, ManagedTaskRunner],
    ],
    *,
    execution_id: str,
) -> None:
    selected = tuple(
        task
        for task, (invocation, _runner) in pending.items()
        if invocation.spec.execution_id == execution_id
    )
    for task in selected:
        task.cancel()
    if selected:
        await asyncio.gather(*selected, return_exceptions=True)
    for task in selected:
        pending.pop(task, None)
