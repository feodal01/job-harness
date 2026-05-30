---
name: job-search
description: Search for job listings across aggregators, resolve to employer career pages, and present curated results
---

# Job Search

You are running a job search. Follow this workflow:

1. **Brief the user** — Activate the `user-briefing` skill to collect search parameters. Do not skip any questions. Save the brief to `.job-harness/searches/YYYY-MM-DD_<slug>/brief.md`.

2. **Search** — Use the `search` MCP tool with the brief parameters. Start with `detail=true`, `resolve=true`, `cache=true`.

3. **Filter and analyze** — Review results. Apply additional filtering based on the brief (exclusions, salary, format). Use `exclude_keywords` and `exclude_keywords_context` for smart context-aware filtering.

4. **Present** — Show top matches with:
   - Direct employer URL (if resolved) as primary link
   - Aggregator URL as fallback
   - Key fields: title, company, salary, format, location
   - Brief reasoning for each match (why it fits the brief)

5. **Save** — Write results to `.job-harness/searches/YYYY-MM-DD_<slug>/results.json`.

6. **Iterate** — If results are insufficient, adjust query/filters and search again. Ask the user before re-searching.

## Key principles

- Job aggregators are middlemen. Always try to find the direct employer URL.
- Not finding a career page is normal for small companies — don't present it as failure.
- Context-aware filtering: "nice to have" keywords should NOT exclude a listing.
- Always save artifacts. The brief is the source of truth.
