# CLAUDE.md — Agent Instructions

## Project Overview

job-harness is a Job Search OS. It is NOT a tool for humans to use directly — it is the runtime environment in which an AI agent searches for jobs on behalf of a human.

The agent receives a natural-language job search request, translates it into CLI commands, runs them, analyzes results, and presents findings.

## Philosophy

Job aggregators are middlemen that create a search bubble. Many companies post vacancies only on their own career pages — never on aggregators. Finding a vacancy directly on a company's site and applying there is a strong signal of genuine interest. The agent should break out of the aggregator bubble whenever possible.

## Architecture

```
src/job_harness/
├── models.py        # SearchParams, JobListing, SearchResults
├── base.py          # BaseScraper ABC (search + fetch_detail)
├── registry.py      # @register_scraper decorator, scraper discovery
├── browser.py       # Stealth browser factory (rebrowser-playwright)
├── filters.py       # Callable-based filter system
├── formatters.py    # Markdown, JSON, CSV output
├── cli.py           # CLI entry point (search, list-sources)
└── scrapers/
    ├── hh_ru.py     # hh.ru scraper
    └── habr_career.py  # Habr Career scraper
```

## Protocols & Detailed Instructions

Specific protocols and operational instructions live in `.claude/protocols/`:

- [Aggregator Scrapers](.claude/protocols/aggregator-scrapers.md) — using and maintaining hh.ru, Habr Career, and future aggregator scrapers

## Agent Workflow

When a user asks to find jobs:

1. **Parse intent** — extract query, experience level, remote preference, location, keywords to exclude
2. **Choose sources** — start with `--sources all`, or specific ones if user mentioned a platform
3. **Run search** — always use `--detail` to get full descriptions for filtering
4. **Apply filters** — use `--exclude-keywords` and `--exclude-keywords-context` for smart filtering
5. **Output as JSON** — use `--format json` so you can programmatically analyze results
6. **Analyze & present** — read the JSON output, filter further if needed, present top matches with reasoning
7. **Iterate** — if results are insufficient, adjust query/filters and run again
