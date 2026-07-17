from __future__ import annotations

import json
import runpy
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECKER = runpy.run_path(str(_REPO_ROOT / "scripts" / "check_plugin_packaging.py"))
_PACKAGING_FILES = (
    *_CHECKER["REQUIRED_JSON_PATHS"],
    "plugins/job-harness/pyproject.toml",
    "plugins/job-harness/uv.lock",
)


def _call(name: str, *args: Any) -> Any:
    value = _CHECKER[name]
    if not callable(value):
        raise AssertionError(f"{name} is not callable")
    return value(*args)


class PluginPackagingTest(unittest.TestCase):
    def test_repository_packaging_is_valid_for_all_hosts(self) -> None:
        self.assertEqual((), _call("validate_packaging", _REPO_ROOT))

    def test_mcp_launcher_resolves_cursor_plugin_root_first(self) -> None:
        config_path = _REPO_ROOT / "plugins/job-harness/.mcp.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        shell_command = config["mcpServers"]["job-harness"]["args"][1]

        self.assertIn('ROOT="${CURSOR_PLUGIN_ROOT:-', shell_command)

    def test_lock_package_must_remain_editable_from_plugin_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative_path in _PACKAGING_FILES:
                destination = root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(_REPO_ROOT / relative_path, destination)
            lock_path = root / "plugins/job-harness/uv.lock"
            content = lock_path.read_text(encoding="utf-8")
            lock_path.write_text(
                content.replace(
                    'source = { editable = "." }',
                    'source = { editable = "./wrong" }',
                    1,
                ),
                encoding="utf-8",
            )

            errors = _call("validate_packaging", root)

        self.assertIn(
            "plugins/job-harness/uv.lock job-harness source must be editable '.'",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
