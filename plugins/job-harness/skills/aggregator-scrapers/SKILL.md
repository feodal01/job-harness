---
name: aggregator-scrapers
description: Activate when maintaining, fixing, or adding job aggregator scrapers
version: 1.0.0
---

# Aggregator Scrapers

## Location & Registry

Scrapers live in `src/job_harness/scrapers/`. Each is a subclass of `BaseScraper` decorated with `@register_scraper("name")`. The registry in `src/job_harness/registry.py` auto-discovers them — no other file needs changes when adding a new scraper.

Current scrapers:
- `company_directory.py` — bundled employer directory (`company_directory`), returns career entrypoints rather than confirmed live vacancies
- `hh_ru.py` — hh.ru (`hh_ru`)
- `habr_career.py` — Habr Career (`habr_career`)
- `cis_sources.py` — additional CIS/RU-speaking sources (`hirehi`, `hirify`, `staff_am`, `geekjob`, `talento`, `finder_work`, `it_jobs_uz`, `jobturbo`, `getmatch`)
- `hh_ru.py` subclasses — regional HH-compatible sources (`hh_kz`, `hh_uz`, `rabota_by`, `headhunter_kg`)

Each job-board scraper must declare `countries`, using CIS country codes from `countries.py`. This powers country-aware source selection in CLI and MCP search. The bundled `company_directory` source is global and uses free-text country/location matching through `location` or the dedicated `search_company_jobs` tool.

## Maintenance Rule

Websites evolve. Selectors break. New UI layouts appear. If you detect that a scraper is returning empty results, crashing, or missing data — **you must fix it**. This is not optional.

When fixing a scraper:
1. Run with `--no-headless --debug` to see the actual page and get screenshots
2. Inspect the current DOM to find correct selectors
3. Prefer `data-qa` attributes over CSS classes — they are more stable
4. When `data-qa` is not available, use the most structural selectors (IDs, ARIA roles, semantic elements) over brittle class names
5. Test with a small `--max-results` run before declaring it fixed
6. If you learned something generally useful, add it to the `scraper-insights` skill

## Universality Rule

Every scraper must be a **universal tool**, not a one-off solution for a specific user request.

Do:
- Return all available fields the platform provides (title, salary, experience, remote, skills, etc.)
- Keep filtering logic out of scrapers — that's what `filters.py` is for
- Use `raw` dict for platform-specific data that doesn't map to universal `JobListing` fields
- Make selectors resilient: try new layout first, fall back to old layout

Don't:
- Hardcode keywords, job types, or domain-specific logic into a scraper
- Skip fields just because the current user request doesn't need them
- Add query-specific URL parameters or filters into `_build_search_url`

## General Practices

- `wait_until="domcontentloaded"` + `wait_for_timeout(2000)` after navigation — pages render dynamically
- Close pages in `finally` blocks to avoid browser memory leaks
- Wrap per-card parsing in try/except and `continue` — one broken card shouldn't kill the whole page
- Salary strings are kept as-is (platform-native format) — don't try to parse them into numbers
- `rebrowser-patches` warnings like `cannot get world` are non-critical — ignore them

## Adding a new aggregator scraper

1. Create `src/job_harness/scrapers/<platform>.py`
2. Subclass `BaseScraper`, implement `search(params)` and `fetch_detail(listing)`
3. Decorate with `@register_scraper("<name>")`
4. Set `countries = (...)` with the CIS country codes the source can search
5. Import in `scrapers/__init__.py`
6. Add unit tests for URL construction/parsing and country metadata
7. The `list_sources` MCP tool will auto-discover it

Use the `scraper-insights` skill for practical lessons learned from building and fixing scrapers.
