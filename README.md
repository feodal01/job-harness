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
- Output in Markdown / JSON / CSV
- YAML preset configurations
- Stealth browser via rebrowser-playwright

## Quick Start

```bash
uv sync
uv run python -m rebrowser_playwright install chromium
uv run job-harness search --query "product manager" --remote-only --format json
```

## Presets

```bash
uv run job-harness search --preset configs/qa_manual_remote.yaml
```

## Roadmap

- Agent skill: discover company career pages from web search and scrape them directly
- New platforms (SuperJob, Rabota.ru, Telegram channels)
- Application funnel tracking
- Deeper vacancy analysis by the agent
