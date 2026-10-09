"""Verify signatures and attribute types of every registered parser implementation."""

import subprocess
import sys
from pathlib import Path


def test_parser_implementations_match_interfaces(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--cache-dir", str(tmp_path / "mypy"), "src/parsers"],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
