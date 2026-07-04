# Search System Gap Analysis

This document compares the current repository state with `docs/search-system-spec.md`.

## Executive Summary

The codebase is not off in the sense of having no architecture. It already has several important pieces of the target system:

- source registry and source groups;
- per-source descriptors and server criteria;
- HTTP vs browser runners;
- independent source execution;
- append-only raw journal and separate `raw_search.jsonl`;
- downstream result pipeline;
- retry policy;
- capability-matrix tests.

The main problem is that the contracts are split across current code, old plans, runtime skills, and tests. Some target behaviors exist, some are only partially implemented, and some are represented with names that no longer match the desired mental model. The result feels more complex than it needs to because the boundaries are not crisp enough.

## What Already Matches

### Source Independence

`SearchEngine` creates one task per eligible source and gathers results with `return_exceptions=True`, so one source exception is coerced into a source outcome instead of crashing the entire run. See `plugins/job-harness/src/job_harness/search_engine.py:140`.

### Raw Evidence Exists

`RawListing` already contains important source-native fields, including `description`, `requirements`, `skills`, and `posted_date`. See `plugins/job-harness/src/job_harness/models.py:28`.

`SearchEngine` writes `RawSearchRecord` rows before running the downstream pipeline. See `plugins/job-harness/src/job_harness/search_engine.py:170`.

### Writer Is Close To The Desired File Manager

`RunJournalWriter` opens both `raw.jsonl` and `raw_search.jsonl` with `O_APPEND`, uses a per-writer lock, and fsyncs each record. See `plugins/job-harness/src/job_harness/run_journal.py:149`.

This is a good foundation for the no-race writer requirement.

### Source Descriptors Exist

`SourceDescriptor` already exposes `group`, `countries`, `server_criteria`, and `source_limit`. See `plugins/job-harness/src/job_harness/types.py:119`.

The CLI source inventory currently lists 19 sources, including aggregators and company career sources.

### Transport Split Exists

`Transport` is already an enum with `http` and `browser`. See `plugins/job-harness/src/job_harness/types.py:21`.

`BaseScraper` and `BaseBrowserScraper` already separate sync HTTP-style scrapers from async browser scrapers.

## Major Gaps

### 1. Search Query Is Still Single-String

Target spec: `query_variants: list[str]`.

Current code: `SearchParams.query` and `SearchCriteriaRequest.query` are a single `str`. See `plugins/job-harness/src/job_harness/models.py:11` and `plugins/job-harness/src/job_harness/types.py:97`.

Impact: the agent cannot express multiple spelling variants as one coherent search intent. Today it must start separate runs or manually concatenate query text, which weakens diagnostics and dedupe.

### 2. Source Types Are Too Broad For The User Model

Target spec: search sources are only `aggregator` or `company_career`; directories are lookup helpers unless they return confirmed vacancies.

Current code: `SourceGroup` includes `DIRECTORY` and `OTHER`. See `plugins/job-harness/src/job_harness/types.py:80`.

`company_directory` is registered as a search source and emits employer entrypoints as `RawListing` rows, not confirmed vacancies.

Impact: vacancy search results can mix actual vacancies with "employer career entrypoint" pseudo-listings. That makes downstream filtering and final agent reasoning noisier.

### 3. Remote And Relocation Semantics Are Under-Modeled

Target spec: separate `relocation`, `work_format`, and `remote_scope`.

Current code: request and raw models mostly have one `remote_only`/`remote` boolean. See `plugins/job-harness/src/job_harness/models.py:15` and `plugins/job-harness/src/job_harness/types.py:101`.

Impact: the system cannot distinguish "remote inside country", "remote worldwide", and "relocation supported". Post-processing can only infer this ad hoc from text.

### 4. Vacancy Geography Search Is Too Narrow

Target spec: `vacancy_geography` values such as `country:RU`, `region:EU`, and `city:<name>`.

Current code: request has one `country` and one `location` string. See `plugins/job-harness/src/job_harness/types.py:99`.

Impact: multi-country, regional, or many-city searches require multiple runs or ambiguous location strings. This complicates broad job search for an agent.

### 5. Outcome Taxonomy Does Not Match The Policy

Target spec: canonical outcomes include `success`, `no_results`, `source_timeout`, `run_timeout`, `parse_error`, `invalid_source_output`, and `resource_failure`.

Current code: `SourceState` has `OK`, `PARTIAL`, `TIMEOUT`, `ERROR`, etc., and no first-class `no_results`. See `plugins/job-harness/src/job_harness/types.py:144`.

Impact: a source returning an empty list can currently become `ok` unless another detector catches it. That conflicts with the requirement that zero listings is only `no_results` when explicit no-result evidence exists.

### 6. Scraper Return Type Cannot Express No-Results Evidence

Target spec: scraper/source attempt must communicate explicit no-result evidence separately from an empty list.

Current code: `BaseScraper.search(...) -> list[RawListing]` and `BaseBrowserScraper.search_with_page(...) -> list[RawListing]`. A normal empty list has no attached reason or evidence.

Impact: the orchestrator cannot reliably distinguish "real no results" from "parser found zero cards because layout changed".

### 7. Invalid Source Output Is Not Strict Enough

Target spec: wrong return type, missing required fields, malformed records, or too many records should become `invalid_source_output`.

Current code: browser path maps wrong return type to `ERROR/PARSE_ERROR`; raw conversion silently drops malformed rows in `raw_listings_from_dicts`. See `plugins/job-harness/src/job_harness/result_pipeline.py:82`.

Impact: invalid normalized output can be hidden as parse error or silently skipped, making refactors less safe.

### 8. Append Mode Is Not A First-Class Search Mode

Target spec: append additional query variants/sources into the same raw corpus and rerun idempotent post-processing.

Current code: `execute_retry` and `RunRegistry.retry` append retry attempts into the same journal, but retry purges listings for retried sources and is scoped to failed/partial source recovery. See `plugins/job-harness/src/job_harness/search_engine.py:97` and `plugins/job-harness/src/job_harness/run_registry.py:183`.

Impact: retry is useful, but it is not the same as broadening the corpus with new search variants.

### 9. Description Is Present In Schema But Not Enforced As Evidence

Target spec: `description` is a stable raw field and its absence has a diagnostic reason.

Current code: `RawListing.description` exists, but there is no `description_status`, detail enrichment status, or source-level obligation to explain absence. See `plugins/job-harness/src/job_harness/models.py:44`.

Impact: post-processing cannot tell whether missing description means "source did not expose it", "detail fetch was skipped", "detail fetch timed out", or "parser failed to extract it".

### 10. Capability Surfaces Are Split

Target spec: source capability should clearly separate native request support, structured output availability, and unsupported criteria.

Current code has both `ScraperCapabilities` with `server/client/best_effort/unsupported` and `SourceDescriptor.server_criteria`, but public `list_sources` exposes only descriptor fields. See `plugins/job-harness/src/job_harness/types.py:33` and `plugins/job-harness/src/job_harness/types.py:119`.

Impact: maintainers have two related concepts to keep aligned, and agents see only part of the story.

### 11. Strict Flag Policy Is Mostly Dormant

`SearchRequest` still has `strict_flags`, but `_resolve_sources` currently skips by country and fast profile only. Unsupported requested criteria are recorded in source summaries, not used to partition/skip sources. See `plugins/job-harness/src/job_harness/search_engine.py:410`.

This may be fine if the intended policy is "search broadly and filter later", but then `strict_flags` should be removed or redefined. Keeping it half-present creates confusion.

### 12. Legacy Test Fixtures Do Not Yet Match The Testing Policy

Target spec: every supported source must have a required real fixture suite under the engine's fixture tree, currently `plugins/job-harness/tests/v2/fixtures/scrapers/<source>/<case>/` for the contract-first engine, and a manually reviewed golden answer for expected raw listings/outcome. No fixture suite means the source is not supported.

Current legacy repo area: `plugins/job-harness/tests/fixtures/` contains only `experience_engine_real_world_samples.json`. Many legacy parser tests use inline HTML/JSON constants or fake DOMs.

Current v2 area: `plugins/job-harness/tests/v2/fixtures/scrapers/` now contains the first policy-grade source fixtures for one aggregator and one company career source. That does not make the legacy registered source catalog compliant; it establishes the target pattern for source-by-source migration.

Impact: these tests are valuable, but they are not policy-grade proof that real source parser contracts are stable. They do not prove that parser code still matches a real source response, and they do not provide a manually verified golden answer independent of the parser implementation. Under the target spec, most currently registered sources would need to be marked unsupported or experimental until their real fixture suites exist.

## Secondary Gaps

### Published Date Naming

Current API uses `freshness_days`, while the user-facing vision talks about a concrete publication date. `freshness_days` is useful for some source-native filters, but the post-processing contract should also support `published_since: date`.

### Final Count Semantics

Current `max_results` is a presentation cap after downstream processing. That is good. The remaining gap is "paginate until enough after filtering" because current sources usually stop at source-local limits rather than coordinating with post-filtered count needs.

### Source Catalog Transport

Transport exists internally, but `SourceDescriptor.to_dict()` does not expose it. If agents need to reason about cost and fragility, transport should be visible in source catalog diagnostics.

### Resource Failure Handling

Failure modes include `DISK_FULL`, `BROWSER_DISCONNECTED`, and `POOL_RECYCLED`, but writer failures and local runtime failures are not cleanly represented as the policy-level `resource_failure`.

## Suggested Refactoring Direction

The likely refactor should be contract-first and incremental:

1. Define a new search request model with query variants, remote/relocation split, multi-country, multi-city, and `published_since`.
2. Replace `SourceState`/`FailureMode` with, or bridge them to, the canonical outcome taxonomy.
3. Introduce a scraper result object that can carry `listings`, `outcome`, `no_result_evidence`, `detail_evidence`, and diagnostics.
4. Make append mode explicit and separate from retry.
5. Split `company_directory` out of vacancy search or mark it as lookup-only.
6. Collapse or clarify `ScraperCapabilities` vs `server_criteria`.
7. Add raw listing evidence fields for description/detail status.
8. Migrate source parser tests to real captured fixtures source by source.

This should not be a rewrite. The current journal, runner split, registry, source limits, and downstream pipeline are worth preserving.
