"""The brief form's source rules (app.js) keep what the form cannot show and never send refused rules."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SOURCES = "const sources=[{id:'hh_ru',group:'aggregator'},{id:'getmatch',group:'it_board'}];\n"
EXPORTS = "\nmodule.exports={ruleSources,sourceRules,rulesEmpty,rulesReachBeyondBuiltIns};\n"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_form_rules_round_trip_and_stay_valid(tmp_path):
    app = (ROOT / "src/web/static/app.js").read_text()
    start = app.index("const isBuiltIn")
    end = app.index("\n", app.index("\n", app.index("const rulesReachBeyondBuiltIns")) + 1)
    module = tmp_path / "rules.js"
    module.write_text(SOURCES + app[start:end] + EXPORTS)
    result = subprocess.run(
        ["node", str(ROOT / "tests/web/source_rules_form.js"), str(module)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
