# AGENTS.md — Agent Instructions

## Project Overview

job-harness is a Job Search OS and Codex plugin. It is NOT a tool for humans to use directly — it is the runtime environment in which an AI agent searches for jobs on behalf of a human.

The agent receives a natural-language job search request, translates it into MCP tool calls, runs them, analyzes results, and presents findings.

## Philosophy

Job aggregators are middlemen that create a search bubble. Many companies post vacancies only on their own career pages — never on aggregators. Finding a vacancy directly on a company's site and applying there is a strong signal of genuine interest. The agent should break out of the aggregator bubble whenever possible.

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

- **Commands**: `/job-search`, `/job-resolve`, `/job-contribute`
- **Skills**: `user-briefing`, `employer-resolution`, `aggregator-scrapers`, `scraper-insights`
- **Agent**: `job-searcher` — full automated workflow
- **MCP tools**: `search`, `resolve`, `resolve_company`, `list_sources`, `cache_get`, `cache_upsert`, `cache_diff`, `cache_stats`

The Python CLI still works standalone: `uv run job-harness search --query "QA" --resolve --cache`
