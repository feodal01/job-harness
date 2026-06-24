# job-harness

Job Search OS — an agent-first approach to job hunting in 2026.

A scalpel, not a shotgun. Precision vacancy search tailored to your request, not mass auto-apply spam.

## Why

Job aggregators are useful, but they create a search bubble: you only see the companies and vacancies that made it into that aggregator. Many companies also maintain their own career pages, and some roles never appear on job boards at all. Job-harness searches across available sources — aggregators and employer career pages — so the agent can find companies and vacancies outside the aggregator bubble.

## Who is this for

This repo is not designed for manual use. It's an OS where an AI agent works — searching for jobs on your behalf. You describe what you're looking for, the agent does the rest.

Works best as an agent plugin for **Claude Code** or **OpenAI Codex**.

## Features (v2)

- Contract-first search engine under `src/job_harness/v2/`
- **14 implemented sources** — Russian/CIS aggregators plus VK and JetBrains career pages
- Strict source catalog via `job-harness-v2 list-sources` with per-source limits and criteria capabilities
- Raw corpus + processed export separation in `run.sqlite`: `raw_listings` vs `processed_results`
- Search criteria: query variants, grades, salary floor, freshness, remote/relocation, countries, cities, text exclusions
- Append mode to accumulate multiple query variants in one run
- Live verification gate: `python scripts/verify_v2.py` (full catalog e2e) or `python scripts/verify_v2.py --live-profile light` (bounded two-source e2e)
- Runtime skill `job-search-workflow` documents the canonical v2 agent workflow

Legacy v1 (MCP async search, browser pool, employer resolution, company-live-batch) remains available under `src/job_harness/v1/` for maintenance but is not the primary search path.

## Installation

The repository has one installable plugin root: `plugins/job-harness`. Codex and Claude Code marketplace files both point to that directory.

### Local development

```bash
git clone https://github.com/feodal01/job-harness.git
cd job-harness
uv --directory plugins/job-harness sync
```

### As a Claude Code / Codex plugin

See marketplace install instructions in prior releases. The plugin ships runtime skills, commands, and (for v1) an MCP server config.

### Cursor

Cursor can use repository-level `AGENTS.md` for maintenance. The installable runtime is `plugins/job-harness`.

## Usage (v2 CLI)

All v2 commands run from the plugin directory:

```bash
uv --directory plugins/job-harness run job-harness-v2 list-sources
```

### Search

```bash
uv --directory plugins/job-harness run job-harness-v2 search \
  --queries "QA | AQA | SDET | Quality Assurance" \
  --grade middle \
  --salary-from 150000 \
  --country RU \
  --max-results 20
```

Stdout is a single JSON object (`record_type: v2_search_execution`) with `run_id`, `run.sqlite` path, per-source `attempts`, and `processed_result_count`.

Default artifact root: `.job-harness/v2/runs/<run_id>/`

### Append another query to the same run

```bash
uv --directory plugins/job-harness run job-harness-v2 search \
  --queries "тестировщик | инженер по тестированию" \
  --append-to-run-id "<run_id>" \
  --max-results 20
```

## v2 search parameters

| Flag | Description |
|------|-------------|
| `--query` | Search text; **repeatable** for multiple variants |
| `--queries` | Pipe-separated query variants, for example `"QA \| AQA \| SDET"`; repeatable |
| `--grade` | `intern`, `junior`, `middle`, `senior`, `lead`; repeatable |
| `--salary-from` | Minimum salary (integer) |
| `--published-since` | ISO date `YYYY-MM-DD` |
| `--exclude-company` | Company name substring to exclude; repeatable |
| `--exclude-text` | Substring exclusion applied in post-processing; repeatable |
| `--exclude-regex` | Regex exclusion; repeatable |
| `--relocation` | `true` or `false` |
| `--remote-in-country` | `true` or `false` |
| `--remote-global` | `true` or `false` |
| `--country` | ISO country code (`RU`, `AM`); repeatable; filters catalog-eligible sources |
| `--city` | City name; repeatable |
| `--max-results` | Cap on **processed** export (default 20); does not cap raw scraping |
| `--source` | Exact source id; repeatable; omit to search all implemented sources |
| `--source-type` | `aggregator` or `company_career`; repeatable |
| `--append-to-run-id` | Append to an existing run corpus |
| `--run-id` | Optional explicit run id |
| `--runs-dir` | Artifact directory (default `.job-harness/v2/runs`) |
| `--source-attempt-timeout` | Per-source timeout in seconds (default 30) |
| `--run-timeout` | Orchestrator timeout in seconds (default 120) |
| `--fetch-timeout` | HTTP fetch timeout in seconds (default 15) |
| `--retry-attempts` | Source retry count (default 1) |

Run `list-sources` to see which criteria each source supports natively vs in post-processing.

## Supported v2 sources

| source_id | type | raw limit | countries |
|-----------|------|-----------|-----------|
| `habr_career` | aggregator | 50 | RU |
| `hh_ru` | aggregator | 100 | RU |
| `talanto` | aggregator | 50 | — |
| `career:vk` | company_career | 25 | RU |
| `career:jetbrains` | company_career | 120 | — |
| `geekjob` | aggregator | 50 | — |
| `talento` | aggregator | 50 | — |
| `finder_work` | aggregator | 100 | — |
| `getmatch` | aggregator | 100 | — |
| `it_jobs_uz` | aggregator | 100 | — |
| `hirify` | aggregator | 100 | — |
| `jobturbo` | aggregator | 50 | — |
| `hirehi` | aggregator | 50 | RU |
| `staff_am` | aggregator | 100 | AM |

## Artifact layout

Each run under `.job-harness/v2/runs/<run_id>/`:

| File | Purpose |
|------|---------|
| `run.sqlite` | Durable run database with `raw_listings`, `source_attempts`, `run_manifest`, and `processed_results` tables |
| `report.html` | Self-contained interactive report generated from `processed_results` |

## Verification

```bash
# v2 gate (deterministic + optional live e2e)
python scripts/verify_v2.py
python scripts/verify_v2.py --live-profile light
python scripts/verify_v2.py --skip-live

# Repository gate (lint, types, v1+v2 unit tests, optional v1 live smokes)
python scripts/verify_repo.py full
```

## Agent workflow

The canonical workflow for AI agents is `plugins/job-harness/skills/job-search-workflow/SKILL.md`. It describes briefing, `list-sources`, `search`, append, artifact handling, and presentation — all through **`job-harness-v2`**.

Claude Code `/job-search` and `agents/job-searcher.md` are thin entrypoints that delegate to that skill.

Scraper development guidance lives in `.agents/skills/job-harness-scraper-development` (repository maintenance only).

## Repository layout

```
plugins/job-harness/
├── src/job_harness/
│   ├── v2/          # Contract-first search engine (primary)
│   └── v1/          # Legacy MCP/browser/employer tooling
├── skills/          # Runtime agent skills (v2 workflow)
├── commands/        # Claude Code slash commands
├── agents/          # Claude Code agent entrypoints
├── scripts/         # MCP server (v1), helpers
└── tests/
    ├── v1/          # Legacy engine tests
    └── v2/          # Contract-first engine tests
```

## Status

v2 is the primary search surface for new work: 14 contract-first sources, fixture-backed parsers, and full/light live e2e profiles. v1 remains for MCP compatibility and legacy tooling under `src/job_harness/v1/`.
