"""One in-flight operation per key, shared by every run of the service."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class TaskRegistry:
    """A second consumer of the same key awaits the first one's result instead of repeating the request.

    The shared task is not owned by any run: cancelling one run does not cancel work another run awaits.
    Finished keys are forgotten; their results live in the store and are reused from there.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def once(self, key: str, start: Callable[[], Awaitable[T]]) -> T:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(start())
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._forget(key, done))
        return await asyncio.shield(task)

    def _forget(self, key: str, task: asyncio.Task) -> None:
        self._tasks.pop(key, None)
        if not task.cancelled():
            task.exception()  # Consumers re-raise it; mark it retrieved if all of them were cancelled.

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
