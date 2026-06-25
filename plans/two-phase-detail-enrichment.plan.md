# Move Detail Enrichment After Search Filtering

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository does not currently check in `PLANS.md`; this file follows the plan specification from `/Users/user/.codex/plugins/cache/ai-engineer-workbench/ai-engineer-workbench/0.2.1/skills/plan-file/references/PLANS.md`. The repository-specific scraper guidance read while authoring this plan is `.agents/skills/job-harness-scraper-development/SKILL.md`.


## Purpose / Big Picture

After this change, `job-harness-v2 search` will first collect search result pages, run deterministic post-processing on those search-list facts, and only then fetch detail descriptions for the listings that survive filtering. This lets the service reduce avoidable requests to sources such as `hh_ru` and `hirify` while still enriching every listing that the result pipeline decides to show.

The observable behavior is a run where `raw_listings` is populated from search pages before detail requests begin, detail requests are made only for kept canonical rows, and the final `processed_results` and `report.html` contain full descriptions where the source allowed them. Operational controls such as source timeouts, fetch timeouts, retry counts, per-source detail delay, and detail concurrency are not CLI flags; they live in a service-owned JSON config packaged with the plugin and documented in the runtime skill so agents know where maintainers can inspect or tune them.


## Progress

- [x] (2026-06-24 17:20Z) Created branch `codex/detail-enrichment-flow-plan`.
- [x] (2026-06-24 17:20Z) Read the ExecPlan authoring skill and canonical plan specification.
- [x] (2026-06-24 17:20Z) Read the repository scraper development skill and inspected v2 runtime, contracts, persistence, post-processing, CLI, source catalog, and job-search workflow guidance.
- [x] (2026-06-24 17:20Z) Captured design constraints from the user: service-owned runtime settings, no detail count budget, no large table expansion, explicit pipeline definition, and direct contract changes.
- [x] (2026-06-24 17:20Z) Authored this plan.
- [x] (2026-06-24 17:35Z) Addressed review feedback that pre-enrichment filtering must use fuzzy title matching because vacancy titles carry high-signal role information but are often phrased imprecisely.
- [x] (2026-06-24 18:02Z) Implemented Milestone 1: added packaged service runtime config and removed operational runtime flags from the v2 CLI surface.
- [x] (2026-06-24 18:02Z) Implemented Milestone 2: added explicit `SearchPipeline` in `plugins/job-harness/src/job_harness/v2/runtime/pipeline.py` and routed `V2SearchApplication` through it.
- [x] (2026-06-24 18:02Z) Implemented Milestone 3: made `SearchOrchestrator` collect and write search-listing records without detail requests.
- [x] (2026-06-24 18:02Z) Implemented Milestone 4: added processing phases, row ids for raw records, and detail work-list construction from pre-enrichment kept rows.
- [x] (2026-06-24 18:02Z) Implemented Milestone 5: added `DetailEnrichmentRunner` and `SqliteRunStore.update_raw_record_detail` to enrich kept rows through controlled per-source detail requests.
- [x] (2026-06-24 18:02Z) Implemented Milestone 6: final post-processing writes `phase: "final"` payloads, renders `report.html`, and keeps detail status in diagnostic surfaces.
- [x] (2026-06-24 18:02Z) Implemented Milestone 7 code, tests, runtime skill update, and plugin version bump to `0.2.41`.
- [x] (2026-06-24 18:02Z) Ran focused v2 subset, ruff, mypy, and full deterministic v2 unittest sweep; 189 tests passed.
- [x] (2026-06-24 18:10Z) Ran final deterministic gate: `python3 scripts/verify_v2.py --skip-live`.
- [x] (2026-06-24 18:14Z) Ran bounded live e2e: `python3 scripts/verify_v2.py --live-profile light`.
- [x] (2026-06-24 18:20Z) Ran targeted live `hh_ru` + `hirify` search and inspected `run.sqlite` plus `report.html`.
- [x] (2026-06-24 19:29Z) Replaced `country-converter` with Babel CLDR country normalization in post-processing and removed country normalization from Hirify parser code.
- [x] (2026-06-24 19:53Z) Expanded country normalization to use Babel territory names across CLDR locales plus normalized source-token keys, then reran targeted live `hh_ru` + `hirify` search.
- [x] (2026-06-24 20:08Z) Added explicit post-processing region scope handling for `europe` and `EU`, with `RU` excluded from the `europe` scope.
- [x] (2026-06-24 20:20Z) Verified the region-scope change with the deterministic v2 gate and a targeted live `hh_ru` + `hirify` run.
- [x] (2026-06-25 00:18Z) Refactored geography normalization and remote-scope filtering out of `postprocessing/pipeline.py` into focused shared modules.


## Surprises & Discoveries

- Observation: The current v2 schema already carries detail status on `raw_listings`, so the plan can avoid introducing a group of new tables.
  Evidence: `plugins/job-harness/src/job_harness/v2/persistence/schema.sql` defines `description_availability`, `detail_fetched`, and `detail_parse_error` columns on `raw_listings`.

- Observation: The current orchestrator owns both search pagination and detail fetching in one source attempt.
  Evidence: `plugins/job-harness/src/job_harness/v2/runtime/orchestrator.py` calls `_enrich_detail_pages` from `_fetch_search_pages` before `_write_raw_records` persists listings.

- Observation: The current CLI exposes operational knobs that belong to the service runtime, not to job-search requests.
  Evidence: `plugins/job-harness/src/job_harness/v2/cli.py` declares `--source-attempt-timeout`, `--run-timeout`, `--fetch-timeout`, and `--retry-attempts` on the `search` command.

- Observation: The current post-processing layer can be reused as the filter boundary because it already dedupes rows and records filtered-out rows.
  Evidence: `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py` builds `results` and `filtered_out_results` from raw listing records.

- Observation: The HTML report already has a debug area where detail status can live without becoming prominent presentation content.
  Evidence: `plugins/job-harness/src/job_harness/v2/presentation/report_template.html` appends debug fields for description status, detail fetched, and detail parse error.

- Observation: The current post-processing code already has reusable fuzzy matching primitives and tests that compare query intent against title tokens.
  Evidence: `plugins/job-harness/src/job_harness/v2/matching.py` defines `fuzzy_tokens_match`; `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py` uses it in `_query_matches`; `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py` covers `QA` matching `AQA` and `тестировщик` matching `Инженер по тестированию`.

- Observation: The existing `processed_results` table needed a phase key so the pre-enrichment and final snapshots can both be stored without adding another table.
  Evidence: `plugins/job-harness/src/job_harness/v2/persistence/schema.sql` now keys `processed_results` by `run_id`, `append_sequence`, and `phase`; `SqliteRunStore.read_processed_results` defaults to `phase: "final"`.

- Observation: Country normalization belongs to post-processing because country matching is a global filter criterion, not a source parser responsibility.
  Evidence: `plugins/job-harness/src/job_harness/v2/geography.py` provides Babel-backed country and region normalization, while `plugins/job-harness/src/job_harness/v2/postprocessing/remote_scope.py` applies it to `listing.country`, `location_text`, `city`, and raw region facts before post-processing filters run. `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/hirify.py` preserves source-provided region text such as `united_states` and `russia`.

- Observation: Babel CLDR data covers Russian and English territory names without adding a project-local country map.
  Evidence: `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py` covers `Кипр`, `Украина`, `United States`, ISO2 country values, and Hirify-style raw region tokens through the post-processing pipeline.

- Observation: Some source APIs emit stable country slugs or common English names that differ from current English CLDR display names.
  Evidence: Hirify live row `676221` emitted `country: "turkey"` and `regions: ["turkey"]`; Babel 2.18 English display name for `TR` is `Türkiye`, so a two-locale name index did not map that structured value.

- Observation: Source region scopes and country codes must not share the same membership rules.
  Evidence: `plugins/job-harness/src/job_harness/v2/geography.py` now treats `europe` and `EU` as explicit region scopes. `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py` proves `PL` matches both scopes, while `RU` does not match `europe`; `plugins/job-harness/tests/v2/test_geography.py` proves `CY` matches `europe` and `EU`.

- Observation: Hirify can expose country eligibility through `remote_restrictions` even when the top-level country field is missing.
  Evidence: Live row `673449` had no top-level raw country but did have `remote_restrictions: ["russia"]`; in run `r-20260624-195934-ac0ca4` it normalized to `RU` and was kept for `--country RU`.


## Decision Log

- Decision: Runtime controls are service configuration, not CLI request parameters.
  Rationale: Search requests should describe what jobs to find. Timeouts, retry counts, request pacing, and concurrency are operational safety policy for the service. The CLI must not invite agents to tune these values during normal searches.
  Date/Author: 2026-06-24 / Codex

- Decision: Store the runtime policy in a packaged JSON file at `plugins/job-harness/src/job_harness/v2/runtime/search_service_config.json`, loaded through a typed config module.
  Rationale: A JSON resource is easy for maintainers to inspect and package with the plugin. A typed loader keeps validation in Python and gives tests one stable contract.
  Date/Author: 2026-06-24 / Codex

- Decision: Do not add a count budget for detail requests.
  Rationale: The service must request detail for every canonical row that survives pre-enrichment post-processing. Request pacing and source access handling protect the service; they do not cap how many kept rows receive detail.
  Date/Author: 2026-06-24 / Codex

- Decision: Keep the SQLite run artifact compact by updating existing `raw_listings` rows for detail enrichment instead of introducing separate detail tables.
  Rationale: The project moved to SQLite so one durable artifact can be updated without file races. Updating the existing row preserves that property and keeps the data model understandable.
  Date/Author: 2026-06-24 / Codex

- Decision: Define the full run pipeline in one module, `plugins/job-harness/src/job_harness/v2/runtime/pipeline.py`.
  Rationale: The major stages must be visible in one place: source catalog selection, search collection, pre-enrichment filtering, detail enrichment, final filtering, persistence, and presentation. This avoids spreading the workflow across application, orchestrator, and post-processing files.
  Date/Author: 2026-06-24 / Codex

- Decision: Detail status is diagnostic metadata, not headline report content.
  Rationale: Users primarily need job title, company, salary, location, work format, description, requirements, and URL. Detail statuses such as blocked or rate-limited are useful for debugging and source health, so they belong in collapsible debug fields or concise diagnostics.
  Date/Author: 2026-06-24 / Codex

- Decision: Pre-enrichment filtering must include fuzzy title matching.
  Rationale: Title text often contains the strongest role signal on search result pages. Exact title matching would drop relevant rows because titles vary by source, language, abbreviation, and inflection. This plan requires a bounded fuzzy title filter now; broader fuzzy search scoring improvements can be planned separately.
  Date/Author: 2026-06-24 / Codex

- Decision: Keep pre-enrichment and final processed snapshots in the existing `processed_results` table by adding `phase` to the primary key.
  Rationale: The pipeline needs a durable pre-enrichment snapshot for inspection and work-list construction, but the user explicitly wanted to avoid table sprawl. A phase key keeps both snapshots queryable inside the same artifact.
  Date/Author: 2026-06-24 / Codex

- Decision: Use Babel CLDR territory names in post-processing instead of normalizing countries in parsers.
  Rationale: Parsers should preserve source facts, while post-processing owns global criteria evidence and filtering. Babel gives a maintained localized territory-name catalog, including Russian and English names, without the `pandas` dependency required by `country-converter`.
  Date/Author: 2026-06-24 / Codex

- Decision: Build the post-processing country index from Babel territory names across CLDR locales, normalized token keys, accent-folded keys, valid territory codes, and single-target CLDR territory aliases.
  Rationale: Source APIs commonly emit country values as lowercase tokens such as `turkey`, `czech_republic`, and `united_states`. This keeps the mapping generic and data-backed while avoiding parser-specific country maps. Ambiguous names are not inserted into the index.
  Date/Author: 2026-06-24 / Codex

- Decision: Preserve source region scopes `europe` and `EU` in the processed `country` field and filter them through explicit membership lists.
  Rationale: Region scopes are useful source evidence, but they are not ISO-2 countries. `EU` means European Union membership, while `europe` uses the project-defined Europe scope and intentionally excludes `RU`. This avoids timezone- or geography-based guesses during filtering.
  Date/Author: 2026-06-24 / Codex

- Decision: Change the v2 contract directly and update all callers, fixtures, tests, runtime skills, and plugin version in the same implementation sequence.
  Rationale: The plugin is in active early development. A direct contract keeps behavior understandable and avoids extra paths whose only purpose is preserving superseded runtime semantics.
  Date/Author: 2026-06-24 / Codex


## Outcomes & Retrospective

Status: implementation and verification complete; follow-up country normalization change is implemented with Babel in post-processing and verified.

Expected final outcome: a v2 run uses search pages to build the evidence corpus, filters and dedupes that corpus, enriches every kept canonical listing with detail text when the source exposes detail, and writes a final processed result set and HTML report. The user can observe the change by running a small two-source search against `hh_ru` and `hirify`, then checking that search-page fetches are completed and persisted before detail fetches begin, and that detail fetch count equals the number of canonical kept rows for sources that implement detail enrichment unless the source returns a terminal access outcome during detail fetching.

Current evidence: focused v2 subset passed with 43 tests, ruff and mypy passed for v2, full deterministic v2 unittest discovery passed with 189 tests, `python3 scripts/verify_v2.py --skip-live` passed, and `python3 scripts/verify_v2.py --live-profile light` passed. New coverage proves search-only collection, pre-enrichment work-list construction, detail enrichment without a count budget, per-source stop on block/rate-limit, filtered-out rows left without detail fetches, CLI runtime flags removed, and final `processed_results` phase selection.

Country normalization evidence: focused post-processing, Hirify parser, architecture boundary, ruff, mypy, and lock checks passed after replacing `country-converter` with Babel. Follow-up post-processing changes expanded the Babel lookup to source-token forms such as `turkey`, `czech_republic`, `UK`, and `cote_d_ivoire`, and preserved `europe` / `EU` as explicit region scopes. `python3 scripts/verify_v2.py --skip-live` passed with 195 v2 tests after the region-scope change.

Live targeted evidence: run `r-20260624-180424-a6de5c` against `hh_ru` and `hirify` wrote 200 raw search records, produced 128 pre-enrichment kept rows and 72 filtered-out rows, enriched all 128 kept rows, and left all 72 filtered-out rows with `detail_fetched = false`. The generated report exists at `plugins/job-harness/.job-harness/v2/runs/r-20260624-180424-a6de5c/report.html` and includes both live sources. Compared with the previous one-phase behavior, this run avoided 72 detail requests: 36.0% fewer detail requests, or 34.4% fewer total source requests when including 9 search-page requests.

Follow-up live country evidence: run `r-20260624-194626-916d2b` against `hh_ru` and `hirify` wrote 200 raw search records and enriched all 127 kept rows. Filtered-out Hirify rows now show normalized country codes for source-token countries: `676221` is `TR`, `676375` is `CZ`, `676485` is `US`, and `676604` is `CY`. Remaining null-country filtered-out rows are rows where the raw record lacks structured country evidence.

Region-scope live evidence: run `r-20260624-195934-ac0ca4` against `hh_ru` and `hirify` wrote 200 raw search records, produced 118 kept rows for `--country RU`, and enriched all 118 kept rows. Row `673449` normalized `remote_restrictions: ["russia"]` to `RU` and was kept. `europe` did not pass `--country RU`; focused post-processing tests prove that `europe` matches `PL` but not `RU`.

Refactor evidence: `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py` no longer owns the Babel country index or remote-scope compatibility matrix. Geography normalization lives in `plugins/job-harness/src/job_harness/v2/geography.py`; listing-specific remote/geography evidence extraction lives in `plugins/job-harness/src/job_harness/v2/postprocessing/remote_scope.py`. Focused tests and `python3 scripts/verify_v2.py --skip-live` passed after this split.


## Context and Orientation

The repository root is `/Users/user/Documents/repos/qa-job-harness`. The installable plugin lives under `plugins/job-harness`. The v2 engine source code lives under `plugins/job-harness/src/job_harness/v2`.

Important files:

- `plugins/job-harness/src/job_harness/v2/application.py` is the application service used by CLI-style entrypoints. It currently creates the catalog, store, orchestrator, post-processor, and report in one method.
- `plugins/job-harness/src/job_harness/v2/runtime/orchestrator.py` runs source fetch jobs and writes source attempt records. It currently performs search pagination and detail enrichment in one path.
- `plugins/job-harness/src/job_harness/v2/contracts/scraper.py` defines `SourceScraper` and `DetailEnrichmentScraper`. A source that implements `DetailEnrichmentScraper` can build and parse a detail request for one raw listing.
- `plugins/job-harness/src/job_harness/v2/contracts/records.py` defines `RawListing`, `RawSearchRecord`, `SourceAttemptRecord`, and detail status fields.
- `plugins/job-harness/src/job_harness/v2/persistence/schema.sql` defines the SQLite artifact schema. The run artifact is `run.sqlite`.
- `plugins/job-harness/src/job_harness/v2/persistence/sqlite_run_store.py` is the only code that writes the SQLite artifact.
- `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py` converts raw records into `processed_results`, including kept rows and filtered-out rows.
- `plugins/job-harness/src/job_harness/v2/cli.py` exposes `job-harness-v2`.
- `plugins/job-harness/skills/job-search-workflow/SKILL.md` is the runtime guidance used by agents when running searches.
- `plugins/job-harness/src/job_harness/v2/source_catalog.sql` lists supported sources and source-level raw search limits.

Definitions:

`Search collection` means sending the source-native search request, applying the source-native filters that the source supports, following source pagination, parsing search result pages or API responses, and writing `RawSearchRecord` rows. It does not fetch individual vacancy detail pages.

`Detail enrichment` means fetching and parsing the full vacancy detail page or detail API response for one already-collected raw listing. It can add `description`, `requirements`, `additional_sections`, `skills`, and extra structured raw facts.

`Canonical row` means the single row selected by post-processing after dedupe and hard filtering. If the same vacancy appears through multiple query variants, the canonical row is the one that the result pipeline keeps for presentation.

`Pre-enrichment post-processing` means running deterministic filtering and dedupe on search-list facts only. It may keep rows that need detail text for final judgment. It must not reject a listing only because full detail text has not been fetched.

`Fuzzy title filtering` means applying the query-intent check to vacancy title text with tolerant token matching, not exact substring equality. It should handle common title variants such as `QA` in `AQA` and Russian inflection such as `тестировщик` in `Инженер по тестированию`. The first implementation should use the repository's existing fuzzy matching helpers; improving fuzzy search quality is a separate follow-up.

`Final post-processing` means running the same result pipeline after detail enrichment has updated kept canonical rows. This final pass produces the `processed_results` payload used by the report.

`Service runtime config` means packaged settings that the service owns: source attempt timeout, run timeout, HTTP fetch timeout, retry policy, detail request pacing, and detail concurrency. Agents do not pass these values through normal search requests.


## Target Pipeline

The target pipeline must be explicit in `plugins/job-harness/src/job_harness/v2/runtime/pipeline.py`. That module should expose a `SearchPipeline` class whose `run` method performs these stages in order:

1. Resolve run paths and reserve an append sequence.
2. Load `SearchServiceConfig` from `runtime/search_service_config.json`.
3. Build the supported source catalog.
4. Run search collection through a search-only source orchestrator.
5. Read search records from `raw_listings`.
6. Run pre-enrichment post-processing and write a `processed_results` snapshot with phase metadata `phase: "pre_enrichment"`.
7. Build a detail work list from pre-enrichment kept canonical rows that belong to sources implementing `DetailEnrichmentScraper`.
8. Run detail enrichment for every work-list item, using service-configured pacing and concurrency, and update the matching `raw_listings` row.
9. Read updated raw records.
10. Run final post-processing and write a `processed_results` snapshot with phase metadata `phase: "final"`.
11. Render `report.html` from the final payload.
12. Mark the append attempt completed.

The pipeline should be the only place where the complete ordered flow is visible. Lower-level modules may own implementation details, but the reader should not have to inspect several modules to understand the stages of one run.


## Plan of Work

Milestone 1 adds service runtime configuration and removes operational settings from the CLI search request. Add `plugins/job-harness/src/job_harness/v2/runtime/config.py` with frozen dataclasses `SearchServiceConfig`, `RetryServiceConfig`, and `DetailServiceConfig`. Add `plugins/job-harness/src/job_harness/v2/runtime/search_service_config.json` and include it in `plugins/job-harness/pyproject.toml` under `tool.hatch.build.targets.wheel.force-include`. The JSON must include source attempt timeout, run timeout, fetch timeout, retry attempts, retry backoff, detail concurrency, per-source detail delay, and stop-on-source-access-outcome settings. It must not include a count limit for detail requests. Update `plugins/job-harness/src/job_harness/v2/cli.py` so `search` no longer declares or consumes timeout, fetch-timeout, or retry flags. Update `plugins/job-harness/skills/job-search-workflow/SKILL.md` so agents learn that runtime settings are service-owned and that the JSON path is for maintainer inspection.

Milestone 2 introduces the explicit pipeline module. Add `plugins/job-harness/src/job_harness/v2/runtime/pipeline.py` with a `SearchPipeline` class. Move the orchestration sequence from `V2SearchApplication.search` into this class without changing public `V2SearchApplication.search` behavior. `V2SearchApplication` should construct `SearchPipeline` with the loaded service config, fetcher, postprocessor, run store factory, and run layout. Keep `application.py` as a thin application entrypoint. Export the pipeline types from `plugins/job-harness/src/job_harness/v2/runtime/__init__.py` only if tests or application need them.

Milestone 3 makes search collection search-only. In `plugins/job-harness/src/job_harness/v2/runtime/orchestrator.py`, split the current behavior into a source search collector that does not call `build_detail_request` or `parse_detail_response`. It must still follow `SourceSearchParseResult.next_request`, enforce each source's `source_limit`, preserve `no_results`, record block/rate-limit/network/timeout outcomes, and write `RawSearchRecord` rows. For records collected from a source that exposes a description in the search response, set `description_availability` to `present`; otherwise use `not_requested`. For records from detail-capable sources whose search card lacks full description, `detail_fetched` must be false.

Milestone 4 makes pre-enrichment filtering produce a detail work list. Extend `ResultTablePostProcessor.process` so callers can pass a phase value. The pre-enrichment phase must dedupe and apply hard filters from fields already present in search records: source, query, title, company, salary, country, city, remote flags, native grade, posted date, source raw facts, skills, and search-card text. Title filtering must be fuzzy, not exact, because source titles often carry the highest-signal role information while using abbreviations, inflected words, or source-specific phrasing. The implementation should reuse the existing fuzzy matching helpers for the first pass and leave deeper fuzzy search quality work outside this migration. It must retain rows when the only missing evidence is full detail text. Include `raw_record_id` in each row by changing `SqliteRunStore.read_raw_records` or adding a new read method that returns the SQLite row id alongside the JSON payload. The work list contains exactly the kept canonical rows that have a detail-capable source and `detail_fetched == false`.

Milestone 5 adds detail enrichment as a separate phase. Add a detail runner in `runtime/orchestrator.py` or a focused `runtime/detail_enrichment.py` module. It accepts detail work items with `raw_record_id`, source id, query variant, and listing payload. It calls `build_detail_request` and `parse_detail_response` for every work item unless the source has already returned a terminal access outcome during this detail phase. Detail pacing and per-source concurrency come from `SearchServiceConfig`. There is no count budget. Add `SqliteRunStore.update_raw_record_detail(raw_record_id, listing, description_availability, detail_fetched, detail_parse_error)` that updates the existing `raw_listings` row, including `listing_json`, `record_json`, and detail columns under one SQLite transaction. Detail block and rate-limit outcomes should update the row's detail diagnostic fields and a detail-phase summary in `run_manifest` or the final processed payload, while preserving the search-list facts. Do not force detail outcomes into `SourceAttemptRecord`; that record remains the search-attempt summary.

Milestone 6 finalizes post-processing and presentation. After detail enrichment, run `ResultTablePostProcessor.process` again with phase `final`, write the final processed payload, and render `report.html` from final results. The HTML report should keep detail metadata in each card's debug section. Markdown formatting in `plugins/job-harness/src/job_harness/v2/presentation/formatters.py` should avoid presenting detail status as headline content; if detail failed, it can show a concise diagnostics section after the listing body. Remove any user-facing mention of a detail budget because the service has no count budget.

Milestone 7 updates tests, skills, verification, and plugin version. Update `plugins/job-harness/tests/v2/test_runtime_orchestrator.py` so detail is not fetched before raw records are written. Add G4 tests proving the pipeline writes search records, builds a detail work list from kept rows, enriches every kept row, stops source detail requests after a source access terminal outcome, and leaves filtered-out rows without detail fetches. Update `plugins/job-harness/tests/v2/test_application_cli.py` to assert that CLI search does not expose operational runtime flags. Update parser fixture tests only where the new orchestration contract affects expected fetch order. Update `plugins/job-harness/skills/job-search-workflow/SKILL.md`, `plugins/job-harness/.codex-plugin/plugin.json`, `plugins/job-harness/pyproject.toml`, and the local package entry in `plugins/job-harness/uv.lock` for the installable plugin change.


## Concrete Steps

Work from the repository root:

    cd /Users/user/Documents/repos/qa-job-harness

Before implementation, confirm the branch:

    git branch --show-current

Expected output:

    codex/detail-enrichment-flow-plan

Run a focused baseline test group before changing runtime code:

    uv --directory plugins/job-harness run python -m unittest \
      tests.v2.test_runtime_orchestrator \
      tests.v2.test_postprocessing_pipeline \
      tests.v2.test_application_cli

Implement Milestone 1 by editing these files:

- `plugins/job-harness/src/job_harness/v2/runtime/config.py`
- `plugins/job-harness/src/job_harness/v2/runtime/search_service_config.json`
- `plugins/job-harness/src/job_harness/v2/runtime/__init__.py`
- `plugins/job-harness/src/job_harness/v2/cli.py`
- `plugins/job-harness/pyproject.toml`
- `plugins/job-harness/skills/job-search-workflow/SKILL.md`

The service config JSON should have this shape:

    {
      "source_attempt_timeout_seconds": 180.0,
      "run_timeout_seconds": 360.0,
      "fetch_timeout_seconds": 15.0,
      "retry": {
        "max_attempts": 1,
        "backoff_seconds": 0.0
      },
      "detail": {
        "per_source_concurrency": 1,
        "default_request_delay_seconds": 0.75,
        "request_delay_seconds_by_source": {
          "hh_ru": 1.5,
          "hirify": 0.75
        },
        "stop_on_blocked": true,
        "stop_on_rate_limited": true
      }
    }

Implement Milestones 2 through 6 by editing these files:

- `plugins/job-harness/src/job_harness/v2/application.py`
- `plugins/job-harness/src/job_harness/v2/runtime/pipeline.py`
- `plugins/job-harness/src/job_harness/v2/runtime/orchestrator.py`
- `plugins/job-harness/src/job_harness/v2/persistence/sqlite_run_store.py`
- `plugins/job-harness/src/job_harness/v2/ports.py`
- `plugins/job-harness/src/job_harness/v2/contracts/records.py`
- `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py`
- `plugins/job-harness/src/job_harness/v2/presentation/formatters.py`
- `plugins/job-harness/src/job_harness/v2/presentation/report_template.html`

After each milestone, update this plan's `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` sections if the implementation changes course or reveals important behavior.

Run focused tests after each runtime milestone:

    uv --directory plugins/job-harness run python -m unittest tests.v2.test_runtime_orchestrator
    uv --directory plugins/job-harness run python -m unittest tests.v2.test_postprocessing_pipeline
    uv --directory plugins/job-harness run python -m unittest tests.v2.test_application_cli

Run the deterministic v2 gate before handoff:

    python scripts/verify_v2.py --skip-live

Run the bounded live profile only after deterministic tests pass and only as operational evidence:

    python scripts/verify_v2.py --live-profile light


## Validation and Acceptance

The implementation is accepted when the following behaviors are observable.

CLI surface:

    uv --directory plugins/job-harness run job-harness-v2 search --help

The help output includes search criteria such as `--queries`, `--grade`, `--salary-from`, `--country`, `--source`, and `--append-to-run-id`. It does not include timeout, fetch-timeout, or retry-attempt flags.

Search-only collection:

Use a fixture-backed orchestrator test with a detail-capable fake source. The expected fetch order for the search phase contains only search URLs. Raw records in `run.sqlite` have `detail_fetched` false before the detail phase.

Pre-enrichment filtering:

Use a test with three search rows: one row excluded by company, one row excluded by query mismatch, and one row kept. The detail work list contains only the kept row. The excluded rows remain in raw evidence and have `detail_fetched` false.

Use fuzzy title matching cases in the same phase: `QA` must keep a title such as `AQA`, `тестировщик` must keep `Инженер по тестированию`, and an unrelated title such as `Account Manager` must be removed as `query_mismatch`. These cases should not require detail text.

Detail enrichment:

Use a test with two kept canonical rows for a detail-capable source. The detail runner fetches both detail URLs and updates the two matching `raw_listings` rows. The final processed payload includes the enriched descriptions.

No detail count budget:

Use a test with five kept canonical rows and a service config with per-source concurrency one. The detail runner fetches all five detail URLs in order, with no count-based stop.

Source access handling:

Use a test where the first detail request for `hh_ru` raises `blocked`. The matching raw row is preserved with detail diagnostics. The detail runner does not continue making detail requests for `hh_ru` during that detail phase when `stop_on_blocked` is true. Rows from other sources continue.

Presentation:

Open `report.html` from a run with one detail failure. The listing card still shows the job information and URL. Detail status appears in the card's debug area, not as the headline content.

Repository verification:

    python scripts/verify_v2.py --skip-live

Expected result: command exits with status 0. If live evidence is needed:

    python scripts/verify_v2.py --live-profile light

Expected result: command exits with status 0 or reports source-specific live access outcomes that are classified according to the v2 taxonomy.


## Idempotence and Recovery

The implementation should be safe to rerun because each `job-harness-v2 search` run writes to one `run.sqlite` under a run id. Append mode continues to reserve a new append sequence before writing rows. Detail enrichment updates rows inside the same SQLite database and must use explicit transactions in `SqliteRunStore.update_raw_record_detail`.

If a detail fetch fails for one listing, preserve the search-listing facts and write diagnostic fields on that row. Do not delete the row. If a source returns a terminal access outcome during detail enrichment, stop detail requests for that source for the current detail phase according to service config and continue other sources.

If implementation reaches a broken intermediate state, use normal Git inspection commands to understand the diff:

    git status --short
    git diff -- plugins/job-harness/src/job_harness/v2

Do not discard unrelated user changes. If a local run database was created only for manual testing and is not under source control, it can be removed after capturing any needed evidence.


## Artifacts and Notes

The new service config is a packaged plugin resource and must be included in the wheel. Add this force-include entry to `plugins/job-harness/pyproject.toml`:

    "src/job_harness/v2/runtime/search_service_config.json" = "job_harness/v2/runtime/search_service_config.json"

The runtime skill must describe the operational contract in plain language. In `plugins/job-harness/skills/job-search-workflow/SKILL.md`, the `v2 search parameters` section should list only user search parameters. Add a short note after the parameter table:

    Runtime safety settings such as timeouts, retry count, HTTP fetch timeout,
    detail request pacing, and detail concurrency are service-owned settings
    packaged in `job_harness/v2/runtime/search_service_config.json`.
    Agents should not pass these values as normal search criteria.

The explicit pipeline can be sketched as:

    class SearchPipeline:
        async def run(self, request: SearchRequest, *, run_id: str | None = None) -> V2SearchExecution:
            paths = self._resolve_paths(request, run_id)
            with self._run_store_factory(paths.database_path, run_id=paths.run_id) as store:
                append_sequence = store.reserve_append_attempt(to_jsonable(request))
                search_result = await self._collect_search_records(...)
                pre = self._postprocessor.process(..., phase=ProcessingPhase.PRE_ENRICHMENT)
                work = self._detail_work_items(pre.payload, store)
                await self._enrich_detail_records(work, store)
                final = self._postprocessor.process(..., phase=ProcessingPhase.FINAL)
                store.write_processed_results(final.payload)
                paths.report_html_path.write_text(render_processed_results_html(final.payload), encoding="utf-8")
                store.mark_append_attempt_completed()

Use the sketch as orientation, not as copy-paste code. The actual implementation must follow the repository's type and test conventions.


## Interfaces and Dependencies

In `plugins/job-harness/src/job_harness/v2/runtime/config.py`, define:

    @dataclass(frozen=True)
    class RetryServiceConfig:
        max_attempts: int
        backoff_seconds: float

    @dataclass(frozen=True)
    class DetailServiceConfig:
        per_source_concurrency: int
        default_request_delay_seconds: float
        request_delay_seconds_by_source: dict[str, float]
        stop_on_blocked: bool
        stop_on_rate_limited: bool

    @dataclass(frozen=True)
    class SearchServiceConfig:
        source_attempt_timeout_seconds: float
        run_timeout_seconds: float
        fetch_timeout_seconds: float
        retry: RetryServiceConfig
        detail: DetailServiceConfig

        @classmethod
        def from_package_resource(cls) -> SearchServiceConfig:
            ...

The loader must validate positive timeouts, non-negative delays, `per_source_concurrency >= 1`, and `retry.max_attempts >= 1`.

In `plugins/job-harness/src/job_harness/v2/persistence/sqlite_run_store.py`, add:

    @dataclass(frozen=True)
    class StoredRawRecord:
        raw_record_id: int
        payload: JsonObject

    def read_raw_record_rows(self) -> tuple[StoredRawRecord, ...]:
        ...

    def update_raw_record_detail(
        self,
        *,
        raw_record_id: int,
        listing: RawListing,
        description_availability: DescriptionAvailability,
        detail_fetched: bool,
        detail_parse_error: str | None,
    ) -> None:
        ...

The update method must update `description_availability`, `detail_fetched`, `detail_parse_error`, `listing_json`, and `record_json` for exactly one row.

In `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py`, define a small phase enum or literal type:

    class ProcessingPhase(StrEnum):
        PRE_ENRICHMENT = "pre_enrichment"
        FINAL = "final"

The processed payload must include `phase`. The final report uses the latest `phase == "final"` payload for normal presentation.

In the detail runner, define:

    @dataclass(frozen=True)
    class DetailWorkItem:
        raw_record_id: int
        source: str
        query_variant: str
        listing: RawListing

    @dataclass(frozen=True)
    class DetailRunResult:
        attempted: int
        enriched: int
        failed: int
        stopped_sources: tuple[str, ...]

The runner must accept a tuple of `DetailWorkItem` and process all items unless a per-source terminal access outcome stops that source according to config. It must not accept a numeric detail limit.

The pipeline must write a compact detail summary to `run_manifest` or the final processed payload. The summary should contain total detail work items, attempted count, enriched count, failed count, and stopped source ids. It should not require another SQLite table.


## Change Note

2026-06-24 / Codex: Created the initial ExecPlan for moving detail enrichment after pre-enrichment post-processing. The plan incorporates the user's design constraints: runtime controls in service config instead of CLI flags, no detail count budget, compact SQLite artifact updates, debug-only detail status in presentation, one explicit pipeline module, and direct v2 contract changes.

2026-06-24 / Codex: Revised Milestone 4 after review to make fuzzy title filtering an explicit pre-enrichment requirement and to defer deeper fuzzy search quality work to a separate follow-up.

2026-06-24 / Codex: Implemented the two-phase pipeline through deterministic v2 tests. The implementation stores pre-enrichment and final snapshots in `processed_results` by phase, moves operational settings into packaged service config, and separates detail enrichment into `DetailEnrichmentRunner`.

2026-06-24 / Codex: Completed deterministic and live validation. The targeted `hh_ru` + `hirify` run confirmed that every kept row was enriched, filtered-out rows did not trigger detail requests, and the new flow reduced detail requests by 36.0% for that run.

2026-06-24 / Codex: Expanded Babel-based country normalization to cover source-token values and single-target CLDR territory aliases in post-processing. The targeted live run `r-20260624-194626-916d2b` confirmed that Hirify rows with `turkey` and `czech_republic` raw countries render as `TR` and `CZ` in the filtered-out report cards.

2026-06-24 / Codex: Added explicit region-scope filtering for `europe` and `EU`. `europe` is preserved in processed rows and matches only the fixed project Europe country list; `RU` is explicitly not a member of that scope.

2026-06-24 / Codex: Verified the region-scope filtering with `python3 scripts/verify_v2.py --skip-live` and the live run `r-20260624-195934-ac0ca4`.

2026-06-25 / Codex: Refactored Babel geography normalization into `job_harness.v2.geography` and listing remote/geography evidence extraction into `job_harness.v2.postprocessing.remote_scope`. `postprocessing/pipeline.py` now delegates those concerns and remains focused on row construction, filtering, and payload assembly.
