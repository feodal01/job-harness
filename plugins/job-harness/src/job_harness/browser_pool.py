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
    """Raised by `run_with_page` when `acquire_timeout_ms` is exceeded."""


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

      1. Wait up to `acquire_timeout_ms` for a free context slot.
      2. Reuse an idle context, or call `browser.new_context(...)` if
         we are below `max_contexts` and the slot is fresh.
      3. Open a page, set its default timeout to `page_timeout_ms`.
      4. Run `await asyncio.wait_for(func(page), timeout_ms/1000)`.
      5. Run `is_blocked(page)` afterwards. If a block is detected,
         replace the value with `BlockedResult`.
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
        acquire_timeout_ms: int = 5_000,
        recycle_after_consecutive_hangs: int = 2,
        browser_factory: Callable[[], Awaitable[Any]] | None = None,
        context_kwargs: dict[str, Any] | None = None,
    ) -> None:
        if max_contexts < 1:
            raise ValueError("max_contexts must be >= 1")
        self._max_contexts = max_contexts
        self._page_timeout_ms = page_timeout_ms
        self._acquire_timeout_ms = acquire_timeout_ms
        self._recycle_threshold = max(1, recycle_after_consecutive_hangs)
        self._browser_factory = browser_factory or _default_browser_factory
        self._context_kwargs = context_kwargs or {"accept_downloads": False}
        self._sem = asyncio.Semaphore(max_contexts)
        self._lock = asyncio.Lock()
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
        timeout_s = (timeout_ms if timeout_ms is not None else self._page_timeout_ms) / 1000.0

        # Acquire the semaphore with the acquire deadline.
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self._acquire_timeout_ms / 1000.0)
        except TimeoutError as exc:
            raise PoolAcquireTimeout(
                f"could not acquire a context within {self._acquire_timeout_ms} ms"
            ) from exc

        context = None
        page = None
        try:
            context = await self._acquire_context_locked()
            page = await context.new_page()
            try:
                page.set_default_timeout(self._page_timeout_ms)
            except Exception:
                pass

            try:
                value = await asyncio.wait_for(func(page), timeout=timeout_s)
            except BrowserBlocked as exc:
                async with self._lock:
                    self._state.consecutive_hangs = 0
                return BlockedResult(block=exc.block)
            except TimeoutError:
                async with self._lock:
                    self._state.consecutive_hangs += 1
                raise

            # Reset hang streak on a successful return.
            async with self._lock:
                self._state.consecutive_hangs = 0

            block = await is_blocked(page)
            if block is not None:
                return BlockedResult(block=block, partial=value)
            return value
        finally:
            # Close page; on failure mark context as poisoned.
            poisoned = False
            if page is not None:
                try:
                    await asyncio.wait_for(page.close(), timeout=3.0)
                except Exception:
                    poisoned = True
            await self._release_context_locked(context, poisoned=poisoned)
            self._sem.release()
            # If too many consecutive hangs, rebuild the browser. The
            # rebuild happens lazily on the next acquire — see
            # `_acquire_context_locked`.

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
            self._state.browser = None
            for ctx in self._state.contexts:
                try:
                    await ctx.close()
                except Exception:
                    pass
            self._state.contexts.clear()
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    # --- internal --------------------------------------------------------

    async def _acquire_context_locked(self) -> Any:
        """Return a usable context. Lazily creates browser/context as needed.

        Rebuilds the browser if:
          • is_connected() is false
          • consecutive_hangs >= recycle_after_consecutive_hangs
        """
        async with self._lock:
            if self._state.shutting_down:
                raise RuntimeError("pool is shutting down")

            needs_rebuild = (
                self._state.browser is None
                or not self._is_connected_locked()
                or self._state.consecutive_hangs >= self._recycle_threshold
            )
            if needs_rebuild:
                await self._rebuild_browser_locked()

            # Reuse an idle context if available.
            if self._state.contexts:
                ctx = self._state.contexts.pop()
                self._state.in_use += 1
                return ctx

            # Create a new context.
            assert self._state.browser is not None
            ctx = await self._state.browser.new_context(**self._context_kwargs)
            self._state.in_use += 1
            return ctx

    async def _release_context_locked(self, context: Any, *, poisoned: bool) -> None:
        async with self._lock:
            if context is None:
                self._state.in_use = max(0, self._state.in_use - 1)
                return
            self._state.in_use = max(0, self._state.in_use - 1)
            if poisoned or self._state.shutting_down:
                try:
                    await context.close()
                except Exception:
                    pass
                return
            self._state.contexts.append(context)

    async def _rebuild_browser_locked(self) -> None:
        """Tear down and recreate the Browser. Caller must hold the lock."""
        for ctx in self._state.contexts:
            try:
                await ctx.close()
            except Exception:
                pass
        self._state.contexts.clear()
        if self._state.browser is not None:
            try:
                await self._state.browser.close()
            except Exception:
                pass
            self._state.browser = None
            self._state.browser_rebuilds += 1
        # Build a fresh browser.
        self._state.browser = await self._browser_factory()
        self._state.consecutive_hangs = 0

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
