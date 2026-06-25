---
name: job-harness-scraper-development
description: Project-local development skill for maintaining job-harness scrapers, plugin runtime surfaces, manifests, MCP/CLI entrypoints, agent-facing instructions, and tests. Use inside this repository when Codex changes scraper code, source capabilities, parser fixtures, source outcome classification, live smoke checks, plugin packaging, runtime skills, commands, agents, or scraper/plugin-related documentation.
---

# Job Harness Scraper Development

This is a repository development skill, not a plugin runtime skill. Keep it out
of `plugins/job-harness/skills/`; plugin skills are shipped for job-search
workflows, while this skill guides maintainers changing the project.

## Required Reading

Before changing scraper behavior or tests, read:

- `references/testing-policy.md` for merge gates, fixture rules, canonical
  outcomes, and G2/G4/G5 ownership.
- `references/experience-filtering.md` when changing `experience_levels`, grade
  assessment, source experience capability, or scrapers that expose native grade.
- `references/scrapers.md` only when practical scraper patterns are relevant.

## Development Rules

- The plugin is in active early development. Do not add compatibility shims,
  legacy fallbacks, backward-compatible adapters, or code paths whose purpose is
  preserving old behavior. Change the contract directly and update callers,
  fixtures, and tests in the same patch. Treat compatibility comments as a
  code smell; `scripts/check_no_compat_comments.py` enforces this for source
  comments.
- Bump the plugin version for every installable plugin runtime change. Update
  `plugins/job-harness/.codex-plugin/plugin.json`,
  `plugins/job-harness/pyproject.toml`, and the local `job-harness` package
  entry in `plugins/job-harness/uv.lock` together. Do not add version fields to
  host manifests unless that host's schema is known to support them.
- Develop the plugin for three agent surfaces: Claude Code, Codex, and Cursor.
  Claude Code uses `.claude-plugin/`, `commands/`, `agents/`, runtime skills,
  MCP config, and `CLAUDE_PLUGIN_ROOT`; Codex uses `.codex-plugin/`, runtime
  skills, MCP config, and deferred tool discovery; Cursor uses the repository
  root maintenance instructions (`AGENTS.md`) and CLI/repo workflows rather than
  installing the plugin runtime directly.
- For agent-facing interface or instruction changes, check every affected
  surface. If a change is runtime-shared, keep wording host-neutral. If a
  host-specific behavior is unavoidable, document the equivalent behavior or
  limitation for Claude Code, Codex, and Cursor in the appropriate
  host-specific file instead of putting a single-host assumption in a shared
  runtime skill.
- Treat live runs as debugging, smoke, or drift evidence, not merge proof.
- After changing scraper behavior, parser output, result post-processing,
  filtering, dedupe, presentation fields, or report rendering, run a live query
  that exercises the changed behavior and manually audit at least 10 affected
  cards when that many are available. For each audited card, open the source
  vacancy page, compare the source-visible facts and relevant structured or
  hidden source facts with what the user-facing report shows, and record the
  evidence before claiming the work is done. If fewer than 10 affected cards
  exist, audit every affected card and state the smaller sample size.
- Base source-specific parser fixtures on real captured source artifacts.
- Do not invent captcha, VPN, geo, login, no-result, or malformed source pages
  to make a source parser test pass.
- Keep unsupported requested criteria as source diagnostics. Do not skip a source
  unless explicit policy or strict mode says to skip it.
- Keep source-supported criteria binary: supported by native request parameters
  or structured source data, otherwise unsupported.
- Distinguish `success`, `no_results`, `partial_success`, block/rate-limit,
  timeout, network, parse, invalid-output, and resource failures according to
  the policy taxonomy.
- Reaching a configured source limit is normal completion with
  `limit_reached=true`, not `partial_success`.

## Working Flow

1. Inspect the source contract: id, countries, capabilities, source limit,
   transport, and supported/unsupported criteria.
2. Inspect existing tests and fixtures before editing scraper code.
3. Prefer structured APIs, SSR payloads, JSON-LD, and stable DOM markers over
   brittle rendered text.
4. Use live browser/debug runs only to understand or capture reality. Convert any
   parser-relevant finding into a deterministic G2 fixture before relying on it.
5. For scraper, post-processing, filtering, or presentation changes, perform the
   live affected-card audit from Development Rules before handoff. The audit must
   include direct source-page checks, not only inspecting generated report rows
   or SQLite payloads.
6. Put generic transport/runtime classification in shared detectors and G5 tests.
   Put source-specific parser/classifier behavior in G2 only when backed by real
   captured artifacts.
7. Run the repository verification gate before handoff.

## Adding Or Changing A Scraper

- Add or update the scraper under `plugins/job-harness/src/job_harness/scrapers/`.
- Register the source and declare explicit countries and capabilities.
- Test request mapping for every supported native criterion.
- Test real parser input for normal results and every real source state used by
  the parser.
- Assert unsupported requested criteria in diagnostics without fabricating or
  deleting raw listings.
- Ensure one broken card does not kill the whole source, but do not convert a
  structurally broken page into `success` or `no_results`.
- Strip tracking parameters from emitted vacancy URLs.
- Keep raw source facts separate from downstream filtering, ranking, dedupe, and
  presentation.
- Treat LinkedIn Job Wrapping workplace tags (`#LI-Remote`, `#LI-Hybrid`,
  `#LI-Onsite`) as valid source-exposed workplace signals when they appear in a
  real captured source artifact. Preserve them as dedicated raw facts, not as
  visible description text and not as generic `work_format`. Post-processing owns
  the precedence: explicit source fields or visible work-format text win over
  LinkedIn tags; if no explicit signal exists, LinkedIn tags may determine work
  format directly. Preserve multiple workplace tags instead of selecting the
  first one.
- Do not normalize country names or city names to ISO codes inside scrapers and
  do not keep source-local country or city-to-country mapping dictionaries. Emit
  the country/location text the source actually exposed, or `None` when the
  source did not expose it. Country, region, and city-derived geography
  normalization belongs in shared v2 post-processing/geography code.

## Useful Patterns

Use `references/scrapers.md` for:

- strict selector and parser maintenance;
- dynamic `data-*` attribute handling;
- over-broad selector cleanup;
- anti-bot and anti-abuse redirects;
- `__NEXT_DATA__`, JSON-LD, and public API extraction;
- Bitrix SEF filter URLs;
- browser isolated-world limitations;
- country-aware source metadata;
- LinkedIn Job Wrapping workplace tags;
- specialization/category APIs.
