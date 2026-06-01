---
name: job-search
description: Search for job listings across aggregators, resolve to employer career pages, and present curated results
---

# Job Search

You are running a job search. Follow this workflow:

1. **Confirm artifact root** — Before creating files, tell the user the current artifact path (`<current-directory>/.job-harness/`) and ask for approval. If they choose another directory, use that directory's `.job-harness/` folder. After approval, initialize it with `scripts/init-artifacts.sh` from the plugin root when available; otherwise create `.job-harness/briefs/`, `.job-harness/companies/`, and `.job-harness/companies/careers.json` manually.

2. **Brief the user** — Activate the `user-briefing` skill to collect or reuse a search brief. Do not skip any questions. Save a confirmed brief to `.job-harness/briefs/YYYY-MM-DD_<slug>/brief.md` and create its `runs/` folder.

3. **Create a run** — For each search attempt, create `.job-harness/briefs/YYYY-MM-DD_<slug>/runs/YYYY-MM-DD_HHMM_<run-name>/`. Save `run.md` with sources, query variants, filters, and resolve settings.

4. **Search** — Use the `search` MCP tool with the brief parameters. Start with `detail=true`, `resolve=true`, `cache=true`.

5. **Filter and analyze** — Review results. Apply additional filtering based on the brief (exclusions, salary, format). Use `exclude_keywords` and `exclude_keywords_context` for smart context-aware filtering.

6. **Present** — Show top matches with:
   - Direct employer URL (if resolved) as primary link
   - Aggregator URL as fallback
   - Key fields: title, company, salary, format, location
   - Brief reasoning for each match (why it fits the brief)

7. **Save** — Write machine-readable results to the run's `results.json`, the human-readable report to `report.md`, and intermediate outputs to `raw/` when useful for audit.

8. **Iterate** — If results are insufficient, ask before re-searching. Reuse the same brief and create a new run folder for the next attempt.

## Key principles

- Search across all available sources. Always try to find the direct employer URL, not only the aggregator listing.
- Not finding a career page is normal for small companies — don't present it as failure.
- Context-aware filtering: "nice to have" keywords should NOT exclude a listing.
- Always save artifacts. The brief is the reusable source of truth; each run records one execution of that brief.
