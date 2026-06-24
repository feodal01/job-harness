# CLAUDE.md — Claude Code Instructions

Follow the shared repository instructions in `AGENTS.md`.

Claude Code-specific note: this repository is a plugin marketplace at the root, but the installable plugin source is `plugins/job-harness`. Do not recreate plugin commands, runtime skills, scripts, MCP config, or Python source at the repository root. Repository-local development skills may live under `.agents/skills`; they are not part of the installable plugin runtime.

The plugin is in active early development. Do not add compatibility shims,
legacy fallbacks, backward-compatible adapters, or code paths whose purpose is
preserving old behavior. Change contracts directly and update callers, fixtures,
and tests in the same patch.
