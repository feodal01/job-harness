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
     --query "QA" \
     --query "quality assurance" \
     --grade middle \
     --salary-from 150000 \
     --country RU \
     --country AM \
     --runs-dir .job-harness/v2/runs
   ```

   The command prints one JSON object (`record_type: v2_search_execution`) to
   stdout. Parse it; do not paste the whole payload into chat.

   **Key response fields:**
   - `run_id` — reuse for append
   - `run_dir` — directory with all artifacts
   - `artifacts.raw_listings` — `raw-listings.jsonl` (unfiltered source evidence)
   - `artifacts.processed_results` — `processed-results.json` (filtered/deduped export)
   - `artifacts.report_html` — `report.html` (self-contained interactive report)
   - `artifacts.source_attempts` — per-source diagnostics
   - `attempts[*].outcome` — `success`, `no_results`, or failure classes
   - `processed_result_count` — downstream listing count after post-processing

5. **Append** — To add another query variant into the same run corpus:

   ```bash
   uv --directory plugins/job-harness run job-harness-v2 search \
     --query "тестировщик" \
     --append-to-run-id "<run_id>" \
     --runs-dir .job-harness/v2/runs
   ```

6. **Read results safely** — Raw and processed artifacts can be large.
   - Use stdout summary fields and `attempts` for diagnostics first.
   - Give the user `report.html` as the primary browsable artifact when present.
   - Read `processed-results.json` in small slices when needed.
   - Treat `raw-listings.jsonl` as audit evidence, not the presentation layer.
   - Never dump full artifact files into the conversation.

7. **Filter & rank** — Apply the brief's exclusion criteria on processed
   results. Use `--exclude-text`, `--exclude-regex`, and `--exclude-company`
   on subsequent searches when criteria are known upfront. Prefer substring
   exclusions for "nice to have" keywords that should not auto-reject a role
   when mentioned only in passing.

8. **Present** — Show top matches with company, title, salary, location,
   remote/relocation when available, source id, and the listing URL. Note which
   sources returned `no_results` vs `success`.

9. **Save** — Keep `processed-results.json`, `report.html`, and the execution
   JSON in the project run folder as the durable audit trail. Write a separate
   `report.md` only when the user asks for a markdown summary.

10. **Iterate** — If results are insufficient, suggest query variants, source
    subsets, or criteria adjustments. Re-search only with user approval.

## v2 search parameters

| CLI flag | Maps to | Notes |
|----------|---------|-------|
| `--query` | `query_variants` | Repeatable; each variant runs against selected sources |
| `--grade` | `grades` | `intern`, `junior`, `middle`, `senior`, `lead`; repeatable |
| `--salary-from` | `salary_from` | Integer lower bound; native on some sources, post-filter elsewhere |
| `--published-since` | `published_since` | ISO date `YYYY-MM-DD` |
| `--exclude-company` | `exclude_companies` | Repeatable company name substrings |
| `--exclude-text` | `exclude_text` | Repeatable substring exclusions |
| `--exclude-regex` | `exclude_regex` | Repeatable regex exclusions |
| `--relocation` | `relocation` | `true` / `false` |
| `--remote-in-country` | `remote_in_country` | `true` / `false` |
| `--remote-global` | `remote_global` | `true` / `false` |
| `--country` | `countries` | Repeatable ISO codes (`RU`, `AM`); filters catalog-eligible sources |
| `--city` | `cities` | Repeatable city names |
| `--source` | `sources` | Repeatable exact source ids; omit for full catalog |
| `--source-type` | `source_types` | `aggregator` or `company_career` |
| `--append-to-run-id` | append mode | Adds to existing run corpus |
| `--run-id` | explicit run id | Optional; auto-generated when omitted |
| `--runs-dir` | artifact root | Default `.job-harness/v2/runs` |
| `--source-attempt-timeout` | per-source timeout | Seconds (default 30) |
| `--run-timeout` | orchestrator timeout | Seconds (default 120) |
| `--fetch-timeout` | HTTP fetch timeout | Seconds (default 15) |
| `--retry-attempts` | source retries | Default 1 |

Call `list-sources` to see per-source `native_request_criteria` vs
`structured_output_criteria` — unsupported criteria are still collected raw and
handled in post-processing.

## Supported v2 sources (14)

| source_id | type | source_limit | countries |
|-----------|------|-------------|-----------|
| `habr_career` | aggregator | 50 | RU |
| `hh_ru` | aggregator | 100 | RU |
| `talanto` | aggregator | 50 | — |
| `career:vk` | company_career | 25 | RU |
| `career:jetbrains` | company_career | 120 | — |
| `geekjob` | aggregator | 50 | — |
| `talento` | aggregator | 50 | — |
| `finder_work` | aggregator | 100 | — |
| `getmatch` | aggregator | 100 | — |
| `it_jobs_uz` | aggregator | 100 | — |
| `hirify` | aggregator | 100 | — |
| `jobturbo` | aggregator | 50 | — |
| `hirehi` | aggregator | 50 | RU |
| `staff_am` | aggregator | 100 | AM |

## Artifact layout

Each run directory contains:

- `raw-listings.jsonl` — one raw listing per line; not deduped or globally capped
- `source-attempts.jsonl` — per-source attempt records with outcomes and evidence
- `run-manifest.json` — run id, append sequence, source summary
- `processed-results.json` — filtered, deduped export for presentation
- `report.html` — self-contained interactive report for kept and filtered-out rows

## Key principles

- Use **`job-harness-v2`**, not legacy `job-harness` (v1), for new searches.
- Raw depth is controlled by each source's `source_limit` in the catalog.
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
