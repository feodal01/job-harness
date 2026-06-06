"""Tests for the non-blocking MCP surface.

Loads scripts/mcp-server.py the same way test_mcp_server.py does, then
exercises search_start / search_status / search_results / search_cancel /
search_refine / list_active_runs against a per-test scraper registry.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar

from job_harness.base import BaseScraper
from job_harness.models import JobListing, SearchParams
from job_harness.registry import _SCRAPERS, register_scraper
from job_harness.types import FilterSupport, RunState, ScraperCapabilities, SourceState

# ---------------------------------------------------------------------------
# MCP server loader (mirrors tests/test_mcp_server.py)
# ---------------------------------------------------------------------------


def _load_mcp_server():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mcp-server.py"
    spec = importlib.util.spec_from_file_location("job_harness_async_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Test scrapers
# ---------------------------------------------------------------------------


def _caps() -> ScraperCapabilities:
    return {
        "remote_only": FilterSupport.SERVER,
        "country": FilterSupport.SERVER,
        "experience": FilterSupport.SERVER,
        "location": FilterSupport.CLIENT,
        "has_salary": FilterSupport.CLIENT,
        "query_match": FilterSupport.SERVER,
    }


class _OkScraper(BaseScraper):
    display_name = "OK"
    requires_browser = False
    detail_requires_browser = False
    countries: tuple[str, ...] = ()
    capabilities: ClassVar[ScraperCapabilities] = _caps()

    @classmethod
    def supports_country(cls, country):
        return True

    def search(self, params: SearchParams):
        return [
            JobListing(title="QA Senior", url=f"https://x/{self.name}/1", company="Acme", source=self.name, remote=True, experience="senior"),
            JobListing(title="QA Junior", url=f"https://x/{self.name}/2", company="Acme", source=self.name, remote=False, experience="junior"),
        ]

    def fetch_detail(self, listing):
        return listing


class _SlowScraper(_OkScraper):
    SLEEP_S: ClassVar[float] = 0.5

    def search(self, params: SearchParams):
        time.sleep(type(self).SLEEP_S)
        return [JobListing(title=f"slow-{self.name}", url=f"https://x/{self.name}/1", company=f"Co-{self.name}", source=self.name)]


class _FlakyScraper(_OkScraper):
    _fail_first: ClassVar[bool] = True

    def search(self, params: SearchParams):
        if type(self)._fail_first:
            type(self)._fail_first = False
            raise RuntimeError("transient failure")
        return [
            JobListing(
                title="QA retry",
                url="https://hh.ru/vacancy/999",
                company="Acme",
                source=self.name,
            )
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RegistryContext:
    def __init__(self, classes: dict[str, type[BaseScraper]]):
        self._classes = classes
        self._saved: dict = {}

    def __enter__(self):
        self._saved = dict(_SCRAPERS)
        _SCRAPERS.clear()
        for name, cls in self._classes.items():
            register_scraper(name)(cls)
        return self

    def __exit__(self, *_e):
        _SCRAPERS.clear()
        _SCRAPERS.update(self._saved)


class _RunsRootContext:
    """Point the module's RUNS_ROOT at a tempdir and reset singletons."""

    def __init__(self, module):
        self._module = module
        self._tmp = tempfile.TemporaryDirectory()
        self._saved: dict = {}

    def __enter__(self):
        self._saved["_RUNS_ROOT"] = self._module._RUNS_ROOT
        self._saved["_engine_singleton"] = self._module._engine_singleton
        self._saved["_run_registry_singleton"] = self._module._run_registry_singleton
        self._module._RUNS_ROOT = Path(self._tmp.name)
        self._module._engine_singleton = None
        self._module._run_registry_singleton = None
        return Path(self._tmp.name)

    def __exit__(self, *_e):
        # Cleanly stop the registry if it was created.
        reg = self._module._run_registry_singleton
        if reg is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Best-effort; tests on IsolatedAsyncioTestCase
                    # take care of their own loop.
                    pass
            except RuntimeError:
                pass
        self._module._RUNS_ROOT = self._saved["_RUNS_ROOT"]
        self._module._engine_singleton = self._saved["_engine_singleton"]
        self._module._run_registry_singleton = self._saved["_run_registry_singleton"]
        self._tmp.cleanup()


async def _wait_state(server, run_id, target: RunState, timeout: float = 5.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = await server.search_status(run_id)
        if s.get("state") == target.value:
            return s
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {target}")


async def _wait_source_ok(
    server, run_id: str, source: str, timeout: float = 5.0
) -> dict:
    """Wait until a source reports state=ok (retry may finish before RUNNING is observed)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = await server.search_status(run_id)
        if s.get("sources", {}).get(source, {}).get("state") == SourceState.OK.value:
            return s
        await asyncio.sleep(0.02)
    raise AssertionError(f"source {source} in run {run_id} never reached ok")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class StartReturnsImmediatelyTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_returns_under_100ms_even_for_slow_engine(self):
        server = _load_mcp_server()
        with _RegistryContext({"slow": _SlowScraper}), _RunsRootContext(server):
            t0 = time.monotonic()
            result = await server.search_start(query="QA", sources="slow")
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 0.5, f"search_start blocked for {elapsed:.2f}s")
            self.assertIn("run_id", result)
            # Wait for it to complete so the tempdir cleanup is clean.
            await _wait_state(server, result["run_id"], RunState.COMPLETED, timeout=5)
            await server._get_run_registry().shutdown()


class StatusAndResultsTest(unittest.IsolatedAsyncioTestCase):
    async def test_results_after_completion(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            results = await server.search_results(r["run_id"])
            self.assertIn("path", results)
            payload = json.loads(Path(results["path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], RunState.COMPLETED.value)
            self.assertEqual(payload["listings_count"], 2)
            self.assertEqual(len(payload["listings"]), 2)
            self.assertNotIn("sources", payload)
            await server._get_run_registry().shutdown()

    async def test_status_reflects_running_then_completed(self):
        server = _load_mcp_server()
        with _RegistryContext({"slow": _SlowScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="slow")
            # While running we should see state=running.
            mid = await server.search_status(r["run_id"])
            self.assertIn(mid["state"], (RunState.RUNNING.value, RunState.COMPLETED.value))
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            await server._get_run_registry().shutdown()

    async def test_unknown_run_id_returns_error(self):
        server = _load_mcp_server()
        with _RunsRootContext(server):
            out = await server.search_results("r-99999999-999999-deadbe")
            self.assertEqual(out["error"], "unknown_run_id")


class CancelTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_flips_state_to_cancelled(self):
        class VerySlow(_SlowScraper):
            SLEEP_S = 5.0

        server = _load_mcp_server()
        with _RegistryContext({"slow": VerySlow}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="slow", total_timeout_ms=10_000)
            await asyncio.sleep(0.05)
            ack = await server.search_cancel(r["run_id"])
            self.assertEqual(ack["state"], "cancelling")
            await _wait_state(server, r["run_id"], RunState.CANCELLED, timeout=3)
            await server._get_run_registry().shutdown()

    async def test_cancel_is_idempotent(self):
        class VerySlow(_SlowScraper):
            SLEEP_S = 5.0

        server = _load_mcp_server()
        with _RegistryContext({"slow": VerySlow}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="slow", total_timeout_ms=10_000)
            await asyncio.sleep(0.05)
            await server.search_cancel(r["run_id"])
            second = await server.search_cancel(r["run_id"])
            self.assertEqual(second["state"], "cancelling")
            await _wait_state(server, r["run_id"], RunState.CANCELLED, timeout=3)
            await server._get_run_registry().shutdown()


class SearchResultsFormatTest(unittest.IsolatedAsyncioTestCase):
    async def test_file_format_writes_results_json(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            out = await server.search_results(r["run_id"], format="file")
            path = Path(out["path"])
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["listings_count"], 2)
            self.assertEqual(len(payload["listings"]), 2)
            self.assertIn("request", payload)
            await server._get_run_registry().shutdown()

    async def test_inline_format_returns_slice(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            out = await server.search_results(r["run_id"], format="inline", limit=1, offset=0)
            self.assertEqual(out["total"], 2)
            self.assertEqual(out["limit"], 1)
            self.assertEqual(len(out["listings"]), 1)
            self.assertNotIn("sources", out)
            await server._get_run_registry().shutdown()

    async def test_inline_limit_hard_cap(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            out = await server.search_results(r["run_id"], format="inline", limit=100)
            self.assertEqual(out["limit"], server.INLINE_LIMIT_MAX)
            self.assertTrue(out["limit_capped"])
            self.assertIn("hint", out)
            self.assertEqual(len(out["listings"]), 2)
            await server._get_run_registry().shutdown()

    async def test_inline_offset_pagination(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            out = await server.search_results(r["run_id"], format="inline", limit=1, offset=1)
            self.assertEqual(out["offset"], 1)
            self.assertEqual(len(out["listings"]), 1)
            self.assertEqual(out["listings"][0]["experience"], "junior")
            await server._get_run_registry().shutdown()

    async def test_debug_includes_sources_inline(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            out = await server.search_results(r["run_id"], format="inline", debug=True)
            self.assertIn("sources", out)
            self.assertIn("src", out["sources"])
            await server._get_run_registry().shutdown()

    async def test_debug_includes_sources_in_file(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            out = await server.search_results(r["run_id"], format="file", debug=True)
            payload = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
            self.assertIn("sources", payload)
            self.assertIn("src", payload["sources"])
            await server._get_run_registry().shutdown()

    async def test_unknown_run_id_both_formats(self):
        server = _load_mcp_server()
        with _RunsRootContext(server):
            for fmt in ("file", "inline"):
                out = await server.search_results("r-99999999-999999-deadbe", format=fmt)
                self.assertEqual(out["error"], "unknown_run_id")

    async def test_still_running_without_partial(self):
        class VerySlow(_SlowScraper):
            SLEEP_S = 5.0

        server = _load_mcp_server()
        with _RegistryContext({"slow": VerySlow}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="slow", total_timeout_ms=10_000)
            await asyncio.sleep(0.05)
            out = await server.search_results(
                r["run_id"], format="inline", include_partial=False
            )
            self.assertEqual(out["error"], "still_running")
            await server.search_cancel(r["run_id"])
            await _wait_state(server, r["run_id"], RunState.CANCELLED, timeout=3)
            await server._get_run_registry().shutdown()


class RefineTest(unittest.IsolatedAsyncioTestCase):
    async def test_refine_filters_journal_without_rescrape(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            refined = await server.search_refine(r["run_id"], experience="senior")
            # Only the senior listing survives.
            self.assertEqual(refined["total"], 1)
            self.assertEqual(refined["listings"][0]["experience"], "senior")
            self.assertIn("experience", refined["refine_filters"])
            await server._get_run_registry().shutdown()


class ListRunsTest(unittest.IsolatedAsyncioTestCase):
    async def test_list_active_runs_includes_recent(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="alpha", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            recent = await server.list_active_runs(limit=10)
            queries = [s["query"] for s in recent["runs"]]
            self.assertIn("alpha", queries)
            await server._get_run_registry().shutdown()


class SearchRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retry_failed_source_and_skip_ok(self):
        server = _load_mcp_server()
        _FlakyScraper._fail_first = True
        with _RegistryContext({"ok": _OkScraper, "flaky": _FlakyScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="ok,flaky")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            status = await server.search_status(r["run_id"])
            self.assertIn("flaky", status["retryable_sources"])
            retry = await server.search_retry(r["run_id"], sources="ok,flaky")
            self.assertEqual(retry["retried_sources"], ["flaky"])
            self.assertEqual(retry["skipped_sources"]["ok"]["reason"], "already_ok")
            await _wait_source_ok(server, r["run_id"], "flaky")
            results = await server.search_results(r["run_id"], format="file")
            payload = json.loads(Path(results["path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["listings_count"], 3)
            await server._get_run_registry().shutdown()

    async def test_retry_unknown_run_id_includes_hint(self):
        server = _load_mcp_server()
        with _RunsRootContext(server):
            out = await server.search_retry("r-99999999-999999-deadbe", sources="hh_ru")
            self.assertEqual(out["error"], "unknown_run_id")
            self.assertIn("hint", out)
            await server._get_run_registry().shutdown()

    async def test_retry_invalid_sources_returns_retryable_hint(self):
        server = _load_mcp_server()
        with _RegistryContext({"src": _OkScraper}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="src")
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            out = await server.search_retry(r["run_id"], sources="hh.ru,made_up")
            self.assertEqual(out["error"], "invalid_sources")
            self.assertIn("retryable_sources", out)
            self.assertIn("sources_in_run", out)
            await server._get_run_registry().shutdown()

    async def test_retry_dedupes_export_after_recovery(self):
        class _DupOk(_OkScraper):
            def search(self, params: SearchParams):
                return [
                    JobListing(
                        title="QA",
                        url="https://hh.ru/vacancy/42",
                        company="Acme",
                        source=self.name,
                    )
                ]

        class _DupFlaky(_FlakyScraper):
            def search(self, params: SearchParams):
                if type(self)._fail_first:
                    type(self)._fail_first = False
                    raise RuntimeError("fail")
                return [
                    JobListing(
                        title="QA duplicate",
                        url="https://hh.ru/vacancy/42",
                        company="Acme",
                        source=self.name,
                    )
                ]

        server = _load_mcp_server()
        _DupFlaky._fail_first = True
        with _RegistryContext({"ok": _DupOk, "flaky": _DupFlaky}), _RunsRootContext(server):
            r = await server.search_start(query="QA", sources="ok,flaky", dedupe=True)
            await _wait_state(server, r["run_id"], RunState.COMPLETED)
            await server.search_retry(r["run_id"], sources="flaky")
            await _wait_source_ok(server, r["run_id"], "flaky")
            out = await server.search_results(r["run_id"], format="inline")
            self.assertEqual(out["total"], 1)
            await server._get_run_registry().shutdown()


class MaxConcurrentRunsTest(unittest.IsolatedAsyncioTestCase):
    async def test_cap_returns_structured_error(self):
        class VerySlow(_SlowScraper):
            SLEEP_S = 5.0

        server = _load_mcp_server()
        with _RegistryContext({"slow": VerySlow}), _RunsRootContext(server):
            # Construct the registry with a cap of 1.
            from job_harness.run_registry import RunRegistry

            async def runner(request, journal, run_id, retry_sources=None):
                engine = server._get_engine()
                if retry_sources:
                    await engine.execute_retry(
                        request,
                        journal=journal,
                        run_id=run_id,
                        sources=retry_sources,
                    )
                else:
                    await engine.execute(request, journal=journal, run_id=run_id)

            server._run_registry_singleton = RunRegistry(
                runs_root=server._RUNS_ROOT,
                engine_runner=runner,
                max_concurrent_runs=1,
            )
            first = await server.search_start(query="A", sources="slow", total_timeout_ms=10_000)
            second = await server.search_start(query="B", sources="slow", total_timeout_ms=10_000)
            self.assertEqual(second.get("error"), "max_concurrent_runs_reached")
            self.assertEqual(len(second["active_runs"]), 1)
            self.assertEqual(second["active_runs"][0]["run_id"], first["run_id"])
            await server._run_registry_singleton.cancel(first["run_id"])
            await server._run_registry_singleton.shutdown()


if __name__ == "__main__":
    unittest.main()
