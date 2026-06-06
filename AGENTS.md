# AGENTS.md — Agent Instructions

## Project Overview

job-harness is a Job Search OS packaged as a Codex and Claude Code plugin. Cursor can use this repository-level `AGENTS.md` for maintenance work, but the installable runtime is the plugin under `plugins/job-harness`.

It is NOT a tool for humans to use directly — it is the runtime environment in which an AI agent searches for jobs on behalf of a human.

The agent receives a natural-language job search request, translates it into MCP tool calls, runs them, analyzes results, and presents findings.

## Philosophy

Search broadly across all available sources: job aggregators, employer career pages, and company-specific career scrapers. Aggregators are useful, but they create a search bubble: some companies and vacancies appear only on employer sites, and aggregator applications may be filtered or auto-rejected before a recruiter sees them. Direct career-site applications often enter a smaller, more manually reviewed recruiting flow, especially when the company does not use a heavy automated ATS. The agent should break out of the aggregator bubble by finding direct employer pages and, when appropriate, presenting both the aggregator link and the employer link so the user can apply through both channels.

## Architecture

```
plugins/job-harness/
├── .codex-plugin/plugin.json   # Codex plugin manifest
├── .claude-plugin/plugin.json  # Claude Code plugin manifest
├── .mcp.json                   # MCP server config
├── commands/                   # Claude Code slash commands
├── agents/                     # Claude Code agent definitions
├── skills/                     # Shared agent skills
├── scripts/                    # MCP server and artifact initialization helper
├── data/
│   └── company-careers-public.json # bundled registry shipped with releases
└── src/job_harness/
├── models.py            # SearchParams, JobListing, SearchResults
├── base.py              # BaseScraper ABC (search + fetch_detail)
├── registry.py          # @register_scraper decorator, scraper discovery
├── browser.py           # Stealth browser factory (rebrowser-playwright)
├── filters.py           # Callable-based filter system
├── formatters.py        # Markdown, JSON, CSV output
├── employer_resolver.py # Resolve aggregator listings to direct employer pages
├── employer_cache.py    # JSON cache of company → career page mappings
├── cli.py               # CLI entry point (search, resolve, list-sources)
└── scrapers/
    ├── hh_ru.py         # hh.ru scraper
    ├── habr_career.py   # Habr Career scraper
    └── career/          # Per-company career site scrapers
        ├── base.py      # BaseCareerScraper ABC + registry
        ├── vk.py        # ВКонтакте (team.vk.company)
        └── ibs.py       # IBS (ibs.ru/career)
```

## Plugin Components

This repo has one real plugin root: `plugins/job-harness`. Do not duplicate plugin runtime files at the repository root.

The plugin includes:

- **Commands**: `/job-search`, `/job-resolve`
- **Skills**: `user-briefing`, `employer-resolution`, `aggregator-scrapers`, `scraper-insights`
- **Agent**: `job-searcher` — full automated workflow
- **MCP tools (search surface)**: `search_start`, `search_status`, `search_results`, `search_cancel`, `search_refine`, `list_active_runs`
- **MCP tools (lookup)**: `list_sources`, `search_company_jobs`, `cache_get`, `cache_upsert`, `cache_stats`

**Search workflow:** `search_start` → poll `search_status` → `search_results(run_id)` (default writes `results.json` and returns `{ path }`). Use `format=inline` for previews (max 20 listings per call). Employer resolution: `job-harness resolve`.

The Python CLI still works standalone from the plugin root: `uv --directory plugins/job-harness run job-harness search --query "QA" --resolve --cache`

## Verification

Run the canonical repository gate from the repo root before handing off code changes:

`python scripts/verify_repo.py full`

The full profile runs Ruff linting, mypy type checking, detect-secrets baseline scanning, and the plugin unit test suite.
