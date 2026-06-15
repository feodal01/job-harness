"""Tests for the async BrowserPool.

All tests use a fake browser fixture — no real Chromium is launched.
The pool's contract is verified independently of Playwright internals,
which were empirically validated separately (plan §4).
"""

from __future__ import annotations

import asyncio
import time
import unittest

from tests._support.fake_browser import (
    FakeBrowser,
    FakeContext,
    FakePage,
    PageBehaviour,
)

import job_harness.browser_pool as browser_pool_module
from job_harness.browser_pool import (
    BlockedResult,
    BlockSignal,
    BrowserBlocked,
    BrowserPool,
    PoolAcquireTimeout,
    PoolShutdown,
    is_blocked,
)
from job_harness.types import BlockReason


def _factory(browser: FakeBrowser):
    """Returns a coroutine factory yielding the given browser instance."""

    async def factory():
        return browser

    return factory


class LazyCreationTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_run_with_page_creates_browser_and_context(self):
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))

        async def use(page):
            return "ok"

        result = await pool.run_with_page(use, timeout_ms=1000)
        self.assertEqual(result, "ok")
        self.assertEqual(browser.new_context_calls, 1)
        await pool.shutdown()

    async def test_context_is_reused_across_calls(self):
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))

        async def use(page):
            return None

        await pool.run_with_page(use, timeout_ms=1000)
        await pool.run_with_page(use, timeout_ms=1000)
        # Only one context created across the two calls.
        self.assertEqual(browser.new_context_calls, 1)
        await pool.shutdown()


class TimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_raises_within_slack(self):
        browser = FakeBrowser()
        pool = BrowserPool(
            max_contexts=2,
            browser_factory=_factory(browser),
            page_timeout_ms=30_000,
        )

        async def hang(page):
            await asyncio.sleep(10)
            return "never"

        t0 = time.monotonic()
        with self.assertRaises(asyncio.TimeoutError):
            await pool.run_with_page(hang, timeout_ms=200)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0, f"timeout took {elapsed:.2f}s, expected <1s")
        await pool.shutdown()

    async def test_next_call_succeeds_after_timeout(self):
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))

        async def hang(page):
            await asyncio.sleep(10)

        async def quick(page):
            return "hello"

        with self.assertRaises(asyncio.TimeoutError):
            await pool.run_with_page(hang, timeout_ms=150)
        # Pool must remain usable for subsequent callers.
        out = await pool.run_with_page(quick, timeout_ms=1000)
        self.assertEqual(out, "hello")
        await pool.shutdown()


class RecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_consecutive_hangs_trigger_browser_rebuild(self):
        # Build a sequence of fresh browsers so we can count rebuilds.
        browsers: list[FakeBrowser] = []

        async def factory():
            b = FakeBrowser()
            browsers.append(b)
            return b

        pool = BrowserPool(
            max_contexts=2,
            browser_factory=factory,
            recycle_after_consecutive_hangs=2,
        )

        async def hang(page):
            await asyncio.sleep(10)

        # Two consecutive hangs.
        for _ in range(2):
            with self.assertRaises(asyncio.TimeoutError):
                await pool.run_with_page(hang, timeout_ms=80)

        # A third call should trigger a rebuild before running.
        async def quick(page):
            return "rebuilt"

        out = await pool.run_with_page(quick, timeout_ms=1000)
        self.assertEqual(out, "rebuilt")
        self.assertEqual(len(browsers), 2, "browser should have been rebuilt once")
        health = await pool.health()
        self.assertEqual(health.browser_rebuilds, 1)
        # consecutive_hangs reset by both rebuild and the successful run.
        self.assertEqual(health.consecutive_hangs, 0)
        await pool.shutdown()


class CancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_propagates_into_page_func(self):
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))

        started = asyncio.Event()
        finished_or_cancelled = asyncio.Event()

        async def slow(page):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                finished_or_cancelled.set()
            return "should-not-return"

        task = asyncio.create_task(pool.run_with_page(slow, timeout_ms=10_000))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # The page's finally branch must have run too.
        await asyncio.wait_for(finished_or_cancelled.wait(), timeout=1.0)
        # Pool must be reusable.
        async def quick(page):
            return "post-cancel"

        out = await pool.run_with_page(quick, timeout_ms=1000)
        self.assertEqual(out, "post-cancel")
        await pool.shutdown()


class SemaphoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_max_contexts_is_respected(self):
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))

        running = 0
        peak = 0
        gate = asyncio.Event()

        async def worker(page):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await gate.wait()
            running -= 1
            return None

        # Start 4 callers; only 2 should be inside `worker` at once.
        tasks = [asyncio.create_task(pool.run_with_page(worker, timeout_ms=5000)) for _ in range(4)]
        # Wait a tick for them to settle on the semaphore.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if running == 2:
                break
        self.assertEqual(running, 2)
        self.assertEqual(peak, 2)
        gate.set()
        await asyncio.gather(*tasks)
        self.assertEqual(peak, 2, "peak parallelism must equal max_contexts")
        await pool.shutdown()


class DisconnectTest(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_browser_triggers_rebuild_on_next_acquire(self):
        browsers: list[FakeBrowser] = []

        async def factory():
            b = FakeBrowser()
            browsers.append(b)
            return b

        pool = BrowserPool(max_contexts=2, browser_factory=factory)

        async def quick(page):
            return None

        await pool.run_with_page(quick, timeout_ms=1000)
        # Simulate the underlying browser crashing.
        browsers[-1].disconnect()
        await pool.run_with_page(quick, timeout_ms=1000)
        self.assertEqual(len(browsers), 2, "disconnect should force rebuild")
        await pool.shutdown()


class RebuildSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_rebuild_demand_starts_one_factory(self):
        factory_started = asyncio.Event()
        factory_release = asyncio.Event()
        factory_calls = 0

        async def factory():
            nonlocal factory_calls
            factory_calls += 1
            factory_started.set()
            await factory_release.wait()
            return FakeBrowser()

        pool = BrowserPool(max_contexts=2, browser_factory=factory)

        async def quick(page):
            return "ok"

        tasks = [
            asyncio.create_task(pool.run_with_page(quick, timeout_ms=1000))
            for _ in range(2)
        ]
        await asyncio.wait_for(factory_started.wait(), timeout=1.0)
        await asyncio.sleep(0.05)
        self.assertEqual(factory_calls, 1)

        factory_release.set()
        self.assertEqual(await asyncio.gather(*tasks), ["ok", "ok"])
        self.assertEqual(factory_calls, 1)
        await pool.shutdown()

    async def test_rebuild_timeout_leaves_pool_usable_for_later_acquire(self):
        browsers: list[FakeBrowser] = []
        rebuild_started = asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            if calls == 1:
                browser = FakeBrowser()
                browsers.append(browser)
                return browser
            if calls == 2:
                rebuild_started.set()
                await asyncio.sleep(30)
            browser = FakeBrowser()
            browsers.append(browser)
            return browser

        pool = BrowserPool(max_contexts=1, browser_factory=factory)

        async def quick(page):
            return "ok"

        await pool.run_with_page(quick, timeout_ms=1000)
        browsers[0].disconnect()

        with self.assertRaises(asyncio.TimeoutError):
            await pool.run_with_page(quick, timeout_ms=100)
        await asyncio.wait_for(rebuild_started.wait(), timeout=1.0)
        health = await pool.health()
        self.assertEqual(health.contexts_available, 0)
        self.assertFalse(health.browser_connected)

        self.assertEqual(await pool.run_with_page(quick, timeout_ms=1000), "ok")
        self.assertEqual(len(browsers), 2)
        await pool.shutdown()

    async def test_shutdown_during_gated_rebuild_closes_uncommitted_browser(self):
        factory_started = asyncio.Event()
        factory_release = asyncio.Event()
        returned_browser = FakeBrowser()

        async def factory():
            factory_started.set()
            await factory_release.wait()
            return returned_browser

        pool = BrowserPool(max_contexts=1, browser_factory=factory)
        pool._cleanup_timeout_s = 0.05

        async def quick(page):
            return "ok"

        run_task = asyncio.create_task(pool.run_with_page(quick, timeout_ms=1000))
        await asyncio.wait_for(factory_started.wait(), timeout=1.0)
        shutdown_task = asyncio.create_task(pool.shutdown())
        await asyncio.sleep(0.05)
        self.assertFalse(shutdown_task.done())

        factory_release.set()
        with self.assertRaises(PoolShutdown):
            await run_task
        await asyncio.wait_for(shutdown_task, timeout=1.0)
        self.assertTrue(returned_browser.closed)
        health = await pool.health()
        self.assertFalse(health.browser_connected)
        self.assertEqual(len(pool._cleanup_tasks), 0)


class CleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_hanging_page_close_is_bounded_and_does_not_hold_pool_lock(self):
        def page_factory():
            return FakePage(behaviour=PageBehaviour(close_hang_seconds=30.0))

        def context_factory(**_kw):
            return FakeContext(page_factory=page_factory)

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))
        pool._cleanup_timeout_s = 0.05

        async def quick(page):
            return "ok"

        t0 = time.monotonic()
        self.assertEqual(await pool.run_with_page(quick, timeout_ms=100), "ok")
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.5)

        health = await asyncio.wait_for(pool.health(), timeout=0.1)
        self.assertEqual(health.contexts_in_use, 0)
        self.assertEqual(health.contexts_available, 0)
        await pool.shutdown()

    async def test_rebuild_old_resource_cleanup_uses_one_batch_cap(self):
        class HangingCloseContext(FakeContext):
            async def close(self):
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    self.closed = True
                    raise

        class HangingCloseBrowser(FakeBrowser):
            async def close(self):
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    self._closed = True
                    self._connected = False
                    raise

        old_browser = HangingCloseBrowser()
        old_contexts = [HangingCloseContext(), HangingCloseContext()]
        new_browser = FakeBrowser()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            return old_browser if calls == 1 else new_browser

        pool = BrowserPool(max_contexts=1, browser_factory=factory)
        pool._cleanup_timeout_s = 0.05

        async def quick(page):
            return "ok"

        await pool.run_with_page(quick, timeout_ms=1000)
        async with pool._lock:
            pool._state.contexts = list(old_contexts)
        old_browser.disconnect()

        t0 = time.monotonic()
        self.assertEqual(await pool.run_with_page(quick, timeout_ms=1000), "ok")
        for _ in range(20):
            if not pool._cleanup_tasks:
                break
            await asyncio.sleep(0.01)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.5)
        self.assertEqual(len(pool._cleanup_tasks), 0)
        self.assertTrue(old_browser.closed)
        await pool.shutdown()

    async def test_shutdown_drains_tracked_cleanup_tasks(self):
        class HangingCloseContext(FakeContext):
            async def close(self):
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    self.closed = True
                    raise

        old_context = HangingCloseContext()
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(FakeBrowser()))
        pool._cleanup_timeout_s = 0.05
        pool._track_cleanup(old_context)

        await pool.shutdown()

        self.assertEqual(len(pool._cleanup_tasks), 0)
        self.assertTrue(old_context.closed)


class AcquireTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_timeout_when_pool_saturated(self):
        browser = FakeBrowser()
        pool = BrowserPool(
            max_contexts=1,
            browser_factory=_factory(browser),
            acquire_timeout_ms=200,
        )

        gate = asyncio.Event()

        async def holder(page):
            await gate.wait()
            return None

        async def quick(page):
            return None

        holder_task = asyncio.create_task(pool.run_with_page(holder, timeout_ms=5000))
        # Give holder time to grab the only slot.
        await asyncio.sleep(0.05)
        with self.assertRaises(PoolAcquireTimeout):
            await pool.run_with_page(quick, timeout_ms=1000)
        gate.set()
        await holder_task
        await pool.shutdown()

    async def test_explicit_acquire_timeout_is_capped_by_call_deadline(self):
        browser = FakeBrowser()
        pool = BrowserPool(
            max_contexts=1,
            browser_factory=_factory(browser),
            acquire_timeout_ms=5000,
        )

        gate = asyncio.Event()

        async def holder(page):
            await gate.wait()

        async def quick(page):
            return None

        holder_task = asyncio.create_task(pool.run_with_page(holder, timeout_ms=5000))
        await asyncio.sleep(0.05)

        t0 = time.monotonic()
        with self.assertRaises(PoolAcquireTimeout) as cm:
            await pool.run_with_page(quick, timeout_ms=100)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 1.0)
        self.assertIn("source deadline", str(cm.exception))
        gate.set()
        await holder_task
        await pool.shutdown()

    async def test_deadline_aware_queue_waits_without_fixed_acquire_cap(self):
        browser = FakeBrowser()
        pool = BrowserPool(
            max_contexts=1,
            browser_factory=_factory(browser),
            acquire_timeout_ms=None,
        )

        gate = asyncio.Event()
        holder_started = asyncio.Event()

        async def holder(page):
            holder_started.set()
            await gate.wait()

        async def quick(page):
            return "queued-ok"

        holder_task = asyncio.create_task(pool.run_with_page(holder, timeout_ms=1000))
        await asyncio.wait_for(holder_started.wait(), timeout=1.0)
        queued_task = asyncio.create_task(pool.run_with_page(quick, timeout_ms=1000))
        await asyncio.sleep(0.05)
        self.assertFalse(queued_task.done())

        gate.set()
        self.assertEqual(await queued_task, "queued-ok")
        await holder_task
        await pool.shutdown()


class CheckoutAccountingTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_context_timeout_does_not_decrement_other_active_context(self):
        class HangingSecondContextBrowser(FakeBrowser):
            async def new_context(self, **kwargs):
                self.new_context_calls += 1
                if self.new_context_calls == 2:
                    await asyncio.sleep(30)
                ctx = FakeContext(accept_downloads=kwargs.get("accept_downloads", False))
                self._contexts.append(ctx)
                return ctx

        browser = HangingSecondContextBrowser()
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))
        gate = asyncio.Event()
        holder_started = asyncio.Event()

        async def holder(page):
            holder_started.set()
            await gate.wait()

        async def quick(page):
            return None

        holder_task = asyncio.create_task(pool.run_with_page(holder, timeout_ms=1000))
        await asyncio.wait_for(holder_started.wait(), timeout=1.0)

        with self.assertRaises(asyncio.TimeoutError):
            await pool.run_with_page(quick, timeout_ms=100)

        health = await pool.health()
        self.assertEqual(health.contexts_in_use, 1)
        gate.set()
        await holder_task
        await pool.shutdown()

    async def test_rejected_context_from_stale_browser_is_cleaned_without_checkout(self):
        rejected_context = FakeContext()
        replacement_browser = FakeBrowser()
        started = asyncio.Event()
        release = asyncio.Event()

        class StaleContextBrowser(FakeBrowser):
            async def new_context(self, **kwargs):
                self.new_context_calls += 1
                started.set()
                await release.wait()
                return rejected_context

        stale_browser = StaleContextBrowser()
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(stale_browser))

        async def quick(page):
            return "ok"

        task = asyncio.create_task(pool.run_with_page(quick, timeout_ms=1000))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        async with pool._lock:
            pool._state.browser = replacement_browser
        release.set()

        self.assertEqual(await task, "ok")
        for _ in range(20):
            if rejected_context.closed:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(rejected_context.closed)
        health = await pool.health()
        self.assertEqual(health.contexts_in_use, 0)
        await pool.shutdown()

    async def test_context_returning_after_shutdown_is_cleaned_without_checkout(self):
        returned_context = FakeContext()
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowContextBrowser(FakeBrowser):
            async def new_context(self, **kwargs):
                self.new_context_calls += 1
                started.set()
                await release.wait()
                return returned_context

        browser = SlowContextBrowser()
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))

        async def quick(page):
            return None

        task = asyncio.create_task(pool.run_with_page(quick, timeout_ms=1000))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await pool.shutdown()
        release.set()

        with self.assertRaises(PoolShutdown):
            await task
        for _ in range(20):
            if returned_context.closed:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(returned_context.closed)


class AntiBotProbeTest(unittest.IsolatedAsyncioTestCase):
    async def test_anti_bot_title_detected(self):
        page = FakePage(behaviour=PageBehaviour(title="Доступ ограничен"))
        block = await is_blocked(page)
        assert block is not None
        self.assertEqual(block.reason, BlockReason.ANTI_BOT_PAGE)

    async def test_cloudflare_title_detected(self):
        page = FakePage(behaviour=PageBehaviour(title="Just a moment..."))
        block = await is_blocked(page)
        assert block is not None
        self.assertEqual(block.reason, BlockReason.ANTI_BOT_PAGE)

    async def test_captcha_iframe_detected(self):
        page = FakePage(
            behaviour=PageBehaviour(
                title="Verify",
                iframes=['iframe[src*="recaptcha"]'],
            )
        )
        block = await is_blocked(page)
        assert block is not None
        self.assertEqual(block.reason, BlockReason.CAPTCHA_PAGE)

    async def test_login_redirect_detected(self):
        page = FakePage(behaviour=PageBehaviour(url="https://rabota.by/login"))
        block = await is_blocked(page)
        assert block is not None
        self.assertEqual(block.reason, BlockReason.LOGIN_REDIRECT)

    async def test_anti_bot_redirect_path_detected(self):
        page = FakePage(
            behaviour=PageBehaviour(
                url="https://omsk.hh.ru/vpncheeck?backUrl=%2Fsearch%2Fvacancy"
            )
        )
        block = await is_blocked(page)
        assert block is not None
        self.assertEqual(block.reason, BlockReason.ANTI_BOT_PAGE)
        self.assertIn("vpncheeck", block.signal)

    async def test_anti_bot_body_detected(self):
        page = FakePage(
            behaviour=PageBehaviour(
                title="",
                url="https://hh.ru/search/vacancy",
                content="<html><body>You have been blocked</body></html>",
            )
        )
        block = await is_blocked(page)
        assert block is not None
        self.assertEqual(block.reason, BlockReason.ANTI_BOT_PAGE)
        self.assertIn("blocked", block.signal)

    async def test_clean_page_returns_none(self):
        page = FakePage(behaviour=PageBehaviour(title="QA Engineer at Acme", url="https://acme.test/jobs/qa"))
        block = await is_blocked(page)
        self.assertIsNone(block)

    async def test_pool_wraps_blocked_result(self):
        # A page whose title trips anti-bot detection causes run_with_page
        # to return BlockedResult, not the user value.
        def page_factory():
            return FakePage(behaviour=PageBehaviour(title="Доступ ограничен"))

        def context_factory(**_kw):
            return FakeContext(page_factory=page_factory)

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))

        async def use(page):
            return "value-from-func"

        out = await pool.run_with_page(use, timeout_ms=1000)
        self.assertIsInstance(out, BlockedResult)
        self.assertEqual(out.block.reason, BlockReason.ANTI_BOT_PAGE)
        self.assertEqual(out.partial, "value-from-func")
        await pool.shutdown()

    async def test_pool_wraps_browser_blocked_exception(self):
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))

        async def use(page):
            raise BrowserBlocked(
                BlockSignal(reason=BlockReason.ANTI_BOT_PAGE, signal="HTTP 403")
            )

        out = await pool.run_with_page(use, timeout_ms=1000)
        self.assertIsInstance(out, BlockedResult)
        self.assertEqual(out.block.reason, BlockReason.ANTI_BOT_PAGE)
        self.assertEqual(out.block.signal, "HTTP 403")
        self.assertIsNone(out.partial)
        await pool.shutdown()

    async def test_post_call_probe_timeout_preserves_successful_value(self):
        original = browser_pool_module.is_blocked

        async def slow_probe(page):
            await asyncio.sleep(30)

        browser_pool_module.is_blocked = slow_probe
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))

        async def use(page):
            await asyncio.sleep(0.01)
            return "value-from-func"

        try:
            out = await pool.run_with_page(use, timeout_ms=80)
        finally:
            browser_pool_module.is_blocked = original
            await pool.shutdown()

        self.assertEqual(out, "value-from-func")

    async def test_quick_post_call_probe_preserves_blocked_partial(self):
        original = browser_pool_module.is_blocked

        async def quick_probe(page):
            return BlockSignal(reason=BlockReason.ANTI_BOT_PAGE, signal="fast block")

        browser_pool_module.is_blocked = quick_probe
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))

        async def use(page):
            return "value-from-func"

        try:
            out = await pool.run_with_page(use, timeout_ms=1000)
        finally:
            browser_pool_module.is_blocked = original
            await pool.shutdown()

        self.assertIsInstance(out, BlockedResult)
        self.assertEqual(out.partial, "value-from-func")
        self.assertEqual(out.block.signal, "fast block")


class PoisonContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_page_close_failure_discards_context(self):
        # The page raises on close; the pool must not return that
        # context to the idle pool, and the next acquire must build
        # a fresh context.
        def page_factory():
            return FakePage(behaviour=PageBehaviour(close_raises=RuntimeError("close fail")))

        def context_factory(**_kw):
            return FakeContext(page_factory=page_factory)

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))

        async def use(page):
            return "ok"

        await pool.run_with_page(use, timeout_ms=1000)
        # Next call must create a NEW context, not reuse the poisoned one.
        await pool.run_with_page(use, timeout_ms=1000)
        self.assertGreaterEqual(browser.new_context_calls, 2)
        await pool.shutdown()


class ContextKwargsTest(unittest.IsolatedAsyncioTestCase):
    async def test_accept_downloads_false_by_default(self):
        seen = {}

        def context_factory(**kw):
            seen.update(kw)
            return FakeContext()

        browser = FakeBrowser(context_factory=context_factory)
        pool = BrowserPool(max_contexts=1, browser_factory=_factory(browser))

        async def use(page):
            return None

        await pool.run_with_page(use, timeout_ms=1000)
        self.assertEqual(seen.get("accept_downloads"), False)
        await pool.shutdown()


class ShutdownTest(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_closes_browser_and_contexts(self):
        browser = FakeBrowser()
        pool = BrowserPool(max_contexts=2, browser_factory=_factory(browser))

        async def use(page):
            return None

        await pool.run_with_page(use, timeout_ms=1000)
        await pool.shutdown()
        self.assertTrue(browser.closed)


if __name__ == "__main__":
    unittest.main()
