from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.v2.runtime.atomic_artifacts import (
    artifact_for_bytes,
    atomic_write_bytes,
    verify_artifact,
)


class AtomicArtifactsTest(unittest.TestCase):
    def test_atomically_replaces_and_verifies_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "execution.json"
            path.write_bytes(b"old")
            expected = artifact_for_bytes(
                name="execution_receipt",
                path=path,
                schema_version=2,
                content=b"new payload\n",
            )

            atomic_write_bytes(path, b"new payload\n")

            self.assertEqual(path.read_bytes(), b"new payload\n")
            self.assertEqual(verify_artifact(expected), expected)

    def test_replace_failure_preserves_old_file_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_bytes(b"old report")

            with (
                patch.object(os, "replace", side_effect=OSError("disk failure")),
                self.assertRaisesRegex(OSError, "disk failure"),
            ):
                atomic_write_bytes(path, b"new report")

            self.assertEqual(path.read_bytes(), b"old report")
            self.assertEqual(tuple(Path(directory).glob(".report.html.*.tmp")), ())

    def test_verification_rejects_wrong_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_bytes(b"tampered")
            expected = artifact_for_bytes(
                name="report",
                path=path,
                schema_version=2,
                content=b"expected",
            )

            with self.assertRaisesRegex(ValueError, "digest"):
                verify_artifact(expected)


if __name__ == "__main__":
    unittest.main()
