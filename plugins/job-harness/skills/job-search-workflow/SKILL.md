---
name: job-search-workflow
description: Full Job Harness workflow for AI agents running job searches. Use when Codex needs to brief a user, search job aggregators and employer career pages, resolve listings to direct employer URLs, filter and rank results, save .job-harness artifacts, or run a broad/full-scale job search with Job Harness MCP or CLI tools.
---

# Job Search Workflow

This is the canonical full job-search workflow for the Job Harness plugin.
Host-specific entrypoints such as Claude Code `agents/job-searcher.md` and
`commands/job-search.md` must stay thin and defer here instead of duplicating
workflow text.

You are a job search specialist. Find the best job matches across aggregators,
employer career pages, and company-specific career scrapers. Break out of the
aggregator bubble by finding direct employer pages and, when useful, presenting
both aggregator and employer links so the user can apply through both channels.

## Workflow

1. **Confirm artifact root** - Before creating files, inspect the current
   artifact path (`<current-directory>/.job-harness/`). If it already exists,
   ask whether the user wants to start a new search, continue from an existing
   brief/run, or use another directory; summarize available briefs/runs when
   they choose to continue. If it does not exist, tell the user where artifacts
   will be saved and ask for approval. If they choose another directory, apply
   the same existing-root check to that directory's `.job-harness/` folder. Only
   after approval, initialize a missing root with `scripts/init-artifacts.sh`
   from the plugin root when available; otherwise create `.job-harness/briefs/`,
   `.job-harness/companies/`, and `.job-harness/companies/careers.json`
   manually.

2. **Brief** - Activate the `user-briefing` skill. Ask all questions, confirm
   before proceeding, or reuse an existing confirmed brief if the user asks for
   another run. Prefer sequential questions and structured answer choices when
   the host supports them. Save the brief to
   `.job-harness/briefs/YYYY-MM-DD_<slug>/brief.md` and create its `runs/`
   folder.

3. **Create run** - For each search attempt, create
   `.job-harness/briefs/YYYY-MM-DD_<slug>/runs/YYYY-MM-DD_HHMM_<run-name>/`
   and save `run.md` with sources, query variants, filters, and resolve
   settings.

4. **Search** - Run the MCP search loop:

   If Job Harness MCP tools are not visible in a host that supports deferred or
   lazy tool discovery, discover the installed plugin tools before falling back
   to CLI. Use the host's native discovery mechanism to surface
   `list_sources`, `search_start`, and related Job Harness tools, then call
   `list_sources`. Use CLI only when discovery is unavailable or the MCP server
   fails to start/respond.

   1. Call `list_sources` when choosing sources. Use exact ids from the
      response with `sources` and semantic groups with `source_groups`
      (`aggregator`, `company_career`, `directory`, `other`). `list_sources`
      also shows server-supported criteria and each source's raw
      `source_limit`.
   2. Call `search_start(...)` with search criteria (`query`, `country`,
      `remote_only`, `experience_levels`, `location`, `salary_from`,
      `freshness_days`), source selectors (`sources`, `source_groups`), and
      presentation `max_results`. It returns `run_id`, `raw_search_path`, and
      `results_path`.
   3. Poll `search_status(run_id)` until `state` is `completed`, `failed`, or
      `cancelled`. Cancel early with `search_cancel(run_id)` when
      `listings_count` is enough.
   4. Inspect `retryable_sources` and `sources[*].state` for failed or partial
      sources. To retry failed sources in the same `run_id`, call
      `search_retry(run_id, sources="headhunter_kg,career:vk")` with exact ids
      from `search_status.sources` or `list_sources`. Successful requested
      sources are skipped and reported in `skipped_sources`. Then poll
      `search_status` again.
   5. For a full re-search of all sources, call `search_start` again to create
      a new `run_id`. `unknown_run_id` or `invalid_sources` errors include
      `hint` and `retryable_sources`; fix ids before retrying.
   6. Export raw evidence from `raw_search_path`. It points to
      `raw_search.jsonl`, which is unfiltered, undeduped, unranked source
      evidence and is not capped by `max_results`.
   7. Export the full presentation dataset with `search_results(run_id)`. The
      default file export returns a path to
      `data/.runs/<run_id>/results.json`. This downstream export applies grade
      assessment, filters, dedupe, ordering, and `max_results`.
   8. Keep context safe: `results.json` can be large. Do not load or paste the
      whole file into chat. Prefer `search_status(run_id)` for counts and
      diagnostics, `search_refine(run_id, ...)` to narrow the journal, and
      `search_results(run_id, format="inline", limit=N, offset=M)` for small
      previews. Inline results are hard-capped at 20 per call. Read
      `results.json` only in targeted slices when needed.
   9. Pass `debug=true` to `search_results` only when per-source diagnostics
      are needed.

5. **Full-scale search** - For broad coverage, run all three phases:

   1. Aggregators, registered job boards, per-company scrapers, and the known
      company career source: `search_start` with `sources=all`, `cache=true`,
      and `country` when the brief has target countries. Then call
      `search_results(run_id)` for the export file. If `company_careers`
      reports `partial`, the configured source timeout was not enough to finish
      every known company target.
   2. Exhaustive employer career-page audit: run
      `job-harness company-live-batch` for the same role query and save
      `--output-jsonl` to `<run>/raw/company-live-results.jsonl` and
      `--summary-json` to `<run>/raw/company-live-summary.json`; include
      `--progress`. This searches the full bundled company directory plus
      resolved employer career pages from the local employer cache when
      present.
   3. Deep research: run ordinary web search queries outside Job Harness tools
      for role + country/city/remote keywords, direct employer career pages,
      hiring posts, community posts, and new job boards not yet implemented as
      scrapers. Save the query list, searched URLs, and useful findings under
      `<run>/raw/deep-research.*`.

   If `company-live-summary.json` contains `access_issues`, tell the user which
   companies could not be checked because LinkedIn, Telegram, or similar URLs
   were not reachable from the current network. Ask whether they can enable VPN
   or retry from another network before treating those companies as having no
   matching vacancies.

   Do not pass `--workers` for normal full-scale runs; the plugin default is
   the operational concurrency setting. Use `search_company_careers` or
   `company-live-search` only for narrow targeted checks, not for the
   bundled/cache-backed company pass.

6. **Resolve** - After filtering exported listings, run
   `uv --directory plugins/job-harness run job-harness resolve` on the results
   file, or follow the `employer-resolution` skill. Use `cache_get` and
   `cache_upsert` to record findings.

7. **Filter** - Apply the brief's exclusion criteria on the exported dataset.
   Prefer `search_refine` for coarse flags (`experience_levels`, `remote_only`,
   `exclude_keywords`, `exclude_companies`) before opening `results.json`.
   `experience_levels` is exact-list filtering (`["middle"]` means middle, not
   middle+); unknown-grade listings remain marked as
   `experience_origin=unknown`. Use `exclude_keywords` with
   `exclude_keywords_context` in `search_start` or `search_refine` for
   context-aware filtering, for example when a keyword is acceptable in a
   "nice to have" section.

8. **Rank** - Prioritize listings with direct employer vacancy URLs first,
   employer career page URLs second, and aggregator-only URLs last.

9. **Present** - Show top matches in a structured format with the best link
   available (direct > career page > aggregator), company, title, salary,
   format, location, and one-line reasoning for why each listing matches the
   brief.

10. **Save** - Copy the MCP export (`data/.runs/<run_id>/results.json`) into
    the project run folder as `results.json`, write the human-readable report
    to `report.md`, and keep intermediate outputs under `raw/` when useful for
    audit.

11. **Iterate** - If results are insufficient, suggest adjustments. Re-search
    only with user approval, reusing the same brief and creating a new run
    folder.

## Key Principles

- Search across all available sources. Always try to find the direct employer
  URL, not only the aggregator listing.
- Treat `max_results` as a presentation limit only. Raw scraping depth is
  controlled by each source's `source_limit`.
- Freshness and salary lower-bound are server-only search criteria. When a
  source cannot apply `freshness_days` or `salary_from` natively, keep its raw
  listings and report that criterion as unsupported.
- Full-scale search means aggregators/job boards plus the bundled/cache-backed
  employer live batch plus ordinary deep web research. Do not treat
  `sources=all` alone as full coverage, because employer career pages and
  uncataloged web sources are separate phases.
- Built-in tools do not replace normal web research. If the user asks for broad
  coverage, market mapping, new sources, or "find everything", include
  deep-research queries in addition to MCP/CLI searches.
- Not finding a career page is normal for small companies. Do not present it as
  failure.
- Context-aware filtering: "nice to have" keywords should not exclude a
  listing.
- Always save artifacts. The brief is the reusable source of truth; each run
  records one execution of that brief.
- Treat `results.json` as a large on-disk artifact: save it, but read and
  reason over it in small chunks. Never dump the full file into the
  conversation.
