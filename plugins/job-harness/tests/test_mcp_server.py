from __future__ import annotations

import asyncio
import importlib.util
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.models import JobListing


def _load_mcp_server():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mcp-server.py"
    spec = importlib.util.spec_from_file_location("job_harness_test_mcp_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _NoBrowserScraperClass:
    requires_browser = False
    detail_requires_browser = False


class _BrowserScraperClass:
    requires_browser = True
    detail_requires_browser = True


class _FakeScraper:
    display_name = "Fake Scraper"

    def __init__(self, context, max_results: int = 20):
        self.context = context
        self.max_results = max_results

    def search(self, params):
        return [
            JobListing(
                title="QA Engineer",
                url="https://example.test/jobs/1",
                company="Example",
                remote=True,
                source="fake",
            )
        ][: self.max_results]

    def fetch_detail(self, listing):
        listing.description = "Detailed description"
        return listing


class McpServerTest(unittest.TestCase):
    def test_sync_playwright_work_is_dispatched_outside_event_loop(self) -> None:
        server = _load_mcp_server()

        async def run():
            event_loop_thread = threading.current_thread().name

            def blocking_work():
                return threading.current_thread().name

            worker_thread = await server._run_in_browser_thread(blocking_work)
            return event_loop_thread, worker_thread

        event_loop_thread, worker_thread = asyncio.run(run())

        self.assertNotEqual(event_loop_thread, worker_thread)
        self.assertTrue(worker_thread.startswith("job-harness-browser"))

    def test_search_does_not_initialize_browser_for_non_browser_scraper(self) -> None:
        server = _load_mcp_server()

        with (
            patch.object(server, "_ensure_browser", side_effect=AssertionError("browser not expected")),
            patch("job_harness.registry.get_scraper_class", return_value=_NoBrowserScraperClass),
            patch("job_harness.registry.create_scraper", return_value=_FakeScraper(context=None, max_results=1)),
        ):
            data = server._search_impl(query="QA", sources="fake", max_results=1)

        self.assertEqual(1, data["total"])
        self.assertEqual([], data["errors"])
        self.assertEqual("QA Engineer", data["listings"][0]["title"])

    def test_search_initializes_browser_for_browser_scraper(self) -> None:
        server = _load_mcp_server()
        contexts = []

        def create_scraper(name, context, **kwargs):
            contexts.append(context)
            return _FakeScraper(context=context, max_results=kwargs["max_results"])

        with (
            patch.object(server, "_ensure_browser", return_value="browser-context") as ensure_browser,
            patch("job_harness.registry.get_scraper_class", return_value=_BrowserScraperClass),
            patch("job_harness.registry.create_scraper", side_effect=create_scraper),
        ):
            data = server._search_impl(query="QA", sources="fake", max_results=1)

        self.assertEqual(1, ensure_browser.call_count)
        self.assertEqual(["browser-context"], contexts)
        self.assertEqual(1, data["total"])


if __name__ == "__main__":
    unittest.main()
