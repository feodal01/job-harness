# job-harness

Job Search OS — an agent-first approach to job hunting in 2026.

A scalpel, not a shotgun. Precision vacancy search tailored to your request, not mass auto-apply spam.

## Why

Job aggregators are middlemen. They trap you in a bubble — you only see what companies paid to post there. But many companies maintain their own career pages with vacancies that never make it to aggregators. Finding and applying through a company's own site is a signal: you're genuinely interested, not just shotgun-blasting resumes. Job-harness is built to break out of that bubble.

## Who is this for

This repo is not designed for manual use. It's an OS where an AI agent works — searching for jobs on your behalf. You describe what you're looking for, the agent does the rest.

Works best with **Claude Code**.

## Features

- Job search on hh.ru and Habr Career
- Detailed parsing of descriptions and skills
- Filtering by experience, remote, keywords (context-aware — "nice to have" is not a requirement)
- Employer resolution: aggregator listings → direct employer career pages
- Output in Markdown / JSON / CSV
- Stealth browser via rebrowser-playwright

## Quick Start

```bash
uv sync
uv run python -m rebrowser_playwright install chromium
uv run job-harness search --query "product manager" --remote-only --format json
```

## Employer Resolution

Find direct employer career pages for aggregator listings:

```bash
# During search (inline)
uv run job-harness search --query "QA engineer" --detail --resolve --format json -o results.json

# From saved results
uv run job-harness resolve --input-file results.json --query "QA engineer" -o resolved.json
```

## Competitive Gap Analysis (vs. Proficiently)

| Priority | Feature | Why |
|----------|---------|-----|
| **P0** | Fit-scoring (High/Medium/Low/Skip) | Without it the agent can only exclude — not rank. Data: targeted 10 applications → 47% interview rate vs. mass-apply 0.4% |
| **P0** | Job history + deduplication | Without it every search is from scratch and duplicates pile up across sessions |
| **P1** | Direct employer career page scraping | Core philosophy — break the aggregator bubble. `employer_resolver.py` + `--resolve` flag provide first pass; needs caching and career-page-native scrapers |
| **P1** | ATS form auto-fill (Greenhouse, Lever, Workday) | Search without apply is half the value. Data: 75% of resumes rejected by ATS before human review |
| **P1** | Resume tailoring | 50% higher callback rate for tailored resumes, 2x more interviews with metrics. 54% of candidates don't tailor |
| **P1** | Cover letter generation | Part of quality application stack; 61% of recruiters value customization |
| **P2** | Application data cache | Reusable personal info for repeat applications — accelerates the apply workflow |
| **P2** | Network scan (LinkedIn contacts → company careers) | 28.5% hire probability via referral vs. 2.7% cold. 85% of placements through networking |
| **P2** | Company careers page cache | Avoid re-resolving URLs across sessions; 7-day freshness model |
| **P2** | Application tracking (funnel + status) | 5-month average search; 69% ghosted; candidates lose track |
| **P3** | Telegram bot | Convenience layer, not critical path |
| **P3** | Deep work history profile (interview) | Needed for quality tailoring, but can start with a simpler version |
| **P3** | Preference learning from feedback | Nice-to-have; doesn't block other features |

### What job-harness has that competitors don't

- **Own CLI tool** — pip-installable, works without Claude Code
- **Scraper framework with registry** — `BaseScraper` ABC + `@register_scraper` decorator; add a new source in 3 steps
- **Russian market** — hh.ru and Habr Career; Proficiently is US-only
- **Stealth browser** — rebrowser-playwright with anti-detection (webdriver removal, plugin spoofing, Chrome UA)
- **Context-aware keyword filtering** — 80-char before + 30-char after window; "nice to have" phrases don't trigger exclusion
- **Multiple output formats** — Markdown, JSON, CSV
- **Structured data models** — dataclasses with validation and serialization

Full market research with data sources: [`docs/market-research-2026.md`](docs/market-research-2026.md)

## Roadmap

- ~~Agent skill: discover company career pages from web search and scrape them directly~~ (done — `employer_resolver.py` + `--resolve`)
- Career-page-native scrapers (scrape company career sites directly, not via aggregators)
- Company careers page cache (7-day freshness, avoid re-resolving)
- Fit-scoring (High/Medium/Low/Skip with dealbreakers/must-haves/nice-to-haves)
- Job history + deduplication across sessions
- New platforms (SuperJob, Rabota.ru, Telegram channels)
- Application funnel tracking
- Resume tailoring + cover letter generation
- ATS form auto-fill
