# Cross-Host Installation and Update Design

## Goal

Make job-harness installation and update behavior consistent across Codex,
Claude Code, and Cursor, using the verified packaging model from
`genai-feature-development` while preserving job-harness-specific runtime
components.

## Scope

The change covers user-facing installation and update documentation, host
plugin manifests, marketplace metadata, and deterministic validation of the
cross-host package. It does not change search behavior, scraper contracts, or
the v1/v2 runtime.

## Packaging Model

`plugins/job-harness` remains the only runtime plugin root. Each supported host
gets metadata inside or alongside that root:

- Codex continues to use `.codex-plugin/plugin.json` and the repository's
  `.agents/plugins/marketplace.json`.
- Claude Code continues to use `.claude-plugin/plugin.json` and the repository's
  `.claude-plugin/marketplace.json`.
- Cursor gains `.cursor-plugin/plugin.json` inside the plugin root and
  `.cursor-plugin/marketplace.json` at the repository root.

All version-bearing manifests and marketplace entries use `0.5.1`, matching
the current Codex manifest, `pyproject.toml`, and `uv.lock`. The Cursor manifest
declares the existing skills, commands, agents, and MCP configuration from the
same plugin root rather than duplicating runtime files. The shared MCP launcher
resolves `CURSOR_PLUGIN_ROOT` before the Codex and Claude plugin-root variables
so a Cursor install does not accidentally use the open workspace as its runtime
root.

## User Workflows

Codex and Claude Code retain their current marketplace install and update
commands because the installed CLIs confirm those commands.

Cursor becomes a real local-plugin workflow. Installation clones the repository
into a temporary directory and copies `plugins/job-harness` to
`$HOME/.cursor/plugins/local/job-harness`. Updating repeats that replacement and
then instructs the user to run **Developer: Reload Window**. A repository
checkout with `git pull` remains documented only as a development workflow, not
as the normal Cursor installation path.

Claude Code documentation keeps the `/plugins` auto-update flow and the
`claude plugin update job-harness` command. Its plugin and marketplace metadata
gain the explicit shared version so release behavior follows the same semver
model as the reference project.

## Validation

A deterministic packaging check will fail when:

- a required Codex, Claude Code, or Cursor manifest is missing;
- plugin or marketplace names do not equal `job-harness`;
- version-bearing manifests, marketplace entries, `pyproject.toml`, or the
  editable `job-harness` package in `uv.lock` disagree;
- required host component paths do not point to the shared plugin directories
  and `.mcp.json`.

The check will be introduced test-first: the initial test must fail against the
current package because Cursor manifests and Claude version metadata are absent.
After implementation, validation includes the focused packaging test, Claude
manifest validation, `python3 scripts/verify_v2.py --skip-live`, and
`git diff --check`.

## Compatibility and Failure Handling

No compatibility shim or duplicate plugin root is introduced. A missing or
inconsistent manifest is a release error and fails validation explicitly. The
README will not claim that opening the repository in Cursor installs the
user-facing plugin.
