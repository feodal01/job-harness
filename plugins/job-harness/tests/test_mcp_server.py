"""Tests for the lookup-only MCP tools (cache_*, list_sources,
search_company_jobs).

The search surface (search_start/status/results/cancel/refine/
list_active_runs) is covered by test_mcp_async_surface.py.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_mcp_server():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mcp-server.py"
    spec = importlib.util.spec_from_file_location("job_harness_test_mcp_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LookupToolsTest(unittest.TestCase):
    def test_list_sources_returns_registered_scrapers(self):
        server = _load_mcp_server()
        meta = server.list_sources()
        # Spot-check a few known scrapers (registered as a side effect
        # of `import job_harness.scrapers`).
        self.assertIn("hh_ru", meta)
        self.assertIn("career:vk", meta)
        # Metadata is the public source descriptor contract.
        for entry in meta.values():
            self.assertEqual(
                set(entry),
                {"group", "countries", "server_criteria", "source_limit"},
            )

    def test_search_company_jobs_uses_bundled_directory(self):
        server = _load_mcp_server()
        data = server.search_company_jobs(query="QA", country="Armenia", max_results=10)
        self.assertGreater(data["total"], 0)
        names = [company["name"] for company in data["companies"]]
        self.assertIn("Miro", names)
        for c in data["companies"]:
            self.assertIn("Armenia", c["countries"])


class CacheToolsTest(unittest.TestCase):
    def test_cache_round_trip(self):
        server = _load_mcp_server()
        with tempfile.TemporaryDirectory() as d:
            cache_path = Path(d) / "company-careers.json"
            with patch.object(server, "_LOCAL_CACHE", cache_path), \
                 patch.object(server, "_PUBLIC_CACHE", cache_path):
                # Initially empty.
                self.assertEqual(server.cache_stats()["total"], 0)
                # Upsert.
                entry = server.cache_upsert(
                    company="Acme",
                    careers_url="https://acme.test/jobs",
                    ats_type="direct",
                )
                self.assertEqual(entry["company"], "Acme")
                # Read back.
                got = server.cache_get("Acme")
                self.assertEqual(got["careers_url"], "https://acme.test/jobs")
                # Stats.
                stats = server.cache_stats()
                self.assertEqual(stats["total"], 1)
                self.assertEqual(stats["with_careers_url"], 1)

    def test_cache_get_unknown_returns_none(self):
        server = _load_mcp_server()
        with tempfile.TemporaryDirectory() as d:
            cache_path = Path(d) / "company-careers.json"
            cache_path.write_text(json.dumps({"companies": []}), encoding="utf-8")
            with patch.object(server, "_LOCAL_CACHE", cache_path), \
                 patch.object(server, "_PUBLIC_CACHE", cache_path):
                self.assertIsNone(server.cache_get("nobody"))


if __name__ == "__main__":
    unittest.main()
