# CLAUDE.md — Agent Instructions

## Project Overview

job-harness is a Job Search OS. It is NOT a tool for humans to use directly — it is the runtime environment in which an AI agent searches for jobs on behalf of a human.

The agent receives a natural-language job search request, translates it into CLI commands, runs them, analyzes results, and presents findings.

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

## Protocols & Detailed Instructions

Specific protocols and operational instructions live in `.claude/protocols/`:

- [User Briefing](.claude/protocols/user-briefing.md) — how to collect search parameters from the user at session start
- [Aggregator Scrapers](.claude/protocols/aggregator-scrapers.md) — using and maintaining hh.ru, Habr Career, and future aggregator scrapers
- [Employer Resolution](.claude/protocols/employer-resolution.md) — resolving aggregator listings to direct employer career pages

## Experience

Reusable insights from solving real problems. When you overcome a non-trivial difficulty, extract the takeaway and add it to the relevant experience file.

- [Scrapers](.claude/experience/scrapers.md) — practical lessons from building and fixing scrapers

## Agent Workflow

When a user asks to find jobs:

1. **Fill the brief** — follow [User Briefing](.claude/protocols/user-briefing.md), save to `searches/<folder>/brief.md`
2. **Choose sources** — start with `--sources all`, or specific ones if user mentioned a platform
3. **Run search** — always use `--detail` to get full descriptions for filtering
4. **Apply filters** — use `--exclude-keywords` and `--exclude-keywords-context` for smart filtering
5. **Output as JSON** — use `--format json` so you can programmatically analyze results, save to search folder
6. **Resolve employers** — run `job-harness resolve --input-file <results.json> --query <query> --cache` or use `--resolve --cache` flag during search to find direct employer career pages and cache results
7. **Analyze & present** — read the JSON output, filter further if needed, present top matches with reasoning; prefer direct employer URLs when available
8. **Iterate** — if results are insufficient, adjust query/filters and run again
