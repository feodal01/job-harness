# Cross-Host Installation and Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make job-harness a consistently versioned Codex, Claude Code, and Cursor plugin with accurate installation and update documentation.

**Architecture:** Keep `plugins/job-harness` as the sole runtime plugin root and add only host metadata around the shared skills, commands, agents, and MCP configuration. Add one deterministic repository checker for packaging and a separate README contract test so release metadata and user instructions fail independently when they drift.

**Tech Stack:** JSON host manifests, Markdown, Python 3.12 standard library (`json`, `tomllib`, `unittest`), existing repository verification scripts.

## Global Constraints

- The shared release version is exactly `0.5.1`.
- Do not duplicate runtime files outside `plugins/job-harness`.
- Do not add compatibility shims or legacy fallback paths.
- Cursor local installation target is exactly `$HOME/.cursor/plugins/local/job-harness`.
- Codex and Claude Code marketplace commands remain unchanged.
- Commit and push actions remain gated by explicit user approval.

---

### Task 1: Enforce the cross-host packaging contract

**Files:**
- Create: `scripts/check_plugin_packaging.py`
- Create: `plugins/job-harness/tests/test_plugin_packaging.py`
- Create: `.cursor-plugin/marketplace.json`
- Create: `plugins/job-harness/.cursor-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `plugins/job-harness/.claude-plugin/plugin.json`
- Modify: `plugins/job-harness/.mcp.json`
- Modify: `scripts/verify_repo.py`

**Interfaces:**
- Consumes: the repository root containing host manifests, `plugins/job-harness/pyproject.toml`, and `plugins/job-harness/uv.lock`.
- Produces: `validate_packaging(root: Path) -> tuple[str, ...]`, returning an empty tuple for a valid package and concrete error strings otherwise.

- [ ] **Step 1: Write the failing packaging test**

```python
from __future__ import annotations

import runpy
import unittest
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECKER = runpy.run_path(str(_REPO_ROOT / "scripts" / "check_plugin_packaging.py"))


def _call(name: str, *args: Any) -> Any:
    value = _CHECKER[name]
    if not callable(value):
        raise AssertionError(f"{name} is not callable")
    return value(*args)


class PluginPackagingTest(unittest.TestCase):
    def test_repository_packaging_is_valid_for_all_hosts(self) -> None:
        self.assertEqual((), _call("validate_packaging", _REPO_ROOT))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add the checker skeleton and verify RED**

Create `scripts/check_plugin_packaging.py` with `validate_packaging()` reading required files and reporting missing manifests before reading their contents. Run:

```bash
uv --directory plugins/job-harness run python tests/test_plugin_packaging.py -v
```

Expected: FAIL because `.cursor-plugin/marketplace.json`, `plugins/job-harness/.cursor-plugin/plugin.json`, and explicit Claude version metadata are absent.

- [ ] **Step 3: Implement the complete packaging checker**

The checker must:

```python
REQUIRED_JSON_PATHS = (
    ".agents/plugins/marketplace.json",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
    "plugins/job-harness/.codex-plugin/plugin.json",
    "plugins/job-harness/.claude-plugin/plugin.json",
    "plugins/job-harness/.cursor-plugin/plugin.json",
)
EXPECTED_COMPONENT_PATHS = {
    "skills": "./skills/",
    "commands": "./commands/",
    "agents": "./agents/",
    "mcpServers": "./.mcp.json",
}
```

It must parse JSON, `pyproject.toml`, and `uv.lock`; require every marketplace and plugin name to be `job-harness`; require Claude and Cursor marketplace entries to use `source: "./plugins/job-harness"`; require version `0.5.1` in all three plugin manifests, both versioned marketplace entries, `pyproject.toml`, and the editable `job-harness` lock package; and require the Cursor component paths above.

`main()` prints each error to stderr and returns `1`, or prints `plugin packaging ok: version 0.5.1` and returns `0`.

- [ ] **Step 4: Add the missing Cursor and Claude metadata**

Create `.cursor-plugin/marketplace.json` with marketplace name `job-harness`, source `./plugins/job-harness`, version `0.5.1`, and the existing user-facing plugin description.

Create `plugins/job-harness/.cursor-plugin/plugin.json` with name/display name, version `0.5.1`, description, author, repository, license, keywords, and all four `EXPECTED_COMPONENT_PATHS` fields.

Add `"version": "0.5.1"` to `plugins/job-harness/.claude-plugin/plugin.json` and to the `job-harness` entry in `.claude-plugin/marketplace.json`.

Update the shared MCP launcher to resolve `CURSOR_PLUGIN_ROOT` before
`PLUGIN_ROOT` and `CLAUDE_PLUGIN_ROOT`.

- [ ] **Step 5: Wire the checker into repository lint verification**

Add this command to the beginning of `scripts/verify_repo.py::_run_lint()`:

```python
[sys.executable, "scripts/check_plugin_packaging.py"]
```

- [ ] **Step 6: Verify GREEN for packaging**

```bash
uv --directory plugins/job-harness run python tests/test_plugin_packaging.py -v
python3 scripts/check_plugin_packaging.py
claude plugin validate .
claude plugin validate plugins/job-harness
```

Expected: the unittest passes, the checker reports version `0.5.1`, and both Claude validations pass without the prior missing-version warning.

### Task 2: Align README installation and update workflows

**Files:**
- Create: `plugins/job-harness/tests/test_readme_installation.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the supported host package layout from Task 1.
- Produces: user instructions for installing and updating Codex, Claude Code, and Cursor without conflating Cursor plugin use with repository development.

- [ ] **Step 1: Write the failing README contract test**

```python
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
```

- [ ] **Step 2: Verify RED for README**

```bash
uv --directory plugins/job-harness run python tests/test_readme_installation.py -v
```

Expected: FAIL because the README says Cursor has no separate plugin installation and does not target the local plugin directory.

- [ ] **Step 3: Rewrite the Cursor installation section**

Replace the checkout-as-install flow with:

```bash
tmp_dir="$(mktemp -d)"
git clone --depth 1 https://github.com/feodal01/job-harness.git "$tmp_dir/job-harness"
mkdir -p "$HOME/.cursor/plugins/local"
rm -rf "$HOME/.cursor/plugins/local/job-harness"
cp -R "$tmp_dir/job-harness/plugins/job-harness" "$HOME/.cursor/plugins/local/job-harness"
rm -rf "$tmp_dir"
uv --directory "$HOME/.cursor/plugins/local/job-harness" sync
```

Tell the user to run **Developer: Reload Window**, open Cursor Agent chat, and ask for a job search. Explain that Cursor loads the plugin bundle from its local plugin directory.

- [ ] **Step 4: Rewrite the Cursor update section and preserve development guidance**

Use the same clone-and-replace commands for updates, followed by `uv sync` and **Developer: Reload Window**. Explain that Claude-imported MCP entries and the locally installed Cursor plugin are separate copies and should not both run the same MCP server. Keep `git pull` plus plugin `uv sync` only under a clearly labeled local repository development note.

- [ ] **Step 5: Verify GREEN for README**

```bash
uv --directory plugins/job-harness run python tests/test_readme_installation.py -v
```

Expected: both README contract tests pass.

### Task 3: Run the complete deterministic verification set

**Files:**
- Verify only; no new files expected.

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: fresh evidence that packaging, documentation, and the existing v2 engine remain valid.

- [ ] **Step 1: Run focused tests and packaging validators**

```bash
uv --directory plugins/job-harness run python tests/test_plugin_packaging.py -v
uv --directory plugins/job-harness run python tests/test_readme_installation.py -v
python3 scripts/check_plugin_packaging.py
claude plugin validate .
claude plugin validate plugins/job-harness
```

Expected: exit `0` with no warnings.

- [ ] **Step 2: Run repository gates**

```bash
python3 scripts/verify_repo.py lint
python3 scripts/verify_repo.py types
python3 scripts/verify_repo.py tests
python3 scripts/verify_v2.py --skip-live
git diff --check
```

Expected: all commands exit `0`. The repository-wide secret profile has a
pre-existing baseline failure on captured scraper fixtures, so run
`detect-secrets-hook --baseline .secrets.baseline` separately on every changed
file and require that targeted scan to exit `0`.

- [ ] **Step 3: Review the final diff and request commit approval**

```bash
git status --short
git diff --stat HEAD
git diff HEAD -- README.md .claude-plugin/marketplace.json .cursor-plugin/marketplace.json \
  plugins/job-harness/.claude-plugin/plugin.json \
  plugins/job-harness/.cursor-plugin/plugin.json \
  plugins/job-harness/.mcp.json \
  scripts/check_plugin_packaging.py scripts/verify_repo.py \
  plugins/job-harness/tests/test_plugin_packaging.py \
  plugins/job-harness/tests/test_readme_installation.py
```

Present the verification results and request explicit approval before committing implementation changes. Request separate approval before pushing.
