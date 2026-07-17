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

Before translating a brief into flags, inspect the active runtime contract:

```bash
uv --directory plugins/job-harness run job-harness-v2 search --help
```

Use `job-harness-v2 search --help` directly when the installed executable is
the selected runtime. Technical notes in a saved brief are non-authoritative:
business preferences remain reusable, but old statements about missing flags,
unsupported combinations, or required manual post-filtering must be checked
against the refreshed CLI and removed when stale.

## Workflow

1. **Confirm artifact root** — Before creating files, inspect
   `<working-directory>/.job-harness/v2/runs/`. If the user already has runs,
   ask whether to start fresh, append to an existing run, or use another
   directory. Default run artifacts live under
   `.job-harness/v2/runs/<run_id>/`.

2. **Brief** — Activate the `user-briefing` skill. Ask all questions, confirm
   before proceeding, or reuse an existing confirmed brief. When reusing one,
   preserve its business preferences and rebuild the CLI mapping from the
   current `--help`; do not inherit historical runtime limitations. Save the brief to
   `.job-harness/briefs/YYYY-MM-DD_<slug>/brief.md` when the user wants
   project-local brief history.

3. **Inspect catalog** — Always start with the v2 source catalog:

   ```bash
   CATALOG_JSON="$(mktemp)"
   uv --directory plugins/job-harness run job-harness-v2 list-sources > "$CATALOG_JSON"
   jq '{source_count: (.sources | length), by_type: (.sources | group_by(.source_type) | map({type: .[0].source_type, count: length})), native_query_sources: [.sources[] | select(.native_request_criteria | index("query")) | .source_id]}' "$CATALOG_JSON"
   ```

   Use exact `source_id` values from the JSON (`habr_career`, `hh_ru`,
   `career:vk`, …). Omit `--source` to search the full implemented catalog.
   Use `--source` / repeat `--source` to narrow the set. Use `--source-type
   aggregator` or `--source-type company_career` for semantic narrowing.

4. **Search** — Run the v2 CLI from the plugin root (or repo root with
   `--directory plugins/job-harness`). Resolve the artifact path in the shell
   before `uv --directory` changes the child process working directory:

   ```bash
   RUNS_DIR="$PWD/.job-harness/v2/runs"
   uv --directory plugins/job-harness run job-harness-v2 search \
     --queries "QA | AQA | SDET | quality assurance | тестировщик" \
     --grade middle \
     --salary-minimum 150000 \
     --salary-currency RUB \
     --salary-period month \
     --work-format remote \
     --remote-scope global \
     --remote-scope country:RU \
     --vacancy-geography country:RU \
     --vacancy-geography country:AM \
     --runs-dir "$RUNS_DIR"
   ```

   The command prints progress to stderr and one JSON receipt
   (`record_type: v2_search_execution`) to stdout. Parse the receipt; do not
   paste it into chat.

   **Key response fields:**
   - `run_id` — reuse for append
   - `run_dir` — directory with all artifacts
   - `artifacts.database` — `run.sqlite` (durable run store)
   - `artifacts.execution_json` — the same durable execution receipt
   - `artifacts.report_html` — `report.html` (self-contained interactive report)
   - `diagnostics.source_plans` — per-source budgets, usage, and terminal status
   - `diagnostics.invocations` — status, outcome, and failure-kind counts
   - `result_count` — filtered, deduplicated, ranked vacancy count

   Use repeated `--scenario` flags when the brief contains OR branches. For
   example, this means “worldwide remote at a Russian employer OR remote/hybrid with
   explicit relocation support”:

   ```bash
   --scenario '{"work_formats":["remote"],"remote_scopes":["global"],"employer_geographies":["country:RU"]}' \
   --scenario '{"work_formats":["remote","hybrid"],"relocation":true}'
   ```

   A scenario cannot be combined with flat workplace, relocation, remote, or
   geography flags. Employer geography is only enforceable when an autonomous
   profile scraper produces structured employer-location evidence; inspect
   source capabilities and receipt diagnostics rather than inferring it from
   vacancy location.

5. **Append** — To add another query variant into the same run corpus:

   ```bash
   uv --directory plugins/job-harness run job-harness-v2 search \
     --queries "тестировщик | инженер по тестированию" \
     --append-to-run-id "<run_id>" \
     --runs-dir "$RUNS_DIR"
   ```

6. **Read results safely** — Raw and processed artifacts can be large.
   - Use receipt diagnostics first.
   - Give the user `report.html` as the primary browsable artifact when present.
   - Review both kept rows and filtered-out title matches. The latter expose
     relevant candidates rejected by grade, work-format, geography, salary,
     relocation, or exclusion rules and make bad filters auditable.
   - Use `job-harness-v2 format --input <run.sqlite>` or read
     `final_vacancies` in small slices when a text review is needed.
   - Treat `listing_observations` and `parser_attempts` as audit evidence, not
     the presentation layer.
   - Never dump full database tables into the conversation.

7. **Filter & rank** — The graph applies the request filters and deterministic
   relevance ranking before writing `final_vacancies`. Review the highest
   `relevanceScore` rows first. Use `--exclude-text`, `--exclude-regex`, and
   `--exclude-company` when exclusions are known upfront.

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
| `--query` | `query_variants` | Repeatable; native-query sources run per variant, downstream-only sources fetch once and apply all variants locally |
| `--queries` | `query_variants` | Pipe-separated variants, for example `"QA \| AQA \| SDET"`; repeatable |
| `--grade` | `grades` | `intern`, `junior`, `middle`, `senior`, `lead`; repeatable |
| `--salary-minimum` | `compensation.minimum` | Required together with currency and period |
| `--salary-currency` | `compensation.currency` | ISO 4217 code; `RUR` normalizes to `RUB`; no FX conversion |
| `--salary-period` | `compensation.period` | `hour`, `day`, `month`, or `year` |
| `--salary-gross` | `compensation.gross` | Optional `true` / `false`; omitted means gross/net is not constrained |
| `--published-since` | `published_since` | ISO date `YYYY-MM-DD` |
| `--exclude-company` | `exclude_companies` | Repeatable company name substrings |
| `--exclude-text` | `exclude_text` | Repeatable substring exclusions |
| `--exclude-regex` | `exclude_text` | Repeatable regex-mode exclusions |
| `--relocation` | `relocation` | `true` / `false` |
| `--work-format` | `work_formats` | `remote`, `hybrid`, or `office`; repeatable |
| `--remote-scope` | `remote_scopes` | Remote eligibility only: `global`, `country:<code>`, or `region:<code>`; repeatable and requires `--work-format remote` |
| `--vacancy-geography` | `vacancy_geographies` | Vacancy market/location: `country:<code>`, `region:<code>`, or `city:<name>`; repeatable |
| `--employer-geography` | `employer_geographies` | Employer location: `country:<code>`, `region:<code>`, or `city:<name>`; requires structured profile evidence |
| `--scenario` | `scenarios` | Repeatable JSON OR branch over relocation, work formats, remote scopes, vacancy geography, and employer geography. |
| `--source` | `sources` | Repeatable exact source ids; omit for full catalog |
| `--source-type` | `source_types` | `aggregator` or `company_career` |
| `--append-to-run-id` | append mode | Adds to existing run corpus |
| `--run-id` | explicit run id | Optional; auto-generated when omitted |
| `--runs-dir` | artifact root | Default `.job-harness/v2/runs` |

`unknown` is an internal evidence state, not a public filter value. A requested
hard criterion with missing evidence may trigger an autonomous enrichment
parser. If the declared providers are exhausted and the fact is still unknown,
the vacancy is excluded from final results with an
`insufficient_evidence:<criterion>` diagnostic in `filtered_out_results`.

Compensation filtering compares only explicit lower bounds with the same
currency and period. A missing lower bound, currency, or period is insufficient
evidence; maximum-only compensation does not satisfy a minimum request.

Call `list-sources` to see per-source `native_request_criteria` vs
`structured_output_criteria`. `unsupported` means the source does not guarantee
that fact. Do not claim that post-processing can enforce an unsupported fact
unless a later autonomous enrichment parser actually produces it.

Runtime safety settings such as source timeouts, run timeout, HTTP fetch
timeout, retry count, host pacing, and host concurrency are
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
- `execution.json` — execution receipt with source-plan and invocation diagnostics
- `report.html` — self-contained interactive report for kept and filtered-out rows

The main audit tables are `listing_observations`, `parser_invocations`,
`parser_attempts`, `source_plans`, `fact_sets`, `selection_evaluations`, and
`final_vacancies`. There are no `raw_listings`, `source_attempts`, or
`processed_results` tables in the graph runtime.

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
