from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.browser import configure_playwright_tmpdir


class BrowserConfigTest(unittest.TestCase):
    def test_rebrowser_runtime_fix_mode_keeps_hh_locators_usable_by_default(self) -> None:
        self.assertEqual("addBinding", os.environ["REBROWSER_PATCHES_RUNTIME_FIX_MODE"])

    def test_configure_playwright_tmpdir_sets_all_temp_environment_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "job-harness" / "tmp"
            with patch.dict(os.environ, {"JOB_HARNESS_TMPDIR": ""}, clear=False):
                configured = configure_playwright_tmpdir(target)

                self.assertEqual(target.resolve(), configured)
                self.assertTrue(configured.exists())
                self.assertEqual(str(configured), os.environ["TMPDIR"])
                self.assertEqual(str(configured), os.environ["TEMP"])
                self.assertEqual(str(configured), os.environ["TMP"])

    def test_mcp_launcher_exports_writable_tmpdir_before_uv(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / ".mcp.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        command = config["mcpServers"]["job-harness"]["args"][1]

        self.assertIn("ROOT=\"${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}\"", command)
        self.assertIn("[ -f \"$PWD/scripts/mcp-server.py\" ]", command)
        self.assertIn("[ -f \"$PWD/plugins/job-harness/scripts/mcp-server.py\" ]", command)
        self.assertIn("ROOT=\"$PWD/plugins/job-harness\"", command)
        self.assertIn("mkdir -p \"$JOB_HARNESS_TMP\"", command)
        self.assertIn("JOB_HARNESS_TMPDIR=\"$JOB_HARNESS_TMP\"", command)
        self.assertIn("TMPDIR=\"$JOB_HARNESS_TMP\"", command)
        self.assertLess(command.index("ROOT="), command.index("JOB_HARNESS_TMP="))
        self.assertLess(command.index("export PYTHONUNBUFFERED"), command.index("exec uv"))
        self.assertLess(command.index("TMPDIR=\"$JOB_HARNESS_TMP\""), command.index("exec uv"))


if __name__ == "__main__":
    unittest.main()
