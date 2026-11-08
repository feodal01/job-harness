"""The sources dialog's rules model (source-rules.js) keeps rules intact and never sends refused ones."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_rules_model_of_the_sources_dialog():
    result = subprocess.run(
        ["node", str(ROOT / "tests/web/source_rules_model.js"), str(ROOT / "src/web/static/source-rules.js")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
