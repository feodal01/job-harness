# AGENTS.md — Agent Instructions

## Project Overview

job-harness is a Job Search OS and Codex plugin. It is NOT a tool for humans to use directly — it is the runtime environment in which an AI agent searches for jobs on behalf of a human.

The agent receives a natural-language job search request, translates it into MCP tool calls, runs them, analyzes results, and presents findings.

## Philosophy

Search broadly across all available sources: job aggregators, employer career pages, and company-specific career scrapers. Aggregators are useful, but they create a search bubble: some companies and vacancies appear only on employer sites, and aggregator applications may be filtered or auto-rejected before a recruiter sees them. Direct career-site applications often enter a smaller, more manually reviewed recruiting flow, especially when the company does not use a heavy automated ATS. The agent should break out of the aggregator bubble by finding direct employer pages and, when appropriate, presenting both the aggregator link and the employer link so the user can apply through both channels.

## Architecture

```
src/job_harness/
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

This repo is a Codex plugin with:

- **Commands**: `/job-search`, `/job-resolve`
- **Skills**: `user-briefing`, `employer-resolution`, `aggregator-scrapers`, `scraper-insights`
- **Agent**: `job-searcher` — full automated workflow
- **MCP tools**: `search`, `resolve`, `resolve_company`, `list_sources`, `cache_get`, `cache_upsert`, `cache_stats`

The Python CLI still works standalone: `uv run job-harness search --query "QA" --resolve --cache`
