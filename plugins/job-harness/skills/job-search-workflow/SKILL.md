---
name: job-search-workflow
description: Full Job Harness v2 workflow for AI agents running job searches. Use when Codex needs to brief a user, search job aggregators and employer career pages via job-harness-v2 CLI, filter and rank results, save .job-harness/v2 artifacts, or run a broad job search.
---

# Job Search Workflow (v2)

This is the canonical full job-search workflow for the Job Harness plugin.
Host-specific entrypoints such as Claude Code `agents/job-searcher.md` and
`commands/job-search.md` must stay thin and defer here instead of duplicating
workflow text.

You are a job search specialist. Find the best job matches across aggregators
and employer career pages using the **v2 contract-first engine** (`job-harness-v2`).
Search broadly, save artifacts under `.job-harness/v2/`, and present curated
results from processed exports — not from raw scrape dumps.

## Runtime refresh before search

Before starting a job search session, refresh the available Job Harness plugin
runtime unconditionally. Do this as routine setup before the search: the agent
does not know which fixes have been merged or which cached plugin version the
host will load.

Use the host-appropriate update path:

- Codex:

  ```bash
  codex plugin marketplace upgrade
  codex plugin add job-harness@job-harness
  ```

  If the marketplace name is different, inspect it with
  `codex plugin marketplace list` and install `job-harness` from the configured
  marketplace that provides this plugin.
- Claude Code:

  ```bash
  claude plugin update job-harness
  ```

  If Claude reports a different installation scope, rerun the update with the
  reported `--scope` value or update through `/plugins`.
- Cursor: there is no confirmed universal update CLI for this plugin. If Cursor
  uses a local plugin copy, refresh that local copy by the configured project
  mechanism and reload Cursor before searching. If Cursor is operating directly
  in this repository, use the repo/worktree CLI below.
- Repository/worktree CLI: use the checkout directly with
  `uv --directory plugins/job-harness run job-harness-v2 ...`. Do not run
  `git pull` or overwrite the working tree without explicit user approval.

After the refresh attempt, continue the search workflow. If a host update
command is unavailable or fails, tell the user which command failed and which
runtime path will be used for the search.

## Workflow

1. **Confirm artifact root** — Before creating files, inspect
   `<working-directory>/.job-harness/v2/runs/`. If the user already has runs,
   ask whether to start fresh, append to an existing run, or use another
   directory. Default run artifacts live under
   `.job-harness/v2/runs/<run_id>/`.

2. **Brief** — Activate the `user-briefing` skill. Ask all questions, confirm
   before proceeding, or reuse an existing confirmed brief. Save the brief to
   `.job-harness/briefs/YYYY-MM-DD_<slug>/brief.md` when the user wants
   project-local brief history.

3. **Inspect catalog** — Always start with the v2 source catalog:

   ```bash
   uv --directory plugins/job-harness run job-harness-v2 list-sources
   ```

   Use exact `source_id` values from the JSON (`habr_career`, `hh_ru`,
   `career:vk`, …). Omit `--source` to search the full implemented catalog.
   Use `--source` / repeat `--source` to narrow the set. Use `--source-type
   aggregator` or `--source-type company_career` for semantic narrowing.

4. **Search** — Run the v2 CLI from the plugin root (or repo root with
   `--directory plugins/job-harness`):

   ```bash
   uv --directory plugins/job-harness run job-harness-v2 search \
     --queries "QA | AQA | SDET | quality assurance | тестировщик" \
     --grade middle \
     --salary-from 150000 \
     --remote-mode compatible-remote \
     --hybrid-ok \
     --work-from RU \
     --vacancy-geography RU \
     --vacancy-geography AM \
     --runs-dir .job-harness/v2/runs
   ```

   The command prints one JSON object (`record_type: v2_search_execution`) to
   stdout. Parse it; do not paste the whole payload into chat.

   **Key response fields:**
   - `run_id` — reuse for append
   - `run_dir` — directory with all artifacts
   - `artifacts.database` — `run.sqlite` (durable run store)
   - `artifacts.raw_listings_table` — `raw_listings` table (unfiltered source evidence)
   - `artifacts.processed_results_table` — `processed_results` table (filtered/deduped export)
   - `artifacts.report_html` — `report.html` (self-contained interactive report)
   - `artifacts.source_attempts_table` — per-source diagnostics
   - `attempts[*].outcome` — `success`, `no_results`, or failure classes
   - `processed_result_count` — downstream listing count after post-processing

5. **Append** — To add another query variant into the same run corpus:

   ```bash
   uv --directory plugins/job-harness run job-harness-v2 search \
     --queries "тестировщик | инженер по тестированию" \
     --append-to-run-id "<run_id>" \
     --runs-dir .job-harness/v2/runs
   ```

6. **Read results safely** — Raw and processed artifacts can be large.
   - Use stdout summary fields and `attempts` for diagnostics first.
   - Give the user `report.html` as the primary browsable artifact when present.
   - Read `processed_results` rows from `run.sqlite` in small slices when needed.
   - Treat the `raw_listings` table as audit evidence, not the presentation layer.
   - Never dump full database tables into the conversation.

7. **Filter & rank** — Apply the brief's exclusion criteria on processed
   results. Use `--exclude-text`, `--exclude-regex`, and `--exclude-company`
   on subsequent searches when criteria are known upfront. Prefer substring
   exclusions for "nice to have" keywords that should not auto-reject a role
   when mentioned only in passing.

8. **Present** — Show top matches with company, title, salary, location,
   remote/relocation when available, source id, and the listing URL. Note which
   sources returned `no_results` vs `success`.

9. **Save** — Keep `run.sqlite`, `report.html`, and the execution
   JSON in the project run folder as the durable audit trail. Write a separate
   `report.md` only when the user asks for a markdown summary.

10. **Iterate** — If results are insufficient, suggest query variants, source
    subsets, or criteria adjustments. Re-search only with user approval.

## v2 search parameters

| CLI flag | Maps to | Notes |
|----------|---------|-------|
| `--query` | `query_variants` | Repeatable; each variant runs against selected sources |
| `--queries` | `query_variants` | Pipe-separated variants, for example `"QA \| AQA \| SDET"`; repeatable |
| `--grade` | `grades` | `intern`, `junior`, `middle`, `senior`, `lead`; repeatable |
| `--salary-from` | `salary_from` | Integer lower bound; native on some sources, post-filter elsewhere |
| `--published-since` | `published_since` | ISO date `YYYY-MM-DD` |
| `--exclude-company` | `exclude_companies` | Repeatable company name substrings |
| `--exclude-text` | `exclude_text` | Repeatable substring exclusions |
| `--exclude-regex` | `exclude_regex` | Repeatable regex exclusions |
| `--relocation` | `relocation` | `true` / `false` |
| `--remote-mode` | `remote_mode` | `any`, `compatible-remote`, `global-remote-only`, `non-remote-only` |
| `--hybrid-ok` | `hybrid_ok` | Allows hybrid vacancies when their country or region matches the requested search geography |
| `--office-ok` | `office_ok` | Allows office vacancies when their country or region matches the requested search geography |
| `--work-from` | `work_from_geographies` | Repeatable country or region; required for `compatible-remote` |
| `--vacancy-geography` | `vacancy_geographies` | Repeatable country or region for vacancy location filtering |
| `--city` | `cities` | Repeatable city names |
| `--source` | `sources` | Repeatable exact source ids; omit for full catalog |
| `--source-type` | `source_types` | `aggregator` or `company_career` |
| `--append-to-run-id` | append mode | Adds to existing run corpus |
| `--run-id` | explicit run id | Optional; auto-generated when omitted |
| `--runs-dir` | artifact root | Default `.job-harness/v2/runs` |

Call `list-sources` to see per-source `native_request_criteria` vs
`structured_output_criteria` — unsupported criteria are still collected raw and
handled in post-processing.

Runtime safety settings such as source timeouts, run timeout, HTTP fetch
timeout, retry count, detail request pacing, and detail concurrency are
service-owned settings packaged in
`job_harness/v2/runtime/search_service_config.json`. Agents should not pass
these values as normal search criteria.

## Supported v2 sources

The source catalog changes as new aggregators and employer career pages are
added. Do not rely on a static source list in this skill. Always call
`job-harness-v2 list-sources` at the start of a search session and use the
returned `source_id`, `source_type`, `source_limit`, country, and criteria
capability fields as the current contract.

## Artifact layout

Each run directory contains:

- `run.sqlite` — durable run database
  - `raw_listings` — one raw listing per row; not deduped or globally capped
  - `source_attempts` — per-source attempt records with outcomes and evidence
  - `run_manifest` — run id, append sequence, source summary
  - `processed_results` — filtered, deduped export for presentation
- `report.html` — self-contained interactive report for kept and filtered-out rows

## Key principles

- Use **`job-harness-v2`**, not legacy `job-harness` (v1), for new searches.
- Raw depth is controlled by each source's `source_limit` in the catalog.
- Prefer several query variants via `--queries` to improve recall; narrow sources
  or append in batches when the search would become too slow or block-prone.
- `no_results` is a valid healthy outcome — distinguish it from transport failures
  (`network_error`, `rate_limited`, `source_timeout`, …).
- Always call `list-sources` before the first search on a new host/session.
- Save artifacts; keep stdout JSON as the execution receipt, not the only copy
  of listings.
- Context-aware filtering: "nice to have" keywords should not exclude a listing
  when they appear only in optional sections.

## Legacy v1 note

The legacy v1 engine (`job-harness`, MCP async search tools, employer resolution)
lives under `src/job_harness/v1/` and is not part of this workflow. Do not mix
v1 MCP tools with v2 CLI artifacts in the same run.
