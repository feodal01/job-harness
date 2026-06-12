# job-harness

Job Search OS — an agent-first approach to job hunting in 2026.

A scalpel, not a shotgun. Precision vacancy search tailored to your request, not mass auto-apply spam.

## Why

Job aggregators are useful, but they create a search bubble: you only see the companies and vacancies that made it into that aggregator. Many companies also maintain their own career pages, and some roles never appear on job boards at all. Job-harness searches across available sources — aggregators, employer career pages, and company-specific career scrapers — so the agent can find companies and vacancies outside the aggregator bubble. Applying through both the aggregator and the employer site can also improve the chance that a recruiter sees you: aggregator applications are often filtered automatically, while career-site applications may enter a smaller, more manually reviewed flow when the company does not use a heavy automated ATS.

## Who is this for

This repo is not designed for manual use. It's an OS where an AI agent works — searching for jobs on your behalf. You describe what you're looking for, the agent does the rest.

Works best as an agent plugin for **Claude Code** or **OpenAI Codex**.

## Features

- Job search on hh.ru and Habr Career
- Detailed parsing of descriptions and skills
- Exact grade filtering by `experience_levels`, with native/estimated/unknown grade assessment
- Employer resolution: aggregator listings → direct employer career pages
- Known company career-page probing through the normal search pipeline
- Per-company career scrapers (VK, IBS)
- Bundled employer career page registry, updated with plugin releases
- Strict source catalog via `list_sources`: exact source ids, source groups (`aggregator`, `company_career`, `directory`, `other`), server-supported criteria, and per-source raw limits
- Named source selection with `sources=hh_ru,career:vk` and group selection with `source_groups=["aggregator"]` / `--source-groups aggregator`
- Raw exhaustive search artifact: `raw_search.jsonl` stores unranked, undeduped, unfiltered source facts before downstream analysis
- Presentation export separation: `results.json` is the downstream filtered/deduped/ranked export, capped by `max_results`; raw scraping is capped by each source's `source_limit`, not by `max_results`
- Server-only search criteria for `salary_from` and `freshness_days`; unsupported sources keep all raw listings and report unsupported criteria in source summaries
- Source-level retry for transient zero-listing failures, with attempts/retries recorded per source
- MCP server with async search tools (`search_start`, `search_status`, `search_results`, …) for Claude Code and Codex integration
- Slash commands: `/job-search`, `/job-resolve`
- Output in Markdown / JSON / CSV
- Stealth browser via rebrowser-playwright

## Installation

The repository has one installable plugin root: `plugins/job-harness`. Codex and Claude Code marketplace files both point to that directory, so commands, skills, MCP tools, scripts, and Python code are not duplicated at the repository root.

### As a Claude Code plugin from GitHub

Install the marketplace from GitHub, then install the plugin:

```bash
claude plugin marketplace add feodal01/job-harness
claude plugin install job-harness@job-harness
```

The plugin provides MCP tools (`search_start`, `search_status`, `search_results`, `search_refine`, `list_sources`, `search_company_jobs`, `cache_get`, `cache_upsert`, `cache_stats`, …), slash commands (`/job-search`, `/job-resolve`), skills (`user-briefing`, `employer-resolution`, `aggregator-scrapers`, `scraper-insights`), and CLI commands including `job-harness resolve`.

### As a Codex plugin from GitHub

Install the marketplace from GitHub, then install the plugin:

```bash
codex plugin marketplace add https://github.com/feodal01/job-harness --ref main
codex plugin add job-harness@job-harness
```

Codex uses the MCP tools and skills. Claude Code slash commands and agents remain available through Claude Code.

### Cursor

Cursor does not install this plugin runtime directly. For repository maintenance, Cursor can use the shared `AGENTS.md` instructions at the repository root.

### Local development install

1. Clone the repo:
   ```bash
   git clone https://github.com/feodal01/job-harness.git
   cd job-harness
   ```

2. Install dependencies and Playwright browser:
   ```bash
   uv --directory plugins/job-harness sync
   uv --directory plugins/job-harness run python -m rebrowser_playwright install chromium
   ```

3. Use the local marketplace while developing:
   ```bash
   claude plugin marketplace add /path/to/job-harness
   claude plugin install job-harness@job-harness
   ```

   Or install the local marketplace in Codex:
   ```bash
   codex plugin marketplace add /path/to/job-harness
   codex plugin add job-harness@job-harness
   ```

### As a standalone CLI

```bash
uv --directory plugins/job-harness sync
uv --directory plugins/job-harness run python -m rebrowser_playwright install chromium
uv --directory plugins/job-harness run job-harness search --query "product manager" --remote-only --format json
```

## Usage

### Plugin commands

- `/job-search` — Full workflow: brief → search → resolve → filter → present
- `/job-resolve` — Resolve aggregator listings to employer career pages

### CLI commands

```bash
# Search with employer resolution
uv --directory plugins/job-harness run job-harness search --query "QA engineer" --detail --resolve --cache --format json -o results.json

# Resolve from saved results
uv --directory plugins/job-harness run job-harness resolve --input-file results.json --query "QA engineer" --cache

# List available scrapers
uv --directory plugins/job-harness run job-harness list-sources
```

### Search layer artifacts

Every MCP/CLI search now has two artifact layers:

- `raw_search.jsonl` — raw search evidence, one `RawSearchRecord` per line. It is not globally truncated, ranked, deduped, grade-estimated, or filtered.
- `results.json` — downstream export for presentation. It applies grade assessment, filters, dedupe, ordering, and `max_results`.

Use `list_sources` before a run to inspect exact source ids, groups, supported server criteria, and source limits. Use exact ids with `sources` or semantic groups with `source_groups`; if both are provided, the selected set is their union in registry order.

## Employer Resolution

The resolver finds direct employer career pages for aggregator listings:

1. Check the bundled registry and local cache (7-day freshness)
2. Search for `[Company] вакансии` / `[Company] careers`
3. Probe common career page paths on company domain
4. Match vacancy on career page using query synonyms
5. Save to cache for future searches

Resolution outcomes:
- **Direct vacancy found** → use employer's URL, tag source as `+direct`
- **Career page found** → link to career page, keep aggregator as primary
- **No career page** → keep aggregator URL (normal for small companies)

## What makes it different

- **Agent-first workflow** — the user describes the job search; the agent asks the right questions, runs the tools, filters results, and produces a concise report.
- **Aggregator bubble escape** — job-harness looks for direct employer career pages instead of stopping at job-board listings.
- **Russian-market focus** — built around hh.ru, Habr Career, and employer career sites relevant to this market.
- **Codex and Claude Code support** — usable as a plugin in both agent environments, with a standalone CLI for development and debugging.

## Status

job-harness currently focuses on search, filtering, employer resolution, and reusable search artifacts. Future releases can add more company-native scrapers, better ranking, deduplication across runs, and application tracking.
