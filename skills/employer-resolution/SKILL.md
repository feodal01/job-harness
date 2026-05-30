---
name: employer-resolution
description: Activate when resolving aggregator listings to direct employer career pages
version: 1.0.0
---

# Employer Resolution

## What

After finding vacancies through aggregators (hh.ru, Habr Career), **resolve** each listing to the employer's direct career page. If the same vacancy exists on the employer's own site, link to that instead.

## Why

- 70% of vacancies never appear on aggregators — but the ones that do often also exist on the employer's site
- Applying directly through a company's career page signals genuine interest
- Direct applications bypass aggregator middlemen
- Some employers post more detail on their own site

## When

Run resolution **after** search, on the filtered set of listings you plan to present to the user. Don't resolve everything — resolve only what passes your quality filters.

## How

### Using MCP tools

- `resolve` tool — batch resolve listings from search results
- `resolve_company` tool — resolve a single company
- `cache_get` / `cache_upsert` — read/write cache entries

### Manual agent-driven resolution

1. **Extract company names** from search results
2. **Deduplicate** — resolve each company only once
3. **Search** for `"[Company] вакансии"` or `"[Company] careers"` via web search
4. **Verify** the found URL is actually the company's career page (not an aggregator page for the same company)
5. **Scan** the career page for a matching vacancy (same or similar title)
6. **Record** the result: direct vacancy URL > career page URL > aggregator URL only

### Resolution outcome categories

| Outcome | Meaning | Action |
|---------|---------|--------|
| **Direct vacancy found** | Same job on employer's site | Use direct URL, tag source as `hh_ru+direct` |
| **Career page found** | Company has a career site but no matching vacancy found | Link to career page, keep aggregator URL as primary |
| **No career page** | Company only uses aggregators | Keep aggregator URL |
| **Company is aggregator-native** | Small companies that exist only on hh.ru/Habr | Keep aggregator URL, don't retry |

## Russian market specifics

The resolver handles Russian legal entity suffixes (ООО, АО, ЗАО, ПАО, etc.) — they are stripped before searching.

**Key finding from field testing**: Russian tech market has a strong aggregator dependency pattern:

- **Large companies** (VK, Sber, Tinkoff, Yandex, Kaspersky, IBS) — have own career sites, but many use client-side rendering (Next.js, React) that requires JavaScript execution
- **Mid-size companies** (RSHB-Intech, Bell Integrator) — may have a career page, but it often just redirects to hh.ru
- **Small companies / startups** — almost never have career pages; they exist exclusively on aggregators

This means the resolver will have varying success rates by company size. Don't present "not found" as a failure — it's market reality.

## Data flow

Search results JSON → `resolve` MCP tool → enriched results with:
- `raw.careers_url` — employer career page URL
- `raw.careers_type` — ATS classification (direct, greenhouse, lever, workday, huntflow)
- `raw.direct_vacancy_url` — direct link to the same vacancy on employer site
- If direct vacancy found: `url` is replaced with the direct link, `source` gets `+direct` suffix

## Caching

Company career page URLs rarely change. The `EmployerCache` uses a two-tier system:

### Local cache (`data/company-careers.json`)

All entries, including companies with no career page. Not committed to git — avoids noise in the repo and keeps user-specific search history local.

### Public cache (`data/company-careers-public.json`)

Only entries with a `careers_url` (companies where a career page was found). Committed to git — serves as a crowdsourced knowledge base that other users can reuse and extend.

On load, the public cache is merged first as a baseline, then the local cache on top (newer entries win). This means pulling from git gives you known career pages for free, while your local runs add to and refine that knowledge.

Cache entries include:
- `careers_url`
- `ats_type` (ATS classification)
- `scraper_name` (per-company career scraper, if available)
- `last_checked` (ISO date)
- `last_found_roles` (boolean — did we find matching roles last time?)
- `ignored` (boolean — user said to skip this company)

Cache is fresh for 7 days. After that, re-verify. If a company has `ignored: true`, skip it entirely.

Use `cache=true` when calling `search` or `resolve` MCP tools.

## Per-company career scrapers

Each company's career site has unique structure. Universal parsers don't work — so we maintain per-company scrapers in `src/job_harness/scrapers/career/`.

Each scraper:
- Extends `BaseCareerScraper` with `@register_career_scraper("name")`
- Knows how to navigate and search ONE company's career page
- Is linked from the cache entry via `scraper_name`

When the resolver finds a company that has a career scraper registered, it uses that scraper instead of the generic link-scanning approach.

### Adding a new career scraper

1. Create `src/job_harness/scrapers/career/<company>.py`
2. Subclass `BaseCareerScraper`, implement `search(params) -> list[JobListing]`
3. Decorate with `@register_career_scraper("<name>")`
4. Import in `scrapers/career/__init__.py`
5. Set `scraper_name` in cache for matching companies
