# CLAUDE.md — Agent Instructions

## Project Overview

job-harness is a Job Search OS. It is NOT a tool for humans to use directly — it is the runtime environment in which an AI agent searches for jobs on behalf of a human.

The agent receives a natural-language job search request, translates it into CLI commands, runs them, analyzes results, and presents findings.

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

## How to Add a New Scraper

1. Create `src/job_harness/scrapers/<platform>.py`
2. Subclass `BaseScraper`, implement `search()` and `fetch_detail()`
3. Decorate with `@register_scraper("platform_name")`
4. Import in `src/job_harness/scrapers/__init__.py`

That's it. The registry auto-discovers it. No changes to CLI or any other file needed.

## CLI Reference

```bash
# Search
uv run job-harness search --query "..." --sources hh_ru,habr_career --remote-only --experience middle --max-results 20 --detail --format json --output results.json

# Exclude keywords (with context-aware exceptions)
uv run job-harness search --query "..." --exclude-keywords "python,java" --exclude-keywords-context "плюсом,желательн"

# From preset
uv run job-harness search --preset configs/qa_manual_remote.yaml

# List available scrapers
uv run job-harness list-sources
```

## Agent Workflow

When a user asks to find jobs:

1. **Parse intent** — extract query, experience level, remote preference, location, keywords to exclude
2. **Choose sources** — start with `--sources all`, or specific ones if user mentioned a platform
3. **Run search** — always use `--detail` to get full descriptions for filtering
4. **Apply filters** — use `--exclude-keywords` and `--exclude-keywords-context` for smart filtering
5. **Output as JSON** — use `--format json` so you can programmatically analyze results
6. **Analyze & present** — read the JSON output, filter further if needed, present top matches with reasoning
7. **Iterate** — if results are insufficient, adjust query/filters and run again

## Key Conventions

- Filters check `description` and `requirements` fields, NOT `skills`. This is intentional — skills listed don't mean they're required.
- `--exclude-keywords-context` is crucial for Russian job market where "будет плюсом" (nice to have) is common.
- Always use `--headless` (default). Use `--no-headless` only for debugging selectors.
- The `raw` dict on JobListing holds platform-specific data that doesn't map to universal fields.
- Experience normalization: raw text → "junior" | "middle" | "senior". See `BaseScraper.normalize_experience()`.

## Current Limitations

- Only Russian job platforms (hh.ru, Habr Career)
- No application tracking yet
- Detail fetching is sequential (one page at a time)
- No deduplication across sources
