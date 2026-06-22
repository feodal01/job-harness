from __future__ import annotations

import json
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
            self.assertEqual(paths.run_dir / "raw-listings.jsonl", paths.raw_listings_path)
            self.assertEqual(paths.run_dir / "source-attempts.jsonl", paths.source_attempts_path)
            self.assertEqual(paths.run_dir / "run-manifest.json", paths.run_manifest_path)
            self.assertEqual(paths.run_dir / "processed-results.json", paths.processed_results_path)

    def test_next_append_sequence_reads_run_manifest(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            layout = RunLayout(Path(tmp))
            paths = layout.create_new_run("r-test")
            paths.run_manifest_path.write_text(
                json.dumps({"latest_append_sequence": 3}),
                encoding="utf-8",
            )

            # Act / Assert
            self.assertEqual(4, layout.next_append_sequence("r-test"))

    def test_append_requires_existing_run(self) -> None:
        # Arrange
        with tempfile.TemporaryDirectory() as tmp:
            layout = RunLayout(Path(tmp))

            # Act / Assert
            with self.assertRaisesRegex(FileNotFoundError, "v2 run does not exist"):
                layout.next_append_sequence("r-missing")


if __name__ == "__main__":
    unittest.main()
