---
name: job-harness-scraper-development
description: Project-local development skill for maintaining, fixing, or adding job-harness scrapers and scraper tests. Use inside this repository when Codex changes scraper code, source capabilities, parser fixtures, source outcome classification, live smoke checks, or scraper-related documentation.
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

- Treat live runs as debugging, smoke, or drift evidence, not merge proof.
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
5. Put generic transport/runtime classification in shared detectors and G5 tests.
   Put source-specific parser/classifier behavior in G2 only when backed by real
   captured artifacts.
6. Run the repository verification gate before handoff.

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
  `max_results`.

## Useful Patterns

Use `references/scrapers.md` for:

- fallback selector chains;
- dynamic `data-*` attribute handling;
- over-broad selector cleanup;
- anti-bot and anti-abuse redirects;
- `__NEXT_DATA__`, JSON-LD, and public API extraction;
- Bitrix SEF filter URLs;
- browser isolated-world limitations;
- country-aware source metadata;
- specialization/category APIs.
