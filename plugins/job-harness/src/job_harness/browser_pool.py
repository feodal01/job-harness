"""Async BrowserPool over Playwright async_api.

Design notes (verified empirically in plans/resilient-scraping.md §4):

* `sync_playwright` is greenlet-based and single-threaded; we use async
  Playwright so multiple contexts can run concurrently in one event
  loop.

* Cancellation: `task.cancel()` on a coroutine awaiting `page.goto(...)`
  raises `asyncio.CancelledError` inside Playwright instantly; the page
  closes cleanly and the context is reusable. We rely on this native
  asyncio cancellation rather than a separate CancelToken.

* Hard timeout: `asyncio.wait_for(func(page), timeout=...)` enforces
  the per-call deadline; on timeout, `CancelledError` propagates into
  `goto` and the page is closed in `finally`.

* Pool poison protection: after `recycle_after_consecutive_hangs`
  consecutive `asyncio.TimeoutError`s, the pool tears down and rebuilds
  the entire Browser. In-flight callers blocked on the semaphore wait
  for the rebuild and then acquire a fresh context.

* Anti-bot / captcha / login detection runs after every callable
  completes (see `is_blocked`). The probe is cheap (one title + one
  URL + a handful of locator counts).

The factory `browser_factory` is injectable for tests; production
callers use `default_browser_factory` which launches a real Chromium.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from job_harness.types import BlockReason

# ---------------------------------------------------------------------------
# Block detection
# ---------------------------------------------------------------------------


# Title regexes for anti-bot and captcha interstitials.
_ANTI_BOT_TITLE_RE = re.compile(
    r"(?i)("
    r"доступ ограничен|"
    r"just a moment|"
    r"verify you are human|"
    r"подтвердите.+человек|"
    r"attention required|"
    r"checking your browser|"
    r"access denied"
    r")"
)
_CAPTCHA_TITLE_RE = re.compile(
    r"(?i)(recaptcha|hcaptcha|captcha|prove you are human)"
)
_CAPTCHA_IFRAME_SELECTORS: tuple[str, ...] = (
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="cf-chl"]',
    'iframe[src*="distil"]',
)
_LOGIN_PATH_RE = re.compile(r"^/(?:login|auth|sign[-_]?in|users/sign_in)(?:/|$)")
_ANTI_BOT_PATH_RE = re.compile(
    r"^/(?:vpncheck|vpncheeck|captcha|blocked|access-denied)(?:/|$)"
)
_ANTI_BOT_BODY_RE = re.compile(
    r"(?is)("
    r"403 forbidden|"
    r"you have been blocked|"
    r"verify you are human|"
    r"checking your browser|"
    r"cf-chl|"
    r"__cf_chl_"
    r")"
)
_CAPTCHA_BODY_RE = re.compile(
    r"(?is)(prove you are human|complete the captcha|captcha challenge)"
)


@dataclass(frozen=True)
class BlockSignal:
    """Result of a `is_blocked` probe."""

    reason: BlockReason
    signal: str


async def is_blocked(page: Any) -> BlockSignal | None:
    """Cheap probe to detect anti-bot, captcha, or login redirect pages.

    Returns None if the page looks normal. The probe MUST be fast
    (<50 ms) so it can run on every navigation without slowing the
    happy path.
    """
    try:
        title = await page.title()
    except Exception:
        title = ""

    # Match title against anti-bot and captcha regexes.
    if title:
        if m := _ANTI_BOT_TITLE_RE.search(title):
            return BlockSignal(reason=BlockReason.ANTI_BOT_PAGE, signal=m.group(0))
        if m := _CAPTCHA_TITLE_RE.search(title):
            return BlockSignal(reason=BlockReason.CAPTCHA_PAGE, signal=m.group(0))

    # Captcha iframe selectors.
    for selector in _CAPTCHA_IFRAME_SELECTORS:
        try:
            if await page.locator(selector).count() > 0:
                return BlockSignal(reason=BlockReason.CAPTCHA_PAGE, signal=selector)
        except Exception:
            continue

    # Login redirect detection.
    try:
        url = page.url
    except Exception:
        url = ""
    if url:
        parsed = urlparse(url)
        if parsed.path and _LOGIN_PATH_RE.match(parsed.path):
            return BlockSignal(reason=BlockReason.LOGIN_REDIRECT, signal=url)
        if parsed.path and _ANTI_BOT_PATH_RE.match(parsed.path):
            return BlockSignal(reason=BlockReason.ANTI_BOT_PAGE, signal=url)

    try:
        content = await page.content()
    except Exception:
        content = ""
    if content:
        if m := _CAPTCHA_BODY_RE.search(content):
            return BlockSignal(reason=BlockReason.CAPTCHA_PAGE, signal=m.group(0))
        if m := _ANTI_BOT_BODY_RE.search(content):
            return BlockSignal(reason=BlockReason.ANTI_BOT_PAGE, signal=m.group(0))
    return None


# ---------------------------------------------------------------------------
# Result wrappers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlockedResult[T]:
    """Wraps a `run_with_page` outcome when the page tripped `is_blocked`.

    The pool returns this in place of the user callable's value so the
    engine can mark the source as BLOCKED with the right failure mode.
    """

    block: BlockSignal
    partial: T | None = None


class BrowserBlocked(Exception):
    """Raised by browser scrapers when a navigation response is blocked."""

    def __init__(self, block: BlockSignal) -> None:
        self.block = block
        super().__init__(f"{block.reason.value}: {block.signal}")


def raise_for_blocked_response(response: Any) -> None:
    """Turn blocked browser navigation responses into a pool block result."""
    status = _response_status(response)
    if status is None:
        return
    if status in (403, 451):
        raise BrowserBlocked(
            BlockSignal(
                reason=BlockReason.ANTI_BOT_PAGE,
                signal=f"HTTP {status}",
            )
        )


# ---------------------------------------------------------------------------
# Health snapshot for tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolHealth:
    max_contexts: int
    contexts_available: int
    contexts_in_use: int
    browser_rebuilds: int
    consecutive_hangs: int
    browser_connected: bool


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PoolAcquireTimeout(Exception):
    """Raised when no context slot is available before the acquire deadline."""


class PoolShutdown(RuntimeError):
    """Raised when browser pool acquisition loses a race with shutdown."""


class BrowserDisconnected(Exception):
    """Raised when `browser.is_connected()` returns False at acquire time
    and the pool has been torn down for rebuild while callers were
    waiting. The runner translates this to FailureMode.BROWSER_DISCONNECTED.
    """


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


@dataclass
class _PoolState:
    """Mutable internal state kept inside the pool's lock."""

    contexts: list[Any] = field(default_factory=list)         # idle contexts
    in_use: int = 0
    browser: Any = None
    consecutive_hangs: int = 0
    browser_rebuilds: int = 0
    shutting_down: bool = False


class BrowserPool:
    """Async pool of Playwright `BrowserContext`s, shared by source coroutines.

    `run_with_page(func, timeout_ms)` is the only entry point. Internally:

      1. Wait for a free context slot within the per-call deadline. An
         explicit `acquire_timeout_ms` can cap this wait sooner.
      2. Reuse an idle context, or create the browser/context within
         the same per-call deadline.
      3. Open a page and set its default timeout to the remaining budget.
      4. Run `func(page)` within the remaining budget.
      5. Run `is_blocked(page)` afterwards with remaining budget. If it
         times out after `func` returned, keep the successful value. If
         a block is detected, replace the value with `BlockedResult`.
      6. Close the page. Return the context to the pool — unless the
         page close itself failed, in which case discard the context
         and let the next acquire build a fresh one.

    Multiple concurrent `run_with_page` callers all share the same
    Browser instance; the `Semaphore(max_contexts)` caps real parallel
    page usage.
    """

    def __init__(
        self,
        *,
        max_contexts: int = 2,
        page_timeout_ms: int = 30_000,
        acquire_timeout_ms: int | None = None,
        recycle_after_consecutive_hangs: int = 2,
        browser_factory: Callable[[], Awaitable[Any]] | None = None,
        context_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if max_contexts < 1:
            raise ValueError("max_contexts must be >= 1")
        if page_timeout_ms < 1:
            raise ValueError("page_timeout_ms must be >= 1")
        if acquire_timeout_ms is not None and acquire_timeout_ms < 1:
            raise ValueError("acquire_timeout_ms must be >= 1 when provided")
        self._max_contexts = max_contexts
        self._page_timeout_ms = page_timeout_ms
        self._acquire_timeout_ms = acquire_timeout_ms
        self._recycle_threshold = max(1, recycle_after_consecutive_hangs)
        self._browser_factory = browser_factory or _default_browser_factory
        self._context_kwargs = context_kwargs or {"accept_downloads": False}
        self._cleanup_timeout_s = 3.0
        self._sem = asyncio.Semaphore(max_contexts)
        self._lock = asyncio.Lock()
        self._rebuild_lock = asyncio.Lock()
        self._cleanup_tasks: set[asyncio.Task[bool]] = set()
        self._state = _PoolState()

    # --- properties ------------------------------------------------------

    @property
    def max_contexts(self) -> int:
        return self._max_contexts

    # --- public API -----------------------------------------------------

    async def run_with_page[T](
        self,
        func: Callable[[Any], Awaitable[T]],
        *,
        timeout_ms: int | None = None,
    ) -> T | BlockedResult[T]:
        """Run `func(page)` with a hard timeout and the anti-bot probe."""
        timeout_budget_ms = timeout_ms if timeout_ms is not None else self._page_timeout_ms
        deadline = monotonic() + (timeout_budget_ms / 1000.0)

        acquired = False
        checked_out = False
        context = None
        page = None
        poisoned = False
        try:
            await self._acquire_slot(deadline)
            acquired = True
        except TimeoutError as exc:
            raise PoolAcquireTimeout(self._acquire_timeout_message(deadline)) from exc

        try:
            try:
                context = await self._wait_for_deadline(
                    self._acquire_context(deadline),
                    deadline=deadline,
                )
                checked_out = True

                page = await self._wait_for_deadline(
                    context.new_page(),
                    deadline=deadline,
                )
                try:
                    page.set_default_timeout(self._page_timeout_for_remaining(deadline))
                except Exception:
                    pass

                try:
                    value = await self._wait_for_deadline(func(page), deadline=deadline)
                except BrowserBlocked as exc:
                    async with self._lock:
                        self._state.consecutive_hangs = 0
                    return BlockedResult(block=exc.block)

                async with self._lock:
                    self._state.consecutive_hangs = 0

                remaining = self._remaining_timeout_s(deadline, raise_expired=False)
                if remaining <= 0:
                    return value
                try:
                    block = await asyncio.wait_for(is_blocked(page), timeout=remaining)
                except TimeoutError:
                    return value
                if block is not None:
                    return BlockedResult(block=block, partial=value)
                return value
            except TimeoutError:
                async with self._lock:
                    self._state.consecutive_hangs += 1
                if checked_out and page is None:
                    poisoned = True
                raise
            except Exception:
                if checked_out and page is None:
                    poisoned = True
                raise
        finally:
            cleanup_error: BaseException | None = None
            if page is not None:
                try:
                    closed = await self._close_batch((page,))
                    poisoned = poisoned or not closed
                except BaseException as exc:
                    poisoned = True
                    cleanup_error = exc
            try:
                if checked_out:
                    await self._release_context(context, poisoned=poisoned)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            finally:
                if acquired:
                    self._sem.release()
            if cleanup_error is not None:
                raise cleanup_error

    async def _acquire_slot(self, deadline: float) -> None:
        remaining = self._remaining_timeout_s(deadline)
        timeout = remaining
        if self._acquire_timeout_ms is not None:
            timeout = min(timeout, self._acquire_timeout_ms / 1000.0)
        await asyncio.wait_for(self._sem.acquire(), timeout=timeout)

    def _acquire_timeout_message(self, deadline: float) -> str:
        remaining = self._remaining_timeout_s(deadline, raise_expired=False)
        if (
            self._acquire_timeout_ms is not None
            and remaining > (self._acquire_timeout_ms / 1000.0)
        ):
            return f"could not acquire a context within {self._acquire_timeout_ms} ms"
        return "could not acquire a context before the source deadline"

    def _remaining_timeout_s(self, deadline: float, *, raise_expired: bool = True) -> float:
        remaining = deadline - monotonic()
        if remaining <= 0 and raise_expired:
            raise TimeoutError("browser pool call deadline exceeded")
        return max(0.0, remaining)

    async def _wait_for_deadline[T](self, awaitable: Awaitable[T], *, deadline: float) -> T:
        return await asyncio.wait_for(awaitable, timeout=self._remaining_timeout_s(deadline))

    def _page_timeout_for_remaining(self, deadline: float) -> int:
        remaining = self._remaining_timeout_s(deadline)
        return max(1, min(self._page_timeout_ms, int(remaining * 1000)))

    async def _close_batch(self, closeables: tuple[Any, ...]) -> bool:
        items = tuple(item for item in closeables if item is not None)
        if not items:
            return True

        async def close_one(item: Any) -> None:
            await item.close()

        tasks = [asyncio.create_task(close_one(item)) for item in items]
        try:
            done, pending = await asyncio.wait(tasks, timeout=self._cleanup_timeout_s)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        ok = not pending
        for task in done:
            if task.cancelled():
                ok = False
                continue
            exc = task.exception()
            if exc is not None:
                ok = False
        return ok

    def _track_cleanup(self, *closeables: Any) -> None:
        items = tuple(item for item in closeables if item is not None)
        if not items:
            return
        task = asyncio.create_task(self._close_batch(items))
        self._cleanup_tasks.add(task)

        def done_callback(done_task: asyncio.Task[bool]) -> None:
            self._cleanup_tasks.discard(done_task)
            try:
                done_task.result()
            except BaseException:
                pass

        task.add_done_callback(done_callback)

    async def _drain_cleanup_tasks(self, tasks: set[asyncio.Task[bool]]) -> None:
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=self._cleanup_timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except BaseException:
                pass

    async def health(self) -> PoolHealth:
        async with self._lock:
            return PoolHealth(
                max_contexts=self._max_contexts,
                contexts_available=len(self._state.contexts),
                contexts_in_use=self._state.in_use,
                browser_rebuilds=self._state.browser_rebuilds,
                consecutive_hangs=self._state.consecutive_hangs,
                browser_connected=self._is_connected_locked(),
            )

    async def shutdown(self) -> None:
        async with self._lock:
            self._state.shutting_down = True
            browser = self._state.browser
            contexts = tuple(self._state.contexts)
            self._state.browser = None
            self._state.contexts.clear()

        async with self._rebuild_lock:
            pass

        async with self._lock:
            cleanup_tasks = set(self._cleanup_tasks)
        await self._close_batch((*contexts, browser))
        await self._drain_cleanup_tasks(cleanup_tasks)
        async with self._lock:
            self._cleanup_tasks.clear()

    # --- internal --------------------------------------------------------

    async def _acquire_context(self, deadline: float) -> Any:
        """Return an accepted context, creating/rebuilding browser as needed."""
        while True:
            async with self._lock:
                if self._state.shutting_down:
                    raise PoolShutdown("browser pool is shutting down")
                needs_rebuild = self._needs_rebuild_locked()
                if not needs_rebuild:
                    if self._state.contexts:
                        ctx = self._state.contexts.pop()
                        self._state.in_use += 1
                        return ctx
                    browser = self._state.browser
                    assert browser is not None
                else:
                    browser = None

            if needs_rebuild:
                await self._ensure_browser(deadline)
                continue

            ctx = await self._wait_for_deadline(
                browser.new_context(**self._context_kwargs),
                deadline=deadline,
            )
            async with self._lock:
                if self._state.shutting_down:
                    self._track_cleanup(ctx)
                    raise PoolShutdown("browser pool is shutting down")
                if self._state.browser is not browser:
                    self._track_cleanup(ctx)
                    continue
                self._state.in_use += 1
                return ctx

    async def _ensure_browser(self, deadline: float) -> None:
        async with self._rebuild_lock:
            async with self._lock:
                if self._state.shutting_down:
                    raise PoolShutdown("browser pool is shutting down")
                if not self._needs_rebuild_locked():
                    return
                old_contexts = tuple(self._state.contexts)
                old_browser = self._state.browser
                self._state.contexts.clear()
                self._state.browser = None
                if old_browser is not None:
                    self._state.browser_rebuilds += 1

            self._track_cleanup(*old_contexts, old_browser)

            new_browser = await self._wait_for_deadline(
                self._browser_factory(),
                deadline=deadline,
            )
            committed = False
            cleanup_scheduled = False
            try:
                async with self._lock:
                    if self._state.shutting_down:
                        self._track_cleanup(new_browser)
                        cleanup_scheduled = True
                        raise PoolShutdown("browser pool is shutting down")
                    self._state.browser = new_browser
                    self._state.consecutive_hangs = 0
                    committed = True
            finally:
                if not committed and not cleanup_scheduled:
                    self._track_cleanup(new_browser)

    def _needs_rebuild_locked(self) -> bool:
        return (
            self._state.browser is None
            or not self._is_connected_locked()
            or self._state.consecutive_hangs >= self._recycle_threshold
        )

    async def _release_context(self, context: Any, *, poisoned: bool) -> None:
        close_context = None
        async with self._lock:
            if self._state.in_use <= 0:
                raise RuntimeError("browser pool context release underflow")
            self._state.in_use -= 1
            if poisoned or self._state.shutting_down:
                close_context = context
            else:
                self._state.contexts.append(context)
        if close_context is not None:
            await self._close_batch((close_context,))

    def _is_connected_locked(self) -> bool:
        browser = self._state.browser
        if browser is None:
            return False
        try:
            return bool(browser.is_connected())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Default factory — real Playwright Chromium
# ---------------------------------------------------------------------------


async def _default_browser_factory() -> Any:
    """Production browser factory. Imports rebrowser-playwright lazily.

    Tests never reach this path because they pass their own
    `browser_factory` constructor argument.
    """
    from rebrowser_playwright.async_api import async_playwright

    from job_harness.browser import configure_playwright_tmpdir, create_browser_async

    configure_playwright_tmpdir()
    pw = await async_playwright().start()
    browser, _ctx = await create_browser_async(pw, headless=True)
    # The browser is what the pool needs; the throwaway ctx is discarded
    # so the pool's own context lifecycle stays consistent.
    return browser


def _response_status(response: Any) -> int | None:
    if response is None:
        return None
    status = getattr(response, "status", None)
    if callable(status):
        try:
            status = status()
        except Exception:
            return None
    return status if isinstance(status, int) else None
