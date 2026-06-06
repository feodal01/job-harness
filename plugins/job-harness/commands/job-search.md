---
name: job-search
description: Search for job listings across aggregators, resolve to employer career pages, and present curated results
---

# Job Search

You are running a job search. Follow this workflow:

1. **Confirm artifact root** — Before creating files, tell the user the current artifact path (`<current-directory>/.job-harness/`) and ask for approval. If they choose another directory, use that directory's `.job-harness/` folder. After approval, initialize it with `scripts/init-artifacts.sh` from the plugin root when available; otherwise create `.job-harness/briefs/`, `.job-harness/companies/`, and `.job-harness/companies/careers.json` manually.

2. **Brief the user** — Activate the `user-briefing` skill to collect or reuse a search brief. Do not skip any questions. Save a confirmed brief to `.job-harness/briefs/YYYY-MM-DD_<slug>/brief.md` and create its `runs/` folder.

3. **Create a run** — For each search attempt, create `.job-harness/briefs/YYYY-MM-DD_<slug>/runs/YYYY-MM-DD_HHMM_<run-name>/`. Save `run.md` with sources, query variants, filters, and resolve settings.

4. **Search** — Use the `search` MCP tool with the brief parameters. Pass `country` when the brief has target CIS countries so `sources=all` can select the relevant country-specific sources. Start with `detail=true`, `resolve=true`, `cache=true`. If aggregator results are sparse or the user wants employer-first discovery, use `search_company_jobs` or `search` with `sources=company_directory` to get bundled company career entrypoints. Use `search_company_careers` only for small targeted live checks over a limited company set.

5. **Filter and analyze** — Review results. Apply additional filtering based on the brief (exclusions, salary, format). Use `exclude_keywords` and `exclude_keywords_context` for smart context-aware filtering.

6. **Present** — Show top matches with:
   - Direct employer URL (if resolved) as primary link
   - Aggregator URL as fallback
   - Key fields: title, company, salary, format, location
   - Brief reasoning for each match (why it fits the brief)

7. **Save** — Write machine-readable results to the run's `results.json`, the human-readable report to `report.md`, and intermediate outputs to `raw/` when useful for audit.

8. **Iterate** — If results are insufficient, ask before re-searching. Reuse the same brief and create a new run folder for the next attempt.

## Key principles

- Search across all available sources relevant to the target country. Always try to find the direct employer URL, not only the aggregator listing.
- Use the bundled company directory as an employer-first expansion source. Treat `search_company_jobs` results as career entrypoints unless a specific vacancy URL was found separately. Treat `search_company_careers` results as live vacancy-link matches from checked company pages.
- For a full-scale search across everything currently available, run all three phases:
  1. Aggregators: call `search` with `sources=all`, `detail=true`, `resolve=true`, and `cache=true`; pass `country` when the brief has target countries.
  2. Employer pages: run `job-harness company-live-batch` for the same role query with `--output-jsonl <run>/raw/company-live-results.jsonl`, `--summary-json <run>/raw/company-live-summary.json`, and `--progress`. This batch searches the bundled company directory plus resolved employer career pages from the local employer cache when present.
     If the summary has `access_issues`, report those companies separately and ask the user whether to enable VPN or retry from another network. Do not count access-restricted LinkedIn/Telegram checks as “no vacancies found.”
  3. Deep research: run ordinary web search queries outside the job-harness tools for role + country/city/remote keywords, direct employer career pages, hiring posts, community posts, and new job boards not yet implemented as scrapers. Save the query list, searched URLs, and useful findings under `<run>/raw/deep-research.*`.
  Do not pass `--workers` for normal full-scale runs; the plugin default is the operational concurrency setting. Use `company-live-search` only for narrow checks, not for the bundled/cache-backed company pass.
- Built-in tools do not replace normal web research. If the user asks for broad coverage, market mapping, new sources, or "find everything", always include deep-research queries in addition to MCP/CLI searches.
- Not finding a career page is normal for small companies — don't present it as failure.
- Context-aware filtering: "nice to have" keywords should NOT exclude a listing.
- Always save artifacts. The brief is the reusable source of truth; each run records one execution of that brief.
