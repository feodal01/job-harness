from __future__ import annotations

import subprocess
import sys
import unittest


class CliContractTest(unittest.TestCase):
    def test_search_help_exposes_experience_levels_only(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "job_harness.cli", "search", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--experience-levels", result.stdout)
        self.assertNotIn("--experience ", result.stdout)


if __name__ == "__main__":
    unittest.main()
