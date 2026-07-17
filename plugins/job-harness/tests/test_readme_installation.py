from __future__ import annotations

import unittest
from pathlib import Path

_README = Path(__file__).resolve().parents[3] / "README.md"


class ReadmeInstallationTest(unittest.TestCase):
    def test_cursor_uses_local_plugin_install_and_update(self) -> None:
        content = _README.read_text(encoding="utf-8")

        self.assertIn('$HOME/.cursor/plugins/local/job-harness', content)
        self.assertIn('cp -R "$tmp_dir/job-harness/plugins/job-harness"', content)
        self.assertIn("Developer: Reload Window", content)
        self.assertNotIn("There is no separate Cursor plugin install step", content)

    def test_codex_and_claude_commands_remain_documented(self) -> None:
        content = _README.read_text(encoding="utf-8")

        for command in (
            "codex plugin marketplace add https://github.com/feodal01/job-harness.git --ref main",
            "codex plugin add job-harness@job-harness",
            "codex plugin marketplace upgrade job-harness",
            "claude plugin marketplace add feodal01/job-harness#main",
            "claude plugin install job-harness@job-harness --scope user",
            "claude plugin update job-harness",
        ):
            with self.subTest(command=command):
                self.assertIn(command, content)


if __name__ == "__main__":
    unittest.main()
