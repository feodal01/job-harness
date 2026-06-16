# Migrate Scraper Tests To The Scraper Testing Policy

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository does not currently check in `PLANS.md`; this file follows the plan specification from `/Users/user/.codex/skills/public/plan-file-author/references/PLANS.md`. The target testing policy is embedded here in operational form so a future contributor does not need the prior conversation. The policy source read while authoring this plan was `.agents/skills/job-harness-scraper-development/references/testing-policy.md`, with grade-related constraints from `.agents/skills/job-harness-scraper-development/references/experience-filtering.md`.


## Purpose / Big Picture

After this migration, maintainers will be able to change a scraper, add a source, or refactor the search engine and know exactly which deterministic tests prove correctness. Source parser tests will be based on captured real source artifacts instead of invented pages, source contracts will state what each source can and cannot enforce, and orchestrator or transport fault tests will classify failures through the same signals production code sees.

The observable result is a split verification model. The deterministic blocking gate proves code correctness with G0 through G6 tests. Live source checks remain useful health signals, but they do not pretend that today's network access proves parser correctness. A developer can run one command before merge and see static checks, unit tests, real-fixture parser tests, source contract tests, orchestrator isolation tests, shared fault-injection tests, and approved golden regressions pass without relying on public websites being reachable.


## Progress

- [x] (2026-06-16 13:38Z) Read the scraper development skill and the complete testing policy, including gate matrix, fixture rules, outcome taxonomy, retry policy, live smoke policy, drift monitoring policy, and review checklist.
- [x] (2026-06-16 13:38Z) Read the experience filtering policy because scraper contracts include grade support and several sources expose native or estimated grade evidence.
- [x] (2026-06-16 13:38Z) Read the ExecPlan authoring specification and existing repository plans in `plans/`.
- [x] (2026-06-16 13:38Z) Inspected current tests, fixtures, source registry contracts, `SearchEngine`, `HttpRunner`, `BrowserPool`, `RunJournal`, `SourceRuntimeConfig`, and `scripts/verify_repo.py`.
- [x] (2026-06-16 13:38Z) Captured the current public source inventory from `uv --directory plugins/job-harness run job-harness list-sources --json`.
- [x] (2026-06-16 13:38Z) Authored this migration plan with current contracts, target contracts, concrete milestones, commands, validation, and recovery guidance.
- [x] (2026-06-16 13:38Z) Reviewed this plan against the current repository state and the testing policy; review findings are recorded in `Outcomes & Retrospective` and `Artifacts and Notes`.
- [ ] Implement Milestone 1: create a deterministic gate map and canonical outcome bridge.
- [ ] Implement Milestone 2: add policy-grade fixture layout, loader, metadata validation, and first real captured fixtures.
- [ ] Implement Milestone 3: expand source contract tests for every registered source and every declared native criterion.
- [ ] Implement Milestone 4: migrate source parser tests from inline synthetic inputs to G2 real-artifact cases source by source.
- [ ] Implement Milestone 5: fill G4 orchestrator isolation coverage for every policy-required fake source behavior.
- [ ] Implement Milestone 6: fill G5 shared fault-injection coverage for every transport, access, and runtime symptom in the policy.
- [ ] Implement Milestone 7: add optional G6 golden regression tests over reviewed G2 fixture suites.
- [ ] Implement Milestone 8: split live smoke, drift monitoring, and access-route checks from the deterministic merge gate.
- [ ] Update repository guidance after the deterministic gate and live-gate split is implemented.


## Surprises & Discoveries

- Observation: `scripts/verify_repo.py full` currently runs live checks after static checks and unit tests, while the policy says G7 live smoke tests do not block normal merges.
  Evidence: `scripts/verify_repo.py` defines `full` as Ruff, mypy, detect-secrets, unit discovery, MCP stdio smoke, registered live source smokes, and full company batch smoke.

- Observation: The repository already has a strict source descriptor surface, but it is not yet the full policy source contract.
  Evidence: `plugins/job-harness/src/job_harness/types.py` defines `SourceDescriptor(group, countries, server_criteria, source_limit)`, while the policy also requires transport type and explicit supported or unsupported criteria ownership. Transport exists today as `BaseScraper.transport()` and is not part of `SourceDescriptor`.

- Observation: The current outcome contract is close to the policy but uses different names and lacks a first-class `no_results` terminal outcome.
  Evidence: `SourceState` currently contains `OK`, `PARTIAL`, `TIMEOUT`, `ERROR`, `RATE_LIMITED`, `BLOCKED`, `CANCELLED`, `SKIPPED`, and `SKIPPED_UNSUPPORTED_FLAG`; the policy taxonomy uses `success`, `no_results`, `partial_success`, `skipped_by_policy`, `cancelled`, `source_timeout`, `run_timeout`, `blocked`, `rate_limited`, `http_client_error`, `http_server_error`, `network_error`, `parse_error`, `invalid_source_output`, and `resource_failure`.

- Observation: A scraper returning an empty list can currently be recorded as `SourceState.OK`.
  Evidence: `HttpRunner._classify(None)` returns `SourceState.OK`, and `SearchEngine` writes an OK status even when `outcome.listings` is empty. `test_search_engine.py::SanityBaselineTest.test_zero_result_against_baseline_flagged_suspicious` relies on this behavior to flag a suspicious zero-result OK source after the fact.

- Observation: Policy-grade G2 parser fixtures are mostly absent today.
  Evidence: `plugins/job-harness/tests/fixtures/` currently contains only `experience_engine_real_world_samples.json`; source-specific parser inputs are mostly inline constants in files such as `test_habr_career.py`, `test_cis_sources.py`, `test_hh_ru.py`, and `test_career_scrapers.py`.

- Observation: Existing source-specific tests still have value, but many are not G2 proof under the policy.
  Evidence: HH-family tests use `tests/_support/fake_browser.py` and `card_dom(...)`; Habr and CIS tests use inline HTML or JSON constants. These are useful for G1 helper behavior, G3 request mapping, and G4/G5 wiring, but a parser fixture test for a real source must use a captured real artifact or a minimized real capture.

- Observation: Public source discovery imports more than the package-level scraper import.
  Evidence: `plugins/job-harness/src/job_harness/cli.py` and `plugins/job-harness/scripts/mcp-server.py` import both `job_harness.scrapers` and `job_harness.scrapers.career`, while `plugins/job-harness/src/job_harness/scrapers/__init__.py` does not import `job_harness.scrapers.career`. Contract tests must bootstrap the registry the same way the public `list_sources` path does, or the dedicated `career:*` sources can be omitted by accident.

- Observation: Runtime retry and manual retry have different policy needs but are easy to confuse.
  Evidence: `SearchEngine._should_retry` already retries only zero-listing transient `FailureMode` values from `SOURCE_LEVEL_RETRYABLE_FAILURES`, while `source_retry.py` classifies `TIMEOUT`, `ERROR`, `RATE_LIMITED`, `BLOCKED`, `CANCELLED`, and `PARTIAL` as manually retryable after a finished run.

- Observation: Existing G4 and G5-style tests are substantial but not policy-complete.
  Evidence: `test_search_engine.py`, `test_http_runner.py`, `test_browser_pool.py`, and `test_run_journal.py` already cover isolation, deadlines, partial blocked raw artifacts, status invariants, and detector behavior. Missing or incomplete policy cases include explicit `no_results`, invalid normalized source output, too many source records, missing required fields, artifact writer failure as `resource_failure`, and unknown zero-card pages failing closed.


## Decision Log

- Decision: Treat the testing policy as the target contract, and treat current `SourceState`/`FailureMode` names as a transition surface that must be bridged explicitly before any broad rename.
  Rationale: The codebase already has many tests and serialized run journals using current names. A bridge lets tests assert policy semantics immediately without forcing a risky all-at-once runtime rename.
  Date/Author: 2026-06-16 / Codex

- Decision: Keep existing inline parser tests temporarily, but classify them honestly as G1, G3, G4, or G5 unless their inputs are traced to real captured source artifacts.
  Rationale: Deleting useful tests before real fixtures exist would reduce coverage. Counting invented or synthetic pages as G2 would violate the policy and give false confidence.
  Date/Author: 2026-06-16 / Codex

- Decision: Make the deterministic merge gate a separate verifier profile, then keep `full` as an optional superset or release-health profile.
  Rationale: The current project guidance says to run `python scripts/verify_repo.py full`, but the policy says live source health must not block normal merge confidence. The migration should avoid ambiguity by naming the deterministic profile explicitly and documenting when live checks are expected.
  Date/Author: 2026-06-16 / Codex

- Decision: Use `uv --directory plugins/job-harness run job-harness list-sources --json` and MCP `list_sources` as the public source catalog truth in contract tests.
  Rationale: The public tools import both normal scrapers and career scrapers. A test that imports only `job_harness.scrapers` can miss `career:ibs` and `career:vk`.
  Date/Author: 2026-06-16 / Codex

- Decision: Introduce explicit no-result evidence at the source parser boundary instead of treating every empty list as success.
  Rationale: The policy makes `success` and `no_results` mutually exclusive and forbids "zero cards" from silently becoming success or no-results without explicit evidence.
  Date/Author: 2026-06-16 / Codex

- Decision: Add G2 fixtures in source cohorts, starting with structured HTTP/API sources before browser-heavy sources.
  Rationale: JSON/API and SSR payload sources are cheaper to capture, minimize, and validate deterministically. Browser-heavy sources may require HAR or minimized DOM capture and should not block establishing the fixture harness.
  Date/Author: 2026-06-16 / Codex

- Decision: Do not invent captcha, VPN, geo, login, no-result, malformed source pages, missing-field vacancies, or duplicate-card cases to satisfy fixture checklists.
  Rationale: The policy allows synthetic symptoms only in shared G5 tests and fake source behavior only in G4. Source-specific parser fixtures must be grounded in real artifacts.
  Date/Author: 2026-06-16 / Codex

- Decision: Keep `experience_levels` owned by the centralized grade engine unless a source has native server or structured client grade support.
  Rationale: The experience policy states that unsupported and best-effort sources must not populate native grade just to satisfy a filter. This affects G2/G3 assertions for `RawListing.experience`.
  Date/Author: 2026-06-16 / Codex


## Outcomes & Retrospective

Status: plan authored and reviewed; implementation has not started.

Author review result: the plan is internally consistent after resolving the main naming mismatch by introducing a transition bridge between current `SourceState`/`FailureMode` and policy canonical outcomes. The plan matches the current repository state observed on 2026-06-16: source-specific parser fixtures are not yet in policy layout, current verifier `full` includes live checks, current source descriptors omit public transport but classes expose transport internally, and current empty-list handling can still produce OK statuses. Ambiguities found during review were addressed in the plan by making the deterministic gate profile explicit, requiring the registry bootstrap to match CLI/MCP `list_sources`, and stating exactly which current tests are not allowed to count as G2 until backed by real captures.

Policy coverage review: the milestones cover G0 through G9. G0 is preserved through Ruff, mypy, and detect-secrets. G1 is covered by helper and request-mapping tests. G2 is covered by the new real-capture fixture harness and source cohort migration. G3 is covered by source descriptor, transport, criterion, unsupported-diagnostic, and source-limit tests. G4 is covered by fake registered source behavior through the engine and journal. G5 is covered by shared transport, detector, runtime, and artifact-failure fault injection. G6 is optional but specified for approved baselines only. G7, G8, and G9 are split from the deterministic gate and treated as operational health and evidence pipelines, not parser correctness proof.

Remaining implementation risk: several future contracts require code changes, not only tests. In particular, `no_results`, `invalid_source_output`, and `resource_failure` need first-class representation or a stable mapping in `types.py`; parser functions need a way to return explicit no-result evidence; and verifier profiles plus project guidance must be updated together so contributors know which gate blocks merges.


## Context and Orientation

The installable runtime lives under `plugins/job-harness/`. The repository root is `/Users/user/Documents/repos/qa-job-harness`. The plugin source code lives in `plugins/job-harness/src/job_harness/`, and the unit test suite lives in `plugins/job-harness/tests/`.

The main runtime pieces are:

- `plugins/job-harness/src/job_harness/models.py`, which defines `SearchParams`, `RawListing`, `RawSearchRecord`, `JobListing`, and `SearchResults`.
- `plugins/job-harness/src/job_harness/types.py`, which defines source descriptors, search criteria, source state, failure modes, run state, and `SourceStatus`.
- `plugins/job-harness/src/job_harness/base.py`, which defines `BaseScraper` and `BaseBrowserScraper`.
- `plugins/job-harness/src/job_harness/registry.py`, which registers scraper classes by exact source id.
- `plugins/job-harness/src/job_harness/search_engine.py`, which resolves eligible sources, runs them concurrently, writes raw artifacts, records source statuses, and then runs the downstream result pipeline.
- `plugins/job-harness/src/job_harness/http_runner.py`, which executes synchronous HTTP scrapers under a bounded async runner and maps HTTP/helper exceptions to source statuses.
- `plugins/job-harness/src/job_harness/browser_pool.py`, which owns async browser context pooling, deadlines, and shared block detection for browser sources.
- `plugins/job-harness/src/job_harness/source_runtime.py`, which holds engine-level source attempt timeout, run timeout, source-level retry, and backoff settings.
- `plugins/job-harness/src/job_harness/source_retry.py`, which is the manual post-run retry helper used after a run finishes.
- `plugins/job-harness/src/job_harness/run_journal.py`, which writes and reads the durable JSONL run journal, `raw_search.jsonl`, and summaries.
- `scripts/verify_repo.py`, which is the current repository verification entrypoint.

Important terms used in this plan:

`Source` means one registered job data provider selected by exact id, such as `hh_ru`, `habr_career`, `getmatch`, or `career:vk`.

`Scraper` means the Python class that knows how to collect raw job facts from one source. HTTP scrapers implement `search(params)`. Browser scrapers implement `search_with_page(page, params)`.

`Raw listing` means a `RawListing` dataclass containing source-native facts only, before grade estimation, filtering, ranking, dedupe, or presentation truncation.

`Source status` means one per-source summary row represented today by `SourceStatus`. It records state, failure mode, source limit, deadline, elapsed time, criteria diagnostics, attempts, retries, rows written, and error text.

`G2 fixture` means a deterministic parser test case backed by a real captured source artifact such as HTML, JSON, SSR payload, HAR, or a minimized copy that preserves the original structure. A screenshot is useful evidence but not parser input.

`G4 test` means an orchestrator test with fake registered sources. It proves source isolation, deadlines, cancellation, retry behavior, raw artifact writing, and summary behavior.

`G5 test` means a shared fault-injection test at a transport or runtime boundary. It proves classification from HTTP statuses, redirects, page titles, body markers, captcha iframes, network errors, malformed JSON, browser disconnects, pool acquire timeouts, or artifact writer failures.

Current public source inventory from `uv --directory plugins/job-harness run job-harness list-sources --json` on 2026-06-16 contains 19 exact source ids:

- Aggregator HTTP/API/HTML sources: `hirehi`, `hirify`, `staff_am`, `geekjob`, `talento`, `finder_work`, `it_jobs_uz`, `jobturbo`, `getmatch`, and `habr_career`.
- Aggregator browser sources in the HH family: `hh_ru`, `hh_kz`, `hh_uz`, `rabota_by`, and `headhunter_kg`.
- Employer and directory sources: `company_careers`, `company_directory`, `career:ibs`, and `career:vk`.

Current source descriptor contract:

- `SourceDescriptor.group` is one of `aggregator`, `company_career`, `directory`, or `other`.
- `SourceDescriptor.countries` is a tuple of supported country codes. An empty tuple means the source has its own wider or directory-specific behavior.
- `SourceDescriptor.server_criteria` is a frozenset of `SearchCriterion` values that the source can apply natively in the request or source-side query.
- `SourceDescriptor.source_limit` is a positive integer source-local raw collection cap.
- `BaseScraper.transport()` returns `http` or `browser`, but transport is not currently serialized in public `list_sources`.

Current scraper-facing contract:

- `BaseScraper.search(params: SearchParams) -> list[RawListing]` for HTTP scrapers.
- `BaseBrowserScraper.search_with_page(page, params: SearchParams) -> list[RawListing]` for browser scrapers.
- A scraper returning a normal list does not currently communicate explicit `no_results` evidence.
- A scraper can signal a problem by raising exceptions that the runner maps to `SourceStatus`, or by setting legacy `timed_out`.
- A source limit is enforced by the engine when it writes raw listings: `outcome.listings[: outcome.status.source_limit]`.

Current raw artifact contract:

- `SearchEngine.execute(...)` writes `RawSearchRecord(schema_version=1, type="raw_listing", run_id, source, collected_at, listing)` rows to `raw_search.jsonl`.
- The summary contains `raw_search.path`, `raw_search.listings_written`, `raw_search.global_truncation`, `results.path`, and `source_statuses`.
- `max_results` caps downstream presentation results after raw collection, not the raw search artifact.
- Downstream grade assessment, filtering, dedupe, ranking, and final result slicing are owned by `result_pipeline.py` and `experience_engine.py`.

Current status contract:

- `SourceState.OK` with no failure mode means the source completed according to current code, whether it returned one listing or zero listings.
- `SourceState.PARTIAL` with `FailureMode.SLOW_PAGINATION` or `MULTI_STEP_PARTIAL` means the source returned listings but stopped before complete collection.
- `SourceState.TIMEOUT` with timeout-related failure modes represents source-level timeouts.
- `SourceState.ERROR` with failure modes such as `PARSE_ERROR`, `HTTP_4XX`, `HTTP_5XX`, `NETWORK_ERROR`, or `GLOBAL_NETWORK_OUTAGE` represents failures that are not currently split into policy canonical outcomes.
- `SourceState.RATE_LIMITED`, `BLOCKED`, `CANCELLED`, `SKIPPED`, and `SKIPPED_UNSUPPORTED_FLAG` represent rate limits, access blocks, cancellations, and policy skips.
- `SourceStatus.__post_init__` enforces that `state == OK` has no `failure_mode`, and `state != OK` always has a `failure_mode` that belongs under that state.

Current test layout:

- `plugins/job-harness/tests/test_capability_matrix.py` and `test_countries_and_registry.py` cover parts of G3 source catalog behavior.
- `plugins/job-harness/tests/test_hh_ru.py`, `test_habr_career.py`, `test_cis_sources.py`, `test_career_scrapers.py`, and `test_company_careers_scraper.py` cover source-specific parsing and request mapping, but most parser inputs are inline synthetic constants or fake DOMs.
- `plugins/job-harness/tests/test_search_engine.py`, `test_run_registry.py`, `test_run_journal.py`, and `test_mcp_async_surface.py` cover important G4 orchestrator and lifecycle behavior.
- `plugins/job-harness/tests/test_http_common.py`, `test_http_runner.py`, `test_browser_pool.py`, and `tests/_support/fake_browser.py` cover important G5 transport, block detection, and runtime behavior.
- `plugins/job-harness/tests/fixtures/experience_engine_real_world_samples.json` is a useful real-world offline fixture for the grade engine, but it is not a source parser fixture suite.


## Target Contracts

The target testing system must preserve the current useful contracts and add the policy contracts below.

The target source contract is:

- Every registered source has a stable exact id.
- Every source declares `group`, `countries`, `transport`, `source_limit`, supported search criteria, and unsupported requested criteria diagnostics.
- Supported search criteria are binary: a criterion is supported only if the source can enforce it through native request parameters, API filters, structured response fields, or stable DOM markers that belong to the source. Free-text downstream inference does not make a source support that criterion.
- Every supported native request criterion has a request mapping test that proves the outgoing URL, API payload, browser form state, or structured query changes in the smallest observable source-native way.
- Unsupported requested criteria are reported in source summaries and do not fabricate facts, delete raw listings, or skip a source unless explicit policy or strict mode says to skip it.
- `experience_levels` follows the experience policy: HH-family and Habr Career can use native server grade for a single requested level, multi-level requests are collected more broadly and filtered by the centralized grade engine, and best-effort or unsupported sources do not populate native `RawListing.experience`.

The target scraper parser contract is:

- A parser fixture test must exercise parser input, not pre-normalized output.
- A G2 source-specific fixture must come from a captured real artifact or minimized real capture.
- Allowed fixture edits are redaction, removal of irrelevant scripts/styles/assets, shortening repeated records while preserving structure, and timestamp normalization only when timestamps are not the behavior under test.
- Forbidden fixture edits include inventing source pages, changing class names or JSON keys to fit the parser, deleting fields to manufacture missing-field cases, adding unobserved captcha or login pages, and creating a no-result page from memory.
- A source-specific block, captcha, login, geo, VPN, or rate-limit parser case is added only after the real source state is captured or otherwise verified. Generic fake block signals belong in G5.
- A zero-card page without explicit no-result evidence is not success and not no-results. It is `blocked` when shared or source-owned block signals are present; otherwise it is `parse_error`.
- One naturally malformed or broken listing card should not kill the whole source when other cards are valid, but a structurally broken result page should fail closed as `parse_error` rather than `success` or `no_results`.
- Emitted vacancy URLs are absolute, canonical enough for dedupe, and stripped of tracking parameters.

The target outcome contract is the policy canonical taxonomy. During migration, tests may assert both the policy outcome and the current serialized state/failure-mode pair. The mapping must be explicit:

- Policy `success` maps from current `SourceState.OK` only when at least one valid normalized raw listing was written and no stop or failure condition occurred.
- Policy `no_results` has no complete current equivalent. It requires an explicit future state or a stable mapping from a source-owned no-results result. It applies only when the source exposes explicit no-result evidence and zero listings.
- Policy `partial_success` maps from current `SourceState.PARTIAL` only for source-owned bounded partial states such as slow pagination or detail enrichment, and only when no higher-precedence block, rate-limit, timeout, parse, network, or runtime failure occurred.
- Reaching `source_limit` maps to policy `success` with `limit_reached=true`, not `partial_success`.
- Policy `skipped_by_policy` maps from current `SourceState.SKIPPED` and `SKIPPED_UNSUPPORTED_FLAG`.
- Policy `cancelled` maps from user cancellation. Policy `run_timeout` maps from total run deadline cancellation, currently represented through `SourceState.CANCELLED` with `FailureMode.TOTAL_TIMEOUT`.
- Policy `source_timeout` maps from source attempt timeouts, currently `SourceState.TIMEOUT` with `HTTP_TIMEOUT`, `GOTO_TIMEOUT`, or `POOL_ACQUIRE_TIMEOUT`.
- Policy `blocked` maps from `SourceState.BLOCKED` and block-related failure modes.
- Policy `rate_limited` maps from `SourceState.RATE_LIMITED`.
- Policy `http_client_error` maps from `SourceState.ERROR` with `FailureMode.HTTP_4XX`.
- Policy `http_server_error` maps from `SourceState.ERROR` with `FailureMode.HTTP_5XX` unless a retry-after status is classified as rate-limited.
- Policy `network_error` maps from `SourceState.ERROR` with `FailureMode.NETWORK_ERROR` or global network outage when the global outage is produced by shared HTTP/network symptoms.
- Policy `parse_error` maps from `SourceState.ERROR` with `FailureMode.PARSE_ERROR`.
- Policy `invalid_source_output` needs first-class validation or a new failure mode. It applies when a source returns the wrong type, missing required raw fields, malformed normalized records, or more records than the runner allows after validation.
- Policy `resource_failure` needs first-class validation or a new failure mode for browser disconnect, poisoned browser context, worker crash, disk/artifact write failure, pool lifecycle failure, or other local runtime failures.

The target fixture layout is:

    plugins/job-harness/tests/fixtures/scrapers/<source>/<case>/
      input.json
      response.html
      response.json
      network.har
      screenshot.png
      expected.raw.json
      expected.status.json
      meta.json

Use only the files needed for a case. `input.json` describes the search intent. `response.*` or `network.har` is parser input. `expected.raw.json` asserts normalized raw listings. `expected.status.json` asserts the policy canonical outcome and, during migration, the current `state`/`failure_mode` mapping. `meta.json` records source id, captured URL, capture date, capture method, country or browser profile when relevant, redactions and minimizations performed, and why the fixture exists.

The target verification profile contract is:

- G0 static quality runs Ruff, mypy, detect-secrets, and import hygiene checks.
- G1 pure unit tests run without browser, network, filesystem state except temporary test dirs, or live source access.
- G2 parser fixture tests run only against committed real-source fixtures.
- G3 source contract tests cover descriptors, transports, criteria, request mapping, unsupported diagnostics, and source limits.
- G4 orchestrator tests use fake registered sources and assert outcomes, summaries, raw artifacts, retry decisions, cleanup, and isolation.
- G5 fault-injection tests use fake transport/runtime symptoms and assert canonical classification through shared code.
- G6 golden regression tests run only over approved deterministic fixture baselines and normalize volatile fields.
- G7 live smoke, G8 drift monitoring, and G9 access-route checks remain operational gates and are not required for normal merge confidence.


## Plan of Work

Milestone 1 establishes the testing vocabulary and gate wiring without changing source behavior. Add a small policy bridge in tests, for example `plugins/job-harness/tests/_support/outcomes.py`, that maps current `SourceStatus` rows to policy canonical outcome names. The bridge should fail loudly for unknown `SourceState` or `FailureMode` pairs. Add `plugins/job-harness/tests/test_testing_policy_contract.py` or extend `test_run_journal.py` so every current state/failure-mode pair has either a policy mapping or a documented gap. Add a verifier profile such as `blocking` or `deterministic` to `scripts/verify_repo.py` that runs G0 through G6 only. Keep existing `default` behavior if needed, but update repository guidance when the new profile is stable. At the end of this milestone, a developer can run the deterministic gate without public websites, and tests have one place where policy outcomes are tied to current runtime serialization.

Milestone 2 adds the fixture harness before migrating individual parsers. Create `plugins/job-harness/tests/_support/scraper_fixtures.py` with helpers to load `input.json`, one parser input file, `expected.raw.json`, `expected.status.json`, and `meta.json`. The loader must validate required metadata fields and reject missing capture dates, unknown source ids, unsupported file combinations, and expected statuses outside the policy taxonomy. Add a fixture metadata test that walks `plugins/job-harness/tests/fixtures/scrapers/` and validates every case. Add one tiny captured fixture for a low-risk source that already uses structured data, preferably `it_jobs_uz` or `finder_work`, because JSON fixtures are easier to minimize honestly than browser DOMs. At the end of this milestone, the repository has a working fixture shape and one G2 case that would fail if parser extraction changed.

Milestone 3 expands G3 source contract coverage. Bootstrap the registry exactly as CLI and MCP do by importing both `job_harness.scrapers` and `job_harness.scrapers.career`. Add or revise tests so the full 19-source catalog from public `list_sources` is covered. For every source, assert stable id, group, countries, transport from `cls.transport()`, positive `source_limit`, and `server_criteria` values drawn from `SearchCriterion`. For every declared supported native request criterion, add the smallest request-mapping assertion. Existing tests already cover many examples: HH remote, salary, freshness, and single-level experience URL parameters; Habr qualification and salary parameters; Finder, Hirify, and IT-Jobs.uz salary URL parameters; Getmatch specialization matching; IBS query/remote/location SEF segments; VK specialty/search/remote URL parameters. Fill gaps by source rather than by adding broad assertions that cannot prove native behavior. At the end of this milestone, a source cannot declare support for a criterion without at least one test proving how that criterion reaches the source.

Milestone 4 migrates parser tests to G2 by source cohort. Do not move an inline synthetic constant into the fixture directory and call it real. For each source, capture a real artifact with a broad stable query such as `QA`, remove secrets and irrelevant bulk, preserve the DOM/API shape under test, write `meta.json`, and assert normalized raw fields from `expected.raw.json`. Start with HTTP/API and SSR sources in this order: `it_jobs_uz`, `finder_work`, `hirify`, `getmatch`, `staff_am`, `jobturbo`, `habr_career`, `hirehi`, `geekjob`, and `talento`. Then handle browser-heavy sources: `career:ibs`, `career:vk`, and the HH family. For HH-family sources, decide whether one captured fixture per host is needed for country-specific URL and host behavior, or whether shared parser structure plus per-host request-mapping tests is enough; record the decision in this plan when implementing. `company_directory` should use bundled directory JSON as deterministic input, not a fake website. `company_careers` should use captured or committed company target records for promotion into listings, while live employer probing remains operational. At the end of this milestone, source-specific parser correctness is proved by real artifacts, and old inline tests are either removed, downgraded to G1/G3 helper tests, or kept only where they test generic parsing helpers without claiming G2 status.

Milestone 5 fills G4 orchestrator tests. Add fake registered sources that complete successfully, return explicit no-results, return source-owned partial success, sleep near timeout, never resolve, raise synchronously, raise asynchronously, return the wrong type, return malformed raw output, return too many records, return records missing required fields, and remain active during cancellation or total run timeout. Each test must drive the real `SearchEngine`, `RunJournalWriter`, and public source status construction. Do not create statuses by hand except in tests whose explicit subject is status serialization. Assert canonical outcome via the bridge, current state/failure-mode during migration, user-visible summary shape, raw artifact rows written or intentionally absent, retry decision when applicable, and isolation of later sources. At the end of this milestone, one bad source cannot poison a run, and invalid source output is no longer hidden as a generic parse exception without a policy-level assertion.

Milestone 6 fills G5 shared fault-injection coverage. Extend `test_http_common.py`, `test_http_runner.py`, `test_browser_pool.py`, and related support fakes so every transport and runtime outcome assigned to G5 in the policy is tested from observable symptoms. HTTP tests should cover 429 and retry-after, retryable 5xx, non-retryable 4xx, anti-bot 403/451, login redirects, malformed JSON, DNS/TLS/socket failures, and retry budget boundaries. Browser tests should cover final URLs such as `/vpncheck` and `/vpncheeck`, title/body/captcha iframe markers, browser disconnect, pool acquire timeout, poisoned context, shutdown, and page creation/resource failures. Artifact tests should inject disk or writer failure and classify it as `resource_failure` once the runtime supports that outcome. These tests may use synthetic signals because they prove shared detectors, not source-specific parser behavior. At the end of this milestone, source-specific fake captcha pages are unnecessary because the shared classifier has deterministic coverage.

Milestone 7 adds G6 golden regression tests only after enough G2 fixtures exist. Create a small reviewed baseline for representative fixture suites, not for every source on day one. Normalize volatile fields such as timestamps, run ids, local paths, elapsed times, attempt timing, unordered diagnostics, and raw capture provenance before comparison. Store golden snapshots near the fixture cases or under `plugins/job-harness/tests/golden/` with clear review notes. Add an update command or documented process that requires reviewer approval before snapshots are changed. At the end of this milestone, broad refactors can be checked against reviewed behavior without blessing known-wrong output as truth.

Milestone 8 splits operational health from merge proof. Modify `scripts/verify_repo.py` and repository guidance so deterministic blocking checks are separate from G7 live smoke. Keep `python scripts/verify_repo.py live` for MCP smoke, registered live source smoke, company batch smoke, and future access-route checks. Live smoke assertions must stay structural: process exits cleanly, output is valid structured data, every source reports a canonical outcome, successful listings have required fields, source summaries include elapsed time and collection limits, and failures are classified. Do not assert exact title, company, salary, count, or ordering from live sites. Add drift-monitoring guidance that says live failures produce evidence for a new G2 fixture or G5 fault test before parser logic changes. At the end of this milestone, live source instability cannot force developers to weaken deterministic tests.

Milestone 9 updates documentation and maintenance guidance. Update `AGENTS.md`, `.agents/skills/job-harness-scraper-development/references/testing-policy.md` only if the policy itself changes, and any local test README or comments added during implementation. Document the fixture capture workflow, the deterministic gate command, the live health command, and the rule that source-specific fixtures must be real captures. If the canonical merge command changes from `python scripts/verify_repo.py full` to a deterministic profile, update every repository instruction that still names the old blocking command. At the end of this milestone, a novice maintainer knows exactly where to put fixtures, which tests prove a scraper change, and why live smoke failures are handled separately.


## Concrete Steps

Work from the repository root:

    cd /Users/user/Documents/repos/qa-job-harness

Before implementing a milestone, inspect the current catalog:

    uv --directory plugins/job-harness run job-harness list-sources --json

Expected shape: a JSON object keyed by exact source id. On 2026-06-16 it contained 19 ids: `hirehi`, `hirify`, `staff_am`, `geekjob`, `talento`, `finder_work`, `it_jobs_uz`, `jobturbo`, `getmatch`, `company_careers`, `company_directory`, `habr_career`, `hh_ru`, `hh_kz`, `hh_uz`, `rabota_by`, `headhunter_kg`, `career:ibs`, and `career:vk`.

Run the current deterministic unit suite while working:

    uv --directory plugins/job-harness run python -m unittest discover -s tests -v

Run static checks after code changes:

    uv --directory plugins/job-harness run ruff check src scripts tests ../../scripts
    uv --directory plugins/job-harness run mypy src/job_harness scripts tests ../../scripts
    python scripts/verify_repo.py secrets

After Milestone 1, run the new deterministic gate. The exact profile name must be whatever the implementation adds, for example:

    python scripts/verify_repo.py deterministic

Expected result: Ruff, mypy, detect-secrets, unit tests, G2 fixture tests, G3 contract tests, G4 orchestrator tests, G5 fault tests, and any approved G6 golden tests pass without requiring network access to public job sites.

When adding a G2 fixture, create one case directory:

    mkdir -p plugins/job-harness/tests/fixtures/scrapers/<source>/<case>

Populate only the files the case needs. For example, an API source may need:

    input.json
    response.json
    expected.raw.json
    expected.status.json
    meta.json

A browser source may need:

    input.json
    response.html
    screenshot.png
    expected.raw.json
    expected.status.json
    meta.json

Do not add cookies, tokens, authenticated user data, personal contact data, or private payment data. If a capture includes such data, redact it in a way that cannot change parser behavior and record the redaction in `meta.json`.

When changing request-mapping or source contract tests, run focused tests:

    uv --directory plugins/job-harness run python -m unittest \
      tests.test_capability_matrix \
      tests.test_countries_and_registry \
      tests.test_hh_ru \
      tests.test_habr_career \
      tests.test_cis_sources \
      tests.test_career_scrapers \
      tests.test_company_careers_scraper

When changing orchestrator or runtime classification tests, run focused tests:

    uv --directory plugins/job-harness run python -m unittest \
      tests.test_search_engine \
      tests.test_http_runner \
      tests.test_http_common \
      tests.test_browser_pool \
      tests.test_run_journal \
      tests.test_source_retry \
      tests.test_mcp_async_surface

Run live health checks only when diagnosing access or preparing a release-health report:

    python scripts/verify_repo.py live

Expected result for live checks: the command may report access-limited, blocked, partial, timeout, or no-results outcomes when those outcomes are canonical and structurally valid. A live failure should create or update evidence, not automatically loosen deterministic tests.


## Validation and Acceptance

Acceptance is behavior-based.

For G0, the deterministic gate must run Ruff, mypy, detect-secrets, and the unit suite successfully from the repository root. A formatting, type, or secret regression must fail before live checks run.

For G1, request mapping and pure helper tests must run without browser or network. Examples include HH URL parameters for remote, salary, freshness, and single-level grade; Habr URL parameters for qualification and salary; Finder/Hirify/IT-Jobs.uz salary parameters; Getmatch specialization mapping; IBS SEF filter segments; and URL canonicalization helpers.

For G2, at least one parser fixture per migrated source must use a real captured artifact and must fail if a required extracted field changes. A fixture with missing `meta.json`, missing capture date, unknown source id, invented outcome, or an expected status outside the canonical taxonomy must fail the fixture metadata test.

For G3, every source returned by public `list_sources` must have a contract test for group, countries, transport, source limit, server criteria, and unsupported diagnostics. A source declaring `salary_from`, `freshness`, `remote_only`, `country`, `location`, `query`, or `experience_levels` support must have a concrete request-mapping or structured-field test. A source that does not support a requested criterion must still collect raw listings and list that criterion in `unsupported_requested_criteria`, unless explicit policy skips it.

For G4, fake source behavior must drive real engine outcomes. A fast success source must write raw records. An explicit no-results source must produce policy `no_results` and zero raw records. A hanging source must produce `source_timeout` or `run_timeout` as appropriate without blocking unrelated sources. A source that raises must not prevent another source from completing. A source returning malformed output, too many rows, or missing required raw fields must produce `invalid_source_output`. Cancellation must write a final cancelled run state. Retry tests must preserve attempts, retries, final outcome, and final raw listing set.

For G5, synthetic transport and runtime symptoms must classify through shared detectors. HTTP 429 with retry-after must become `rate_limited`; retryable 5xx must follow retry policy; non-rate-limit 4xx must become `http_client_error`; malformed JSON must become `parse_error`; DNS/TLS/socket failures must become `network_error`; browser block URLs, titles, bodies, and captcha iframes must become `blocked`; browser pool acquire timeout and browser resource failures must map to timeout or `resource_failure` according to the final outcome bridge.

For G6, golden tests are accepted only after reviewed baselines exist. Updating a golden snapshot must be a deliberate behavior review, not an automatic fixture rewrite.

For G7, live smoke tests must assert structure and canonical classification, not exact public job inventory. Live smoke failures are accepted as operational health events when they produce evidence for G2, G5, G8, or G9 follow-up.

For G8, drift monitoring is accepted when it records canonical outcome distribution, raw count, required-field completeness, selector counts, final URL/status, block markers, latency, pagination depth, detail success rate, source configuration, country/profile, first failing time, and last known good time for a detected change.

For G9, access-route checks are accepted when they report source by country, proxy/provider, browser profile, headless/headful/stealth mode, request rate, and time-window behavior separately from parser correctness.

The final migration is complete when a scraper maintainer can change any registered source and answer the review checklist with evidence: G0 passes, G1 request/helper tests cover deterministic code, G2 real fixtures cover source parsing, G3 contracts match source declarations, G4 proves orchestrator isolation, G5 proves touched transport/runtime outcomes, G6 changes are reviewed if snapshots changed, and live or drift findings have been converted into deterministic fixtures before parser logic is changed.


## Idempotence and Recovery

Most changes in this migration are additive. Adding a fixture case, adding a test helper, or adding a verifier profile can be repeated safely as long as paths are stable and metadata is deterministic.

If a fixture capture contains secrets or personal data, do not commit it. Redact the source artifact first, record the redaction in `meta.json`, and rerun the fixture metadata test. If redaction would change parser behavior, discard the capture and capture a safer artifact.

If a live source changes while implementing a parser fixture, do not weaken parser assertions to match an unknown live page. Preserve the new live evidence, classify it as block, rate-limit, no-results, parse drift, network, or access route, then add the appropriate G2 or G5 deterministic test before changing parser code.

If the outcome bridge reveals a current status that cannot map cleanly to the policy, do not hide it in a generic bucket. Add a documented gap in this plan and either add a new `FailureMode`, a canonical outcome field, or a targeted migration step. The bridge should fail until the gap is resolved.

If a deterministic test fails after adding live-gate separation, fix the deterministic behavior. If a live smoke fails because of network, anti-bot, geo, source inventory churn, or access route, record a health event and rerun `python scripts/verify_repo.py live` once after checking connectivity. Do not patch deterministic tests or source contracts to pass a transient live failure.

If implementation partially lands and the suite becomes noisy, roll forward by narrowing the failing milestone. For example, if a G2 fixture harness is correct but a migrated source fixture is questionable, keep the harness and mark that source case incomplete rather than deleting the harness.


## Artifacts and Notes

Current repository evidence gathered while authoring this plan:

    rg --files | rg '(^tests/|/tests/|fixtures|verify_repo|pyproject.toml)'

This showed the current test suite under `plugins/job-harness/tests/` and only one committed fixture file under `plugins/job-harness/tests/fixtures/experience_engine_real_world_samples.json`.

    uv --directory plugins/job-harness run job-harness list-sources --json

This returned the current 19-source public catalog listed in `Context and Orientation`.

    rg -n "class SourceState|class FailureMode|class SourceStatus" \
      plugins/job-harness/src/job_harness/types.py

This confirmed the current closed state/failure-mode taxonomy and the invariant that non-OK statuses require failure modes.

Author review checklist applied to this plan:

- Internal contradictions: resolved. The plan does not require immediate runtime renaming and instead uses a transition bridge for current and target outcome names.
- Repository fit: verified against current files and commands. The plan names the existing tests, fixture directory, source catalog, runtime modules, and verifier profile behavior observed on 2026-06-16.
- Ambiguity: reduced by naming concrete source cohorts, fixture paths, commands, expected artifacts, and acceptance behavior.
- Policy coverage: G0 through G9 are all covered. The plan distinguishes deterministic merge proof from live operational health and states where synthetic inputs are allowed.
- Known uncovered runtime gaps: explicit `no_results`, `invalid_source_output`, and `resource_failure` need implementation support before their tests can pass as first-class outcomes.


## Interfaces and Dependencies

Prefer standard library code and existing project helpers. Do not add new third-party dependencies for the test harness unless a later implementation milestone proves a strong need and records the decision in this plan.

The fixture helper should expose stable test-only functions similar to:

    SCRAPER_FIXTURE_ROOT = Path("plugins/job-harness/tests/fixtures/scrapers")

    def iter_scraper_cases() -> Iterator[ScraperFixtureCase]:
        ...

    def load_scraper_case(source: str, case: str) -> ScraperFixtureCase:
        ...

    def assert_raw_listings_match(actual: list[RawListing], expected_path: Path) -> None:
        ...

    def assert_status_matches_policy(status: SourceStatus, expected_path: Path) -> None:
        ...

The outcome bridge should expose stable test-only names similar to:

    class CanonicalOutcome(StrEnum):
        SUCCESS = "success"
        NO_RESULTS = "no_results"
        PARTIAL_SUCCESS = "partial_success"
        SKIPPED_BY_POLICY = "skipped_by_policy"
        CANCELLED = "cancelled"
        SOURCE_TIMEOUT = "source_timeout"
        RUN_TIMEOUT = "run_timeout"
        BLOCKED = "blocked"
        RATE_LIMITED = "rate_limited"
        HTTP_CLIENT_ERROR = "http_client_error"
        HTTP_SERVER_ERROR = "http_server_error"
        NETWORK_ERROR = "network_error"
        PARSE_ERROR = "parse_error"
        INVALID_SOURCE_OUTPUT = "invalid_source_output"
        RESOURCE_FAILURE = "resource_failure"

    def canonical_outcome_from_status(status: SourceStatus) -> CanonicalOutcome:
        ...

During migration, this may live under `plugins/job-harness/tests/_support/` instead of production `types.py`. Once runtime contracts are updated, move the canonical enum into production code only if source summaries or MCP results need to expose the policy names directly.

The future scraper-result interface, if needed to represent explicit no-results without exceptions, should be small and source-facing:

    @dataclass(frozen=True)
    class SourceSearchResult:
        listings: list[RawListing]
        outcome: CanonicalOutcome
        pages_visited: int | None = None
        evidence: str | None = None

The final scraper contract can then become `search(params) -> SourceSearchResult` and `search_with_page(page, params) -> SourceSearchResult`. During transition, runners may adapt legacy `list[RawListing]` returns, but the final policy-complete state should not rely on an empty list alone to distinguish `no_results`, `parse_error`, or `blocked`.

Change note: This plan was created on 2026-06-16 to make the migration from the current scraper test suite to the project testing policy executable from a single file. It intentionally records both current repository contracts and future policy contracts because several policy requirements cannot be satisfied by test-only changes.
