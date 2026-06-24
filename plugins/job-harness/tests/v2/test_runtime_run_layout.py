from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from job_harness.v2.runtime import RunLayout


class RunLayoutTest(unittest.TestCase):
    def test_create_new_run_uses_standard_v2_artifact_names(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            layout = RunLayout(Path(tmp))

            # Act
            paths = layout.create_new_run("r-test")

            # Assert
            self.assertEqual((Path(tmp) / "r-test").resolve(), paths.run_dir)
            self.assertEqual(paths.run_dir / "run.sqlite", paths.database_path)
            self.assertEqual(paths.run_dir / "report.html", paths.report_html_path)

    def test_append_requires_existing_run(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            layout = RunLayout(Path(tmp))

            # Act / Assert
            with self.assertRaisesRegex(FileNotFoundError, "v2 run does not exist"):
                layout.existing_run("r-missing")


if __name__ == "__main__":
    unittest.main()
