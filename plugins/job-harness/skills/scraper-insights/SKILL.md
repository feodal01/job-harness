---
name: scraper-insights
description: Activate when building or fixing scrapers — reusable principles from real-world experience
version: 1.0.0
---

# Scraper Insights

Generalized, reusable principles from building and fixing scrapers. See [references/scrapers.md](references/scrapers.md) for the full list of patterns and strategies.

## When to apply

- Building a new scraper (aggregator or career site)
- Fixing a scraper that returns empty/wrong results
- Adding filtering or URL construction logic
- Debugging browser automation issues

## Key patterns at a glance

- **Fallback selectors** — always chain selectors; new layout first, old as fallback
- **Dynamic attribute suffixes** — use CSS starts-with/contains when exact match fails
- **Over-broad selectors** — look for the most specific element in the subtree
- **Text-based detection fragility** — prefer structural selectors over text content
- **URL tracking params** — strip query params from vacancy URLs
- **Anti-bot detection** — check page title for block signals
- **Bitrix SEF filter URLs** — construct filter URLs directly instead of clicking checkboxes
- **Next.js SSR JSON** — extract `__NEXT_DATA__` before falling back to DOM
- **Isolated world** — `page.evaluate()` can't access page JS globals; use HTTP requests or URL construction
- **Search input visibility** — check `is_visible()` before calling `fill()`

Full details and strategies for each pattern are in [references/scrapers.md](references/scrapers.md).
