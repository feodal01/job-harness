# job-harness

Job Search OS — an agent-first approach to job hunting in 2026.

A scalpel, not a shotgun. Precision vacancy search tailored to your request, not mass auto-apply spam.

## Why

Job aggregators are middlemen. They trap you in a bubble — you only see what companies paid to post there. But many companies maintain their own career pages with vacancies that never make it to aggregators. Finding and applying through a company's own site is a signal: you're genuinely interested, not just shotgun-blasting resumes. Job-harness is built to break out of that bubble.

## Who is this for

This repo is not designed for manual use. It's an OS where an AI agent works — searching for jobs on your behalf. You describe what you're looking for, the agent does the rest.

Works best as an agent plugin for **Claude Code** or **OpenAI Codex**.

## Features

- Job search on hh.ru and Habr Career
- Detailed parsing of descriptions and skills
- Filtering by experience, remote, keywords (context-aware — "nice to have" is not a requirement)
- Employer resolution: aggregator listings → direct employer career pages
- Per-company career scrapers (VK, IBS)
- Crowdsourced employer career page cache
- MCP server with 8 tools for Claude Code and Codex integration
- Slash commands: `/job-search`, `/job-resolve`, `/job-contribute`
- Output in Markdown / JSON / CSV
- Stealth browser via rebrowser-playwright

## Installation

### As a Claude Code plugin

1. Clone the repo:
   ```bash
   git clone https://github.com/feodal01/job-harness.git
   cd job-harness
   ```

2. Install dependencies and Playwright browser:
   ```bash
   uv sync
   uv run python -m rebrowser_playwright install chromium
   ```

3. Run Claude Code with the plugin directly:
   ```bash
   claude --plugin-dir /path/to/job-harness
   ```

The plugin provides MCP tools (`search`, `resolve`, `resolve_company`, `list_sources`, `cache_get`, `cache_upsert`, `cache_diff`, `cache_stats`), slash commands (`/job-search`, `/job-resolve`, `/job-contribute`), and skills (`user-briefing`, `employer-resolution`, `aggregator-scrapers`, `scraper-insights`).

To install from the repository marketplace instead:

```bash
claude plugin marketplace add /path/to/job-harness
claude plugin install job-harness@job-harness
```

### As a Codex plugin

1. Clone the repo and install runtime dependencies:
   ```bash
   git clone https://github.com/feodal01/job-harness.git
   cd job-harness
   uv sync
   uv run python -m rebrowser_playwright install chromium
   ```

2. Add the local marketplace and install the plugin:
   ```bash
   codex plugin marketplace add /path/to/job-harness
   codex plugin add job-harness@job-harness
   ```

Codex uses the same MCP tools and skills. Claude-only slash commands and agents remain available through Claude Code.

### As a standalone CLI

```bash
uv sync
uv run python -m rebrowser_playwright install chromium
uv run job-harness search --query "product manager" --remote-only --format json
```

## Usage

### Plugin commands

- `/job-search` — Full workflow: brief → search → resolve → filter → present
- `/job-resolve` — Resolve aggregator listings to employer career pages
- `/job-contribute` — Contribute new cache entries back via PR

### CLI commands

```bash
# Search with employer resolution
uv run job-harness search --query "QA engineer" --detail --resolve --cache --format json -o results.json

# Resolve from saved results
uv run job-harness resolve --input-file results.json --query "QA engineer" --cache

# List available scrapers
uv run job-harness list-sources
```

## Employer Resolution

The resolver finds direct employer career pages for aggregator listings:

1. Check crowdsourced cache (7-day freshness)
2. Search for `[Company] вакансии` / `[Company] careers`
3. Probe common career page paths on company domain
4. Match vacancy on career page using query synonyms
5. Save to cache for future searches

Resolution outcomes:
- **Direct vacancy found** → use employer's URL, tag source as `+direct`
- **Career page found** → link to career page, keep aggregator as primary
- **No career page** → keep aggregator URL (normal for small companies)

## Crowdsourced Cache

`data/company-careers-public.json` contains verified employer career page URLs. It's committed to git and shared across all users. When you resolve employers, your local cache grows. Use `/job-contribute` to push new entries back.

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

- **Claude Code plugin** — MCP server, slash commands, skills, and agents
- **Own CLI tool** — pip-installable, works without Claude Code
- **Scraper framework with registry** — `BaseScraper` ABC + `@register_scraper` decorator; add a new source in 3 steps
- **Russian market** — hh.ru and Habr Career; Proficiently is US-only
- **Stealth browser** — rebrowser-playwright with anti-detection (webdriver removal, plugin spoofing, Chrome UA)
- **Context-aware keyword filtering** — 80-char before + 30-char after window; "nice to have" phrases don't trigger exclusion
- **Crowdsourced employer cache** — shared career page knowledge base
- **Multiple output formats** — Markdown, JSON, CSV
- **Structured data models** — dataclasses with validation and serialization

Full market research with data sources: [`docs/market-research-2026.md`](docs/market-research-2026.md)

## Roadmap

- ~~Agent skill: discover company career pages from web search and scrape them directly~~ (done — `employer_resolver.py` + `--resolve`)
- ~~Claude Code plugin with MCP server~~ (done)
- ~~Crowdsourced employer career page cache~~ (done)
- Career-page-native scrapers (more companies)
- Fit-scoring (High/Medium/Low/Skip with dealbreakers/must-haves/nice-to-haves)
- Job history + deduplication across sessions
- New platforms (SuperJob, Rabota.ru, Telegram channels)
- Application funnel tracking
- Resume tailoring + cover letter generation
- ATS form auto-fill
