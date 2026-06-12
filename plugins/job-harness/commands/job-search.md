---
name: job-search
description: Search for job listings across aggregators, resolve to employer career pages, and present curated results
---

# Job Search

You are running a job search. Follow this workflow:

1. **Confirm artifact root** — Before creating files, tell the user the current artifact path (`<current-directory>/.job-harness/`) and ask for approval. If they choose another directory, use that directory's `.job-harness/` folder. After approval, initialize it with `scripts/init-artifacts.sh` from the plugin root when available; otherwise create `.job-harness/briefs/`, `.job-harness/companies/`, and `.job-harness/companies/careers.json` manually.

2. **Brief the user** — Activate the `user-briefing` skill to collect or reuse a search brief. Do not skip any questions. Save a confirmed brief to `.job-harness/briefs/YYYY-MM-DD_<slug>/brief.md` and create its `runs/` folder.

3. **Create a run** — For each search attempt, create `.job-harness/briefs/YYYY-MM-DD_<slug>/runs/YYYY-MM-DD_HHMM_<run-name>/`. Save `run.md` with sources, query variants, filters, and resolve settings.

4. **Search** — Run the MCP search loop:

   1. Call `list_sources` when choosing sources. Use exact source ids with `sources` and semantic groups with `source_groups` (`aggregator`, `company_career`, `directory`, `other`). The response also lists server-supported criteria and per-source raw limits.
   2. `search_start(...)` with search criteria (`query`, `country`, `remote_only`, `experience_levels`, `location`, `salary_from`, `freshness_days`), source selectors (`sources`, `source_groups`), and presentation `max_results`. Pass `country` when the brief has target CIS countries so `sources=all` can select relevant sources.
   3. Poll `search_status(run_id)` until the run finishes or has enough listings. Check `retryable_sources` for failed sources.
      - Retry in the same run: `search_retry(run_id, sources="hh_ru,career:vk")` — exact source ids only; ok sources are skipped automatically.
      - Full re-search: new `search_start` (new `run_id`).
   4. `raw_search_path` from `search_start`/`search_status` points to `raw_search.jsonl`: unfiltered, undeduped, unranked source evidence, not capped by `max_results`.
   5. `search_results(run_id)` (default `format=file`) → returns `path` to the downstream on-disk export (`results.json`). This export applies grade assessment, filters, dedupe, ordering, and `max_results`.
   6. **Context safety:** `results.json` can be very large and will bloat the context if read whole. Do not paste or load the entire file into chat. Prefer `search_refine` first, then `search_results(..., format="inline", limit=N)` for previews; read the file in targeted slices (counts, top-N fields) when you need more.
   7. Optional preview: `search_results(run_id, format="inline", limit=10)` — max 20 per call.
   8. Optional re-filter: `search_refine(run_id, ...)` without re-scraping.

   If aggregator results are sparse or the user wants employer-first discovery, use `search_company_jobs` or `search_start` with `sources=company_directory`. Use `company-live-search` CLI only for small targeted live checks.

5. **Filter and analyze** — Work from the exported dataset on disk, not from loading the whole `results.json` into context. Apply brief exclusions via `search_refine` first, then analyze inline slices or selected fields from the file. Use `exclude_keywords` and `exclude_keywords_context` in `search_start` or `search_refine`.

6. **Present** — Show top matches with:
   - Direct employer URL (if resolved) as primary link
   - Aggregator URL as fallback
   - Key fields: title, company, salary, format, location
   - Brief reasoning for each match (why it fits the brief)

7. **Save** — Copy the MCP export from `data/.runs/<run_id>/results.json` into the project run's `results.json`, write `report.md`, and keep intermediate outputs under `raw/` when useful for audit.

8. **Iterate** — If results are insufficient, ask before re-searching. Reuse the same brief and create a new run folder for the next attempt.

## Key principles

- Search across all available sources relevant to the target country. Always try to find the direct employer URL, not only the aggregator listing.
- Treat `max_results` as a presentation limit only. Raw scraping depth is controlled by each source's `source_limit`.
- Freshness and salary lower-bound are server-only search criteria. Unsupported sources still keep their raw listings and report the unsupported criterion in source summaries.
- Use the bundled company directory as an employer-first expansion source. Treat `search_company_jobs` results as career entrypoints unless a specific vacancy URL was found separately. Treat `search_company_careers` results as live vacancy-link matches from checked company pages.
- For a full-scale search across everything currently available, run all three phases:
  1. Aggregators: `search_start` with `sources=all`, `cache=true`, and `country` when the brief has target countries; poll `search_status`; export via `search_results(run_id)`.
  2. Employer pages: run `job-harness company-live-batch` for the same role query with `--output-jsonl <run>/raw/company-live-results.jsonl`, `--summary-json <run>/raw/company-live-summary.json`, and `--progress`. This batch searches the bundled company directory plus resolved employer career pages from the local employer cache when present.
     If the summary has `access_issues`, report those companies separately and ask the user whether to enable VPN or retry from another network. Do not count access-restricted LinkedIn/Telegram checks as “no vacancies found.”
  3. Deep research: run ordinary web search queries outside the job-harness tools for role + country/city/remote keywords, direct employer career pages, hiring posts, community posts, and new job boards not yet implemented as scrapers. Save the query list, searched URLs, and useful findings under `<run>/raw/deep-research.*`.
  Do not pass `--workers` for normal full-scale runs; the plugin default is the operational concurrency setting. Use `company-live-search` only for narrow checks, not for the bundled/cache-backed company pass.
- Built-in tools do not replace normal web research. If the user asks for broad coverage, market mapping, new sources, or "find everything", always include deep-research queries in addition to MCP/CLI searches.
- Not finding a career page is normal for small companies — don't present it as failure.
- Context-aware filtering: "nice to have" keywords should NOT exclude a listing.
- Always save artifacts. The brief is the reusable source of truth; each run records one execution of that brief.
- `results.json` is for persistence and targeted reads — copy it to the run folder, but keep in-context work to small previews and refined subsets.
