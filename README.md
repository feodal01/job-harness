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
- Filtering by experience, remote, keywords (context-aware — "nice to have" is not a requirement)
- Employer resolution: aggregator listings → direct employer career pages
- Per-company career scrapers (VK, IBS)
- Bundled employer career page registry, updated with plugin releases
- MCP server with 7 tools for Claude Code and Codex integration
- Slash commands: `/job-search`, `/job-resolve`
- Output in Markdown / JSON / CSV
- Stealth browser via rebrowser-playwright

## Installation

### As a Claude Code plugin from GitHub

Install the marketplace from GitHub, then install the plugin:

```bash
claude plugin marketplace add feodal01/job-harness
claude plugin install job-harness@job-harness
```

The plugin provides MCP tools (`search`, `resolve`, `resolve_company`, `list_sources`, `cache_get`, `cache_upsert`, `cache_stats`), slash commands (`/job-search`, `/job-resolve`), and skills (`user-briefing`, `employer-resolution`, `aggregator-scrapers`, `scraper-insights`).

### As a Codex plugin from GitHub

Install the marketplace from GitHub, then install the plugin:

```bash
codex plugin marketplace add https://github.com/feodal01/job-harness --ref main
codex plugin add job-harness@job-harness
```

Codex uses the MCP tools and skills. Claude Code slash commands and agents remain available through Claude Code.

### Local development install

1. Clone the repo:
   ```bash
   git clone https://github.com/feodal01/job-harness.git
   cd job-harness
   ```

2. Install dependencies and Playwright browser:
   ```bash
   uv sync
   uv run python -m rebrowser_playwright install chromium
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
uv sync
uv run python -m rebrowser_playwright install chromium
uv run job-harness search --query "product manager" --remote-only --format json
```

## Usage

### Plugin commands

- `/job-search` — Full workflow: brief → search → resolve → filter → present
- `/job-resolve` — Resolve aggregator listings to employer career pages

### CLI commands

```bash
# Search with employer resolution
uv run job-harness search --query "QA engineer" --detail --resolve --cache --format json -o results.json

# Resolve from saved results
uv run job-harness resolve --input-file results.json --query "QA engineer" --cache

# List available scrapers
uv run job-harness list-sources
```

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
