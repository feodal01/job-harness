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
├── commands/                   # Claude Code slash command entrypoints
├── agents/                     # Claude Code agent entrypoints
├── skills/                     # Runtime skills and canonical workflows shipped with the plugin
├── scripts/                    # MCP server and artifact initialization helper
├── data/
│   └── company-careers-public.json # bundled registry shipped with releases
└── src/job_harness/
├── models.py            # SearchParams, RawListing, RawSearchRecord, JobListing, SearchResults
├── base.py              # BaseScraper ABC (search + fetch_detail)
├── registry.py          # @register_scraper decorator, strict source catalog
├── browser.py           # Stealth browser factory (rebrowser-playwright)
├── filters.py           # Callable-based filter system
├── formatters.py        # Markdown, JSON, CSV output
├── result_pipeline.py   # Downstream filtering/dedupe/ranking from raw listings
├── source_runtime.py    # Engine-level source timeout/retry policy
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

Development-only skills may live under `.agents/skills`. They are repository
maintenance guidance and must not be treated as plugin runtime skills.
When maintaining scraper code, source contracts, parser fixtures, or scraper
tests, read `.agents/skills/job-harness-scraper-development/SKILL.md`; its
references include the scraper testing policy and experience-level source
policy.

The plugin includes:

- **Commands**: `/job-search`, `/job-resolve`
- **Runtime skills**: `job-search-workflow`, `user-briefing`, `employer-resolution`
- **Development skills**: `.agents/skills/job-harness-scraper-development`
- **Agent**: `job-searcher` — Claude Code entrypoint for the full automated workflow
- **MCP tools (search surface)**: `search_start`, `search_status`, `search_results`, `search_cancel`, `search_refine`, `search_retry`, `list_active_runs`
- **MCP tools (lookup)**: `list_sources`, `search_company_jobs`, `cache_get`, `cache_upsert`, `cache_stats`

`plugins/job-harness/skills/job-search-workflow/SKILL.md` is the canonical
search workflow. `plugins/job-harness/agents/job-searcher.md` and
`plugins/job-harness/commands/job-search.md` must remain thin entrypoints and
must not duplicate the skill workflow text.

**Search workflow:** call `list_sources` first to inspect exact source ids, groups, server-supported criteria, and source limits. Then run `search_start` → poll `search_status` → `search_results(run_id)` (default writes downstream `results.json` and returns `{ path }`). Use `format=inline` for previews (max 20 listings per call). Raw search evidence is written separately to `raw_search.jsonl`; it is not filtered, ranked, deduped, grade-estimated, or globally capped by `max_results`.

New search-layer features to preserve:

- Exact source selection via `sources` and semantic source selection via `source_groups`.
- Per-source raw collection limits from the source catalog; `max_results` only caps downstream presentation.
- Server-only `salary_from` and `freshness_days`; unsupported sources still collect raw listings and report unsupported criteria in source summaries.
- Source-level retry for transient zero-listing failures; source summaries record `attempts`, `retries`, `limit_reached`, `server_criteria_used`, and `unsupported_requested_criteria`.

The Python CLI still works standalone from the plugin root: `uv --directory plugins/job-harness run job-harness search --query "QA" --resolve --cache`

## Verification

Run the canonical repository gate from the repo root before handing off code changes:

`python scripts/verify_repo.py full`

The full profile runs Ruff linting, mypy type checking, detect-secrets baseline scanning, and the plugin unit test suite.
