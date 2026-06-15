# Introduce a Strict Search Layer

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository does not currently check in `PLANS.md`; this file follows the plan specification from `/Users/user/.codex/skills/public/plan-file-author/references/PLANS.md`. This plan is self-contained and repeats the relevant context from `plans/resilient-scraping.md` rather than requiring the reader to know that earlier plan.


## Purpose / Big Picture

After this change, job-harness will have an explicit search layer whose only job is to scrape selected sources as broadly and honestly as possible and write the raw results to disk. A high-level agent will be able to ask "what exact sources exist?", choose all sources, one source, a group of sources, or a named subset, and then receive a raw search artifact that contains every listing the selected sources returned within per-source safety limits.

The important user-visible behavior is that `sources=all` no longer means "return at most `max_results` total and let that same number cap each scraper." Instead, all eligible sources are attempted, each source has its own local hard limit, failures are recorded per source, and the raw search artifact is not globally truncated. Filtering, grade estimation, ranking, deduplication, and "best match" selection happen after the search layer and can produce separate downstream artifacts without changing the raw search evidence.


## Progress

- [x] (2026-06-08 08:07Z) Created branch `codex/search-layer-architecture`.
- [x] (2026-06-08 08:07Z) Read the plan authoring specification and existing repository plans.
- [x] (2026-06-08 08:07Z) Inspected the current search engine, registry, source capabilities, MCP tools, CLI, run journal, retry helpers, and scrapers.
- [x] (2026-06-08 08:07Z) Captured the current source inventory from `uv --directory plugins/job-harness run job-harness list-sources --json`.
- [x] (2026-06-08 08:07Z) Authored this architecture and implementation plan.
- [x] (2026-06-08 14:29Z) Revised pagination language so pagination is a source-wide search-layer contract, not an HH-specific concern.
- [x] (2026-06-08 14:45Z) Added explicit source catalog and raw artifact contracts, including field definitions and JSON examples.
- [x] (2026-06-08 14:52Z) Removed `best_effort` and client-side filtering from the target search-layer contract; search criteria are now a closed server-only list.
- [x] (2026-06-08 15:06Z) Simplified source catalog to the fields needed by agents, replaced per-listing `applied` flags with typed per-source run summaries, and added typed dataset contracts.
- [x] (2026-06-08 15:23Z) Investigated server-side salary filters, added `salary_from` as a possible server criterion, and wrote the MCP tool contract explicitly.
- [x] (2026-06-08 15:32Z) Removed the implied `JobListing`-to-raw adapter from the target design; scrapers now return `RawListing` directly and the search layer only wraps it for storage.
- [x] (2026-06-08 15:38Z) Linked search-layer responsibilities to the typed, MCP, and runtime contracts, audited current timeout behavior, and added an explicit source runtime policy.
- [x] (2026-06-08 15:52Z) Made listing detail fields explicitly optional source facts and moved scraper runtime controls out of the MCP request into service configuration.
- [x] (2026-06-09 06:09Z) Narrowed the runtime plan to separate already-existing component defaults from the small set of new engine-level source runtime controls.
- [x] (2026-06-09 06:23Z) Added a compact current-to-target file map so implementers can see which files should change without expanding the architecture text.
- [x] (2026-06-09 07:38Z) Implemented the source descriptor and source group model.
- [x] (2026-06-09 07:38Z) Split raw search artifacts from downstream filtered result exports.
- [x] (2026-06-09 07:38Z) Added source-local limit policy and stopped using presentation `max_results` as scraper depth.
- [x] (2026-06-09 07:38Z) Added honest server-only freshness support with no unsupported-source fallback.
- [x] (2026-06-09 07:38Z) Added a uniform source retry policy for HTTP and browser sources.
- [x] (2026-06-09 07:38Z) Updated MCP, CLI, agent guidance, and tests.
- [x] (2026-06-09 07:38Z) Ran the live search `middle qa manual` in Russia with remote-only and middle filters, recorded source health, and fixed the observed company-career timeout cleanup problem.
- [x] (2026-06-09 08:02Z) Ran `python3 scripts/verify_repo.py full` after implementation; the full gate passed.


## Surprises & Discoveries

- Observation: The current `SearchEngine` already dispatches eligible sources concurrently, records source statuses, and writes a durable journal.
  Evidence: `plugins/job-harness/src/job_harness/search_engine.py` creates one task per eligible source in `execute`, and `plugins/job-harness/src/job_harness/run_journal.py` writes fsynced JSONL events.

- Observation: The current raw journal is not a pure search-layer artifact because listings are annotated with grade assessments before they are written.
  Evidence: `SearchEngine.execute` calls `_annotate_listing_from_outcome(listing, outcome)` immediately before `journal.write_listing(...)`.

- Observation: The current `search_results(run_id)` export is intentionally filtered, deduped, and truncated, not raw.
  Evidence: `plugins/job-harness/src/job_harness/run_journal.py` function `materialize_listings` rebuilds the filter plan, applies filters, dedupes, orders by grade match, and slices to `max_results`.

- Observation: Pagination is a general source concern, not an HH-specific concern. Any source that exposes pages, cursors, offsets, or "next" links should keep traversing them until the source-local limit, source deadline, or source-native end marker is reached.
  Evidence: The HH-family currently paginates by clicking `[data-qa="pager-next"]` until `len(listings) >= self.max_results`; Habr Career follows `next_href`; Getmatch uses API `limit` and may need offset/cursor expansion if the API supports more pages.

- Observation: HH-family scrapers do not only read the first page today, but their pagination is still capped by `self.max_results`.
  Evidence: `plugins/job-harness/src/job_harness/scrapers/hh_ru.py` loops while `len(listings) < self.max_results` and clicks `_PAGER_NEXT`.

- Observation: For most sources, the same `request.max_results` value currently controls both scraper depth and final presentation size.
  Evidence: `_default_scraper_factory(name, request.max_results, ...)` receives `request.max_results`, and later `SearchEngine.execute` slices `final = deduped[: request.max_results]`.

- Observation: `company_careers` already follows the desired exhaustive-source principle better than other sources: it does not let `max_results` cap the company set.
  Evidence: `CompanyCareersScraper.search_with_page` calls `_load_company_targets(..., max_results=None)`, and `tests/test_company_careers_scraper.py` has `test_scraping_scope_is_not_capped_by_requested_result_limit`.

- Observation: Freshness is not a current search parameter.
  Evidence: `SearchRequest` and `SearchParams` have `query`, `country`, `remote_only`, `experience_levels`, `location`, and `max_results`, but no `freshness_days`, `posted_after`, or freshness criterion flag.

- Observation: Several sources expose a `posted_date` field, but there is no policy that distinguishes source-native freshness from downstream date filtering.
  Evidence: `hirify`, `staff_am`, `finder_work`, `it_jobs_uz`, and `getmatch` write `posted_date`; no engine code reads that field for source selection or freshness enforcement.

- Observation: Several aggregators expose salary values in returned listings, but returned salary is raw evidence, not proof of a server-side salary search criterion.
  Evidence: `hh_ru`, `habr_career`, `hirify`, `finder_work`, `it_jobs_uz`, and `getmatch` populate `JobListing.salary` or salary-derived text today, while their URL/API builders currently only send query/category/specialization-style parameters.

- Observation: Server-side salary lower-bound search appears feasible for some aggregators and unproven for others.
  Evidence: HH official OpenAPI documentation at `https://api.hh.ru/openapi/redoc` for vacancy search includes a `salary` query parameter and `only_with_salary`/salary labels; live probes showed Habr Career `salary=200000` changes result totals, Finder API `salary_from=200000` changes counts, Hirify API `salary_from=4000` changes totals, and IT-Jobs.uz API `salaryMin=3000` changes totals. Comparable Getmatch probes with `salary`, `salary_from`, `salary_min`, and `sa` did not change the returned offer set.

- Observation: The target raw dataset contract is not exactly the current `JobListing.to_dict()` output.
  Evidence: `JobListing` currently has native `experience`, but `to_dict()` omits it and instead emits downstream assessment fields; `remote` defaults to `False`, which cannot distinguish explicitly non-remote from unknown.

- Observation: `list_sources` already gives exact source ids, but it does not expose semantic source groups.
  Evidence: `registry.get_scraper_metadata()` returns display name, countries, transport, browser requirements, and capabilities, but no `group`.

- Observation: There is HTTP retry behavior, manual source retry, and some scraper-local retry behavior, but no uniform source-level retry policy that covers both HTTP and browser sources.
  Evidence: `scrapers/http_common.py` retries URL fetches, `source_retry.py` validates manual `search_retry`, and `HabrCareerScraper._fetch_html` has its own `_FETCH_ATTEMPTS`, while `SearchEngine._run_browser_source` does not retry source attempts.

- Observation: Runtime budgets already exist, but they are spread across request, scraper, browser, HTTP, and engine code rather than documented as one source contract.
  Evidence: `SearchRequest` currently has `source_timeout_ms=30_000` and `total_timeout_ms=90_000`; `BaseScraper.remaining_timeout_ms()` tracks per-source time; `BrowserPool` has deadline-aware context acquisition, optional explicit `acquire_timeout_ms`, and `page_timeout_ms=30_000`; `http_common.py` uses `DEFAULT_RETRIES=2`, a 1 second minimum attempt timeout, and a 10 second maximum attempt timeout; `SearchEngine.execute` wraps the whole run in `asyncio.wait_for(..., timeout=total_timeout_ms/1000)`.

- Observation: Most low-level runtime knobs do not need to be newly designed; they already exist in component constructors or helper constants.
  Evidence: `BrowserPool.__init__` already exposes `max_contexts=2`, `page_timeout_ms=30_000`, deadline-aware `acquire_timeout_ms=None`, and `recycle_after_consecutive_hangs=2`; `HttpRunner.__init__` already exposes `max_workers=8`, `cooldown_threshold=4`, and `cooldown_window_s=10.0`; `scrapers/http_common.py` already has `DEFAULT_RETRIES=2`, `MIN_ATTEMPT_TIMEOUT_S=1.0`, `MAX_ATTEMPT_TIMEOUT_S=10.0`, deterministic network backoff, HTTP 429/503 `Retry-After` handling, and terminal HTTP 4xx / parse-error behavior.

- Observation: `description`, `requirements`, and `skills` exist in `JobListing`, but current scraper coverage is uneven.
  Evidence: `models.py` defines all three fields; HH and Habr Career extract description and skills from detail/card pages; several CIS API sources extract description, requirements, and skills; company directory and company career sources use profile text and stack tags; many listing-card-only paths leave requirements empty. Therefore these fields cannot be treated as universally available search-layer facts.


## Decision Log

- Decision: The search layer will own scraping and source-native constraints only; it will not own grade estimation, ranking, deduplication, or "best match" filtering.
  Rationale: The user asked for a clear responsibility boundary. Scrapers may extract facts that a source directly provides, but inferred fields such as estimated grade must be downstream analysis.
  Date/Author: 2026-06-08 / Codex

- Decision: Keep exact source ids as the primary user-facing selector and add source groups as metadata and optional convenience selectors.
  Rationale: The agent must be able to choose concrete names from `list_sources`, while groups make broad strategies easier to explain and execute.
  Date/Author: 2026-06-08 / Codex

- Decision: `max_results` remains a presentation/export limit for compatibility, but it must stop limiting raw scraping depth.
  Rationale: The current public tools already use `max_results`; changing its meaning abruptly is unnecessary. A new per-source limit policy can provide local hard caps without globally truncating the raw artifact.
  Date/Author: 2026-06-08 / Codex

- Decision: `sources=all` means "attempt every eligible selected source and record every source outcome," not "all sources must succeed."
  Rationale: Job sites fail, block automation, time out, or return partial data. The correct exhaustive behavior is fail-open with source diagnostics, not fail-fast.
  Date/Author: 2026-06-08 / Codex

- Decision: Freshness will be a declared search criterion and can be enforced only through a source-native URL/API parameter that filters on the server side.
  Rationale: The search layer must not perform client-side filtering or date interpretation. If a source merely returns a posted date, that date is raw evidence for downstream processing, not proof that the search layer applied freshness.
  Date/Author: 2026-06-08 / Codex

- Decision: Listings with missing posted date must remain in raw search output unless a server-side source parameter already excluded them before collection.
  Rationale: The user asked that if vacancy age is not indicated, the listing should still be collected. The search layer records whether freshness was applied by the source; downstream processing may later decide how to handle missing dates.
  Date/Author: 2026-06-08 / Codex

- Decision: Source retries should be source-level and policy-driven, while `search_retry` remains the manual retry tool for a finished run.
  Rationale: Fetch-level retries do not cover browser timeouts, rate limits, or whole-source failures. Manual retry is still valuable after the agent inspects source states.
  Date/Author: 2026-06-08 / Codex

- Decision: Pagination is part of each scraper's source-native collection contract and must be implemented for every source that exposes a reliable pagination mechanism.
  Rationale: Broad search coverage cannot depend on first-page scraping. The search layer's local source limit should cap traversal safely, but it should not prevent any paginated source from going beyond page one when more results are available.
  Date/Author: 2026-06-08 / Codex

- Decision: Source metadata will expose only fields the agent or contract tests need: `group`, `countries`, `server_criteria`, and `source_limit`.
  Rationale: `display_name` is presentation-only, `transport` is engine-internal, and `native_fields` duplicates the raw dataset contract. Keeping them out of `list_sources` avoids making agents depend on implementation details.
  Date/Author: 2026-06-08 / Codex

- Decision: Use one local `source_limit` per source instead of separate `default` and `hard` limits in v1.
  Rationale: The user requirement is a local cap per source, not a public deep-crawl override. One source-local hard stop is easier to reason about and test. If a future use case needs per-run overrides, that should be a separate plan change with its own safety rules.
  Date/Author: 2026-06-08 / Codex

- Decision: Search-layer criteria support values are only `server` and `unsupported`; there is no target `client` or `best_effort` support mode in the search layer.
  Rationale: Scrapers must only apply constraints that the source itself accepts as query/API parameters. Structured fields returned by a source are raw evidence, and any interpretation or filtering based on those fields belongs to the downstream result pipeline.
  Date/Author: 2026-06-08 / Codex

- Decision: Add `salary_from` as a first-class search-layer criterion, but only as a server-side lower-bound criterion.
  Rationale: Some aggregators can narrow search by salary, and the useful user input is "salary from". The search layer must still avoid local salary parsing/filtering: a source declares `salary_from` support only when it maps the amount to a native URL/API/form parameter with a tested, unambiguous currency behavior.
  Date/Author: 2026-06-08 / Codex

- Decision: Raw listing records will not carry per-criterion `applied` booleans.
  Rationale: `applied` is ambiguous on a listing row: a criterion is applied per source request, not per listing. The run summary will instead record `requested_criteria`, `server_criteria_used`, and `unsupported_requested_criteria` for each source.
  Date/Author: 2026-06-08 / Codex

- Decision: The MCP tool contract is the public serialization of `Typed Contracts`, not a separate ad hoc schema.
  Rationale: Agents call MCP tools, so the plan must state the MCP request and response shape explicitly. Keeping MCP schemas tied to the typed contracts prevents drift between CLI, MCP, journal, and tests.
  Date/Author: 2026-06-08 / Codex

- Decision: `RawListing` is the canonical target return type of scrapers; `RawSearchRecord` is only the storage envelope added by the search layer.
  Rationale: A separate `JobListing` to raw serializer would create an unnecessary intermediate format and another place for hidden inference. Scrapers already know which fields are source facts, so they should return raw normalized facts directly. The search layer adds run metadata such as `run_id` and `collected_at`; scrapers must not know those storage concerns.
  Date/Author: 2026-06-08 / Codex

- Decision: Source timeout, retry, and limit behavior is a named runtime contract, not an implementation detail hidden inside individual scrapers.
  Rationale: The search layer cannot be honest or exhaustive if one scraper silently uses different timeout, retry, pagination, or partial-result semantics. A shared runtime policy lets tests prove when a source ended normally, hit `source_limit`, timed out, retried, was rate-limited, or produced partial evidence.
  Date/Author: 2026-06-08 / Codex

- Decision: `description`, `requirements`, and `skills` remain optional raw listing fields and must be populated only from source-native listing cards, source APIs, employer profiles, or detail pages already visited under the source runtime policy.
  Rationale: Some current scrapers can extract these facts, but many cannot. The raw contract should preserve available evidence without forcing every source into extra detail crawling or inventing requirements from free text.
  Date/Author: 2026-06-08 / Codex

- Decision: Runtime behavior is controlled by service/component settings, not by agent-facing MCP request parameters; the new `SourceRuntimeConfig` owns only engine-level deadlines, source-level retry, source-level backoff, and company probe timeout.
  Rationale: Timeout, retry, backoff, browser pool, HTTP fetch, and cooldown knobs are operational safety settings. Exposing only some of them to the agent would create an incomplete and misleading public API. Agents choose sources and search criteria; the service owns scraper runtime behavior, while existing low-level component defaults stay in their current modules.
  Date/Author: 2026-06-08 / Codex

- Decision: `SourceRuntimeConfig` v1 will include only engine-level controls that are missing or currently exposed through public search requests: total run timeout, source attempt timeout, per-company probe timeout, source-level retry count, and source-level retry backoff. Existing `BrowserPool`, `HttpRunner`, and `http_common.py` defaults remain in those components and are documented as current behavior rather than duplicated in the new config.
  Rationale: The previous config draft mixed new policy with already-implemented low-level settings, making the plan look larger than the required change. The implementation needs a small service config for cross-source orchestration, while preserving existing component defaults and tests.
  Date/Author: 2026-06-09 / Codex

- Decision: Keep a brief current-to-target file map in this plan.
  Rationale: The plan already defines the target contracts in detail. A compact file map makes the migration path explicit and reduces room for implementers to invent extra layers or touch unrelated runtime components.
  Date/Author: 2026-06-09 / Codex


## Outcomes & Retrospective

Status: implemented and verified. `python3 scripts/verify_repo.py full` passed after the implementation, plan update, live search, and verifier/runtime fixes.

New user-facing features and contract changes:

- `list_sources` now exposes a strict descriptor catalog keyed by exact source id: `group`, `countries`, `server_criteria`, and `source_limit`.
- Search requests can select exact `sources` and/or semantic `source_groups` such as `aggregator`, `company_career`, and `directory`.
- The search layer writes `raw_search.jsonl` as a pure raw evidence artifact and keeps downstream `results.json` separate.
- `max_results` is now a presentation/export limit; raw scraper depth is controlled by each source's `source_limit`.
- `salary_from` and `freshness_days` are first-class criteria, but only source-native server filters are applied. Unsupported sources keep all returned rows and report unsupported criteria in their source status.
- Scrapers now return `RawListing` facts, and the search layer wraps those facts as `RawSearchRecord` rows. Grade estimation, ranking, dedupe, and slicing moved into `ResultPipeline`.
- Source statuses now report requested criteria, server criteria used, unsupported requested criteria, attempts, retries, elapsed time, deadlines, rows written, source limits, and failure diagnostics.
- Source-level retry now covers both HTTP and browser sources for zero-listing transient failures. Manual retry remains a separate finished-run workflow.
- Runtime timeout and retry controls moved out of the public MCP/CLI request surface into `SourceRuntimeConfig`.

Live search verification:

    uv --directory plugins/job-harness run job-harness search \
      --query "middle qa manual" --country RU --remote-only \
      --experience-levels middle \
      --source-groups aggregator,company_career \
      --max-results 20 --format json \
      --output /tmp/job-harness-middle-qa-manual-final.json

The live run wrote 297 raw rows and returned 20 downstream presentation rows. Relevant middle manual QA matches were present from multiple sources, including Hirehi and Hirify. Expected country filtering skipped non-RU regional sources: `staff_am`, `it_jobs_uz`, `hh_kz`, `hh_uz`, `rabota_by`, and `headhunter_kg`.

Scraper health from the live run:

- Working with rows: `hirehi`, `hirify`, `geekjob`, `talento`, `finder_work`, `getmatch`, `habr_career`, `hh_ru`, `career:ibs`, and `career:vk`.
- Working with zero rows for this query: `jobturbo`.
- `getmatch` and `habr_career` hit their source limits, which is expected limit protection rather than a parser failure.
- `career:vk` initially hit a transient browser failure and then succeeded after the new source-level retry policy.
- `company_careers` timed out during the broad grouped run after retrying with zero rows. The cause was the 30 second source attempt deadline while probing many live employer pages, not a selector/parser break in a specific company scraper.

Fix from the live diagnosis: company career probing now budgets every per-company stage against the remaining source deadline and cancels/awaits fallback browser tasks on timeout or cancellation. This removed the late Python `Future/Task exception was never retrieved` cleanup warnings observed during live runs. Remaining `rebrowser-patches` frame-context messages are browser runtime noise; they did not prevent registered sources or direct career sources from returning results.

Separate direct-employer batch verification checked 400 of 410 bundled companies, found 56 total matches, and recorded 10 access issues. The access issues were Abe Health, Coding Invaders, DestinyX, Eightify, Grammarly, MetaPax, Pagoda, Telegram, Uitrial, and Wheely; causes were live network or destination reachability constraints such as LinkedIn/Telegram access, SSL handshake timeout, or page timeout, not local parser regressions.

The repository verifier was updated to match the new runtime contract: registered `company_careers` smoke accepts structured `partial/slow_pagination` or bounded `timeout/goto_timeout` statuses, its process timeout accounts for two 30 second source attempts, and company batch network-restricted `budget exhausted` fallback errors are classified as access issues when the restricted URL is LinkedIn or Telegram.


## Context and Orientation

The installable plugin lives under `plugins/job-harness`. The current search path is centered on these files:

- `plugins/job-harness/src/job_harness/types.py` defines `SearchRequest`, `FilterSupport`, `ScraperCapabilities`, `SourceStatus`, `SourceState`, and failure modes.
- `plugins/job-harness/src/job_harness/models.py` defines `SearchParams`, `JobListing`, and `SearchResults`.
- `plugins/job-harness/src/job_harness/base.py` defines `BaseScraper` and `BaseBrowserScraper`.
- `plugins/job-harness/src/job_harness/registry.py` registers scrapers and exposes metadata for `list_sources`.
- `plugins/job-harness/src/job_harness/search_engine.py` resolves sources, runs scrapers, annotates grade, filters, dedupes, truncates, and returns `SearchResults`.
- `plugins/job-harness/src/job_harness/run_journal.py` writes `raw.jsonl`, rewrites `summary.json`, and materializes `results.json`.
- `plugins/job-harness/scripts/mcp-server.py` exposes `search_start`, `search_status`, `search_results`, `search_retry`, `search_cancel`, `search_refine`, `list_sources`, and lookup tools.
- `plugins/job-harness/src/job_harness/cli.py` exposes `job-harness search`, `list-sources`, `company-search`, and `company-live-batch`.

In this plan, a "source" is one registered scraper id such as `hh_ru`, `habr_career`, `finder_work`, `company_careers`, or `career:vk`. A "source group" is a semantic label used for planning and selection, for example `aggregator`, `company_career`, or `directory`. A "raw search artifact" is the on-disk file containing the listings exactly as the search layer collected them, plus source-native parsed fields and source diagnostics. It is not ranked, deduped, grade-estimated, or truncated by total result count. A "downstream artifact" is any later export that filters, dedupes, ranks, annotates, or slices the raw search artifact for presentation.

The current registered source inventory observed on 2026-06-08 is:

- Aggregator and job-board sources: `hh_ru`, `hh_kz`, `hh_uz`, `rabota_by`, `headhunter_kg`, `habr_career`, `hirehi`, `hirify`, `staff_am`, `geekjob`, `talento`, `finder_work`, `it_jobs_uz`, `jobturbo`, and `getmatch`.
- Company career sources that probe live employer pages: `company_careers`, `career:ibs`, and `career:vk`.
- Directory source that returns employer entrypoints rather than confirmed vacancies: `company_directory`.

The current capability matrix tracks `remote_only`, `country`, `experience`, `location`, `has_salary`, and `query_match` as several support levels. That is too broad for the new boundary because it mixes source-native server filters, structured fields returned by a source, and heuristic inference. The target search layer removes that ambiguity: catalog criteria are server-filter support only, raw native fields are reported in the raw dataset, and every heuristic belongs to downstream processing. For example, a URL parameter such as `schedule=remote` is a search-layer remote criterion; a JSON field named `workType=REMOTE` is raw evidence; deciding from title or description text that the job is remote is downstream inference.

Current source selection works like this. If `SearchRequest.sources` is absent or `all`, `_resolve_sources` walks the entire registered scraper list. It skips sources whose static `countries` do not match the request country. It skips browser sources only when `profile="fast"`. It skips unsupported flags only when `strict_flags=True`, except that `experience` no longer skips sources because the grade engine handles it downstream. This is close to fail-open search, but the raw artifact is still polluted by downstream grade annotation and by the global `max_results` depth cap.


## Current-To-Target File Map

The current model is: MCP and CLI build `SearchRequest` in `plugins/job-harness/src/job_harness/types.py`, registry metadata in `plugins/job-harness/src/job_harness/registry.py` describes scrapers with display/transport/capability fields, scrapers return `JobListing` from `plugins/job-harness/src/job_harness/models.py`, `plugins/job-harness/src/job_harness/search_engine.py` both scrapes and performs downstream annotation/filtering, and `plugins/job-harness/src/job_harness/run_journal.py` writes one event journal plus a filtered `results.json`. Runtime budgets are public request fields today, while lower-level defaults already live in `BrowserPool`, `HttpRunner`, and `http_common.py`.

The target model is: MCP and CLI send only search criteria and source selectors; `list_sources` exposes a compact `SourceDescriptor`; scrapers return `RawListing`; the search layer writes `raw_search.jsonl` and typed source summaries before any downstream analysis; `ResultPipeline` reads raw rows and produces filtered `results.json`; runtime behavior is service/component policy, not agent-provided search input.

The files that should be touched are:

- Catalog, request, and public contracts: `plugins/job-harness/src/job_harness/types.py`, optionally a new `plugins/job-harness/src/job_harness/source_catalog.py`, `plugins/job-harness/src/job_harness/base.py`, `plugins/job-harness/src/job_harness/registry.py`, `plugins/job-harness/scripts/mcp-server.py`, and `plugins/job-harness/src/job_harness/cli.py`.
- Scraper metadata and `RawListing` output: `plugins/job-harness/src/job_harness/scrapers/hh_ru.py`, `plugins/job-harness/src/job_harness/scrapers/habr_career.py`, `plugins/job-harness/src/job_harness/scrapers/cis_sources.py`, `plugins/job-harness/src/job_harness/scrapers/company_careers.py`, `plugins/job-harness/src/job_harness/scrapers/company_directory.py`, `plugins/job-harness/src/job_harness/scrapers/career/ibs.py`, and `plugins/job-harness/src/job_harness/scrapers/career/vk.py`.
- Search/raw/downstream boundary: `plugins/job-harness/src/job_harness/search_engine.py`, `plugins/job-harness/src/job_harness/run_journal.py`, `plugins/job-harness/src/job_harness/models.py`, a new `plugins/job-harness/src/job_harness/result_pipeline.py`, plus `plugins/job-harness/src/job_harness/filters.py` and `plugins/job-harness/src/job_harness/dedupe_filter.py` only as needed to make the downstream pipeline consume raw-derived presentation objects.
- Engine-level runtime policy: a new `plugins/job-harness/src/job_harness/source_runtime.py`, `plugins/job-harness/src/job_harness/source_retry.py`, `plugins/job-harness/src/job_harness/search_engine.py`, and `plugins/job-harness/src/job_harness/scrapers/company_careers.py`. Do not edit `plugins/job-harness/src/job_harness/browser_pool.py`, `plugins/job-harness/src/job_harness/http_runner.py`, or `plugins/job-harness/src/job_harness/scrapers/http_common.py` just to move existing defaults.
- Tests and guidance: `plugins/job-harness/tests/test_capability_matrix.py`, `plugins/job-harness/tests/test_countries_and_registry.py`, `plugins/job-harness/tests/test_search_engine.py`, `plugins/job-harness/tests/test_run_journal.py`, `plugins/job-harness/tests/test_filters_and_formatters.py`, `plugins/job-harness/tests/test_mcp_async_surface.py`, `plugins/job-harness/tests/test_mcp_server.py`, `plugins/job-harness/tests/test_cli.py`, `plugins/job-harness/tests/test_source_retry.py`, `plugins/job-harness/tests/test_company_careers_scraper.py`, `README.md`, `AGENTS.md`, `plugins/job-harness/agents/job-searcher.md`, and `plugins/job-harness/commands/job-search.md`.


## Target Architecture

The target structure is a layered pipeline:

    MCP tools and CLI
      -> request validation and source selection
      -> SearchLayer: dispatch all selected scrapers, receive RawListing rows, write raw artifact
      -> ResultPipeline: optional grade assessment, downstream filters, dedupe, ranking, presentation limits
      -> exported artifacts: raw_search.jsonl, raw_search_summary.json, results.json, inline previews

The search layer must have these responsibilities and no others. The data shapes are normative in [Typed Contracts](#typed-contracts), the public MCP serialization is normative in [MCP Tool Contract](#mcp-tool-contract), and source deadlines, retries, partial states, and limit handling are normative in [Source Runtime Policy](#source-runtime-policy).

1. Validate request shape against `SearchRequest`: query is present, exact source ids exist, source groups exist, and `max_results`, `salary_from`, and `freshness_days` are positive when present.
2. Resolve selected sources by exact source ids and optional source groups, preserving registry order and recording every skipped selected source with a reason.
3. Resolve each selected source's `SourceDescriptor`, including `group`, `countries`, `server_criteria`, and `source_limit`.
4. Build each source's criteria summary from `SearchCriteriaRequest`: `requested_criteria`, `supported_server_criteria`, `server_criteria_used`, and `unsupported_requested_criteria`.
5. Pass only server-side source-native request parameters to scrapers when the source declares honest support for that `SearchCriterion`.
6. Run every selected eligible source, including every known company target inside `company_careers`, under `Source Runtime Policy` until the source reaches a source-native end marker, reaches `source_limit`, times out, is blocked, is rate-limited, fails, exhausts retry policy, or the whole run is cancelled.
7. Receive `RawListing` rows directly from scrapers and treat them as source facts, not as downstream `JobListing` projections.
8. Wrap each collected `RawListing` in a `RawSearchRecord` and append it to `raw_search.jsonl` before any downstream filtering, grading, ranking, dedupe, or presentation slicing.
9. Write one `RawSourceSummary`-compatible source row for every selected source, including successful, zero-result, partial, timeout, rate-limited, blocked, error, cancelled, skipped, and unsupported-criterion outcomes.
10. Return artifact paths and diagnostics, including the raw artifact path and downstream results path, so the high-level agent can decide what to inspect, retry, or refine.

The search layer must not:

1. Estimate grade from title, description, requirements, skills, or raw text.
2. Infer remote, country, location, salary, freshness, or experience from text or loosely structured page content.
3. Apply client-side filtering based on posted dates, raw locations, raw remote labels, raw grade labels, raw salary text, keywords, company names, or missing fields.
4. Drop listings because they are not the "best match" for the user.
5. Deduplicate across sources.
6. Rank or reorder raw search records for presentation.
7. Globally truncate the raw artifact to `max_results`.
8. Convert `JobListing.to_dict()` into raw records or infer missing raw fields from downstream presentation objects.
9. Mutate a scraper-produced `RawListing` except to wrap it in a `RawSearchRecord` with run metadata.
10. Hide source errors or silently treat an unsupported constraint as applied.

The downstream result pipeline should keep the current useful behavior: deterministic grade assessment in `experience_engine.py`, filters in `filters.py` and `dedupe_filter.py`, dedupe, result ordering, `max_results` presentation slicing, `search_refine`, Markdown/JSON/CSV formatting, and agent-facing previews. The implementation should make that pipeline read from the raw artifact instead of changing what the search layer writes.


## Typed Contracts

This section is the normative contract. Prose elsewhere must not add extra search-layer fields or criteria.

    SourceId = NewType("SourceId", str)
    CountryCode = NewType("CountryCode", str)
    PositiveInt = NewType("PositiveInt", int)
    IsoDateOrDateTime = NewType("IsoDateOrDateTime", str)
    JsonScalar = str | int | float | bool | None
    JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

    class SourceGroup(StrEnum):
        AGGREGATOR = "aggregator"
        COMPANY_CAREER = "company_career"
        DIRECTORY = "directory"
        OTHER = "other"

    class SearchCriterion(StrEnum):
        QUERY = "query"
        COUNTRY = "country"
        REMOTE_ONLY = "remote_only"
        EXPERIENCE_LEVELS = "experience_levels"
        LOCATION = "location"
        SALARY_FROM = "salary_from"
        FRESHNESS = "freshness"

    @dataclass(frozen=True)
    class SearchCriteriaRequest:
        query: str
        country: CountryCode | None = None
        remote_only: bool = False
        experience_levels: tuple[str, ...] = ()
        location: str | None = None
        salary_from: PositiveInt | None = None
        freshness_days: PositiveInt | None = None

    @dataclass(frozen=True)
    class SearchRequest(SearchCriteriaRequest):
        sources: Literal["all"] | tuple[SourceId, ...] = "all"
        source_groups: tuple[SourceGroup, ...] = ()
        max_results: PositiveInt = PositiveInt(20)

    @dataclass(frozen=True)
    class SourceDescriptor:
        group: SourceGroup
        countries: tuple[CountryCode, ...]
        server_criteria: frozenset[SearchCriterion]
        source_limit: PositiveInt

`SearchCriteriaRequest` is the closed server-criteria input. `SearchRequest` adds source selection and presentation size. `sources` selects exact source ids from catalog keys. `source_groups` selects catalog groups. If both are present, selection is the union in catalog order. `max_results` is only a downstream presentation limit and never caps raw scraping. Runtime behavior such as total run timeout, source attempt timeout, source-level retry count, and source-level retry backoff is not part of `SearchRequest`; it is service configuration described in [Source Runtime Policy](#source-runtime-policy). Lower-level browser-pool, HTTP-runner, and HTTP-fetch settings remain component defaults unless a later plan creates a separate deployment configuration surface for them.

`list_sources()` and `job-harness list-sources --json` return `dict[SourceId, SourceDescriptor]` in registry order. Example:

    {
      "hh_ru": {
        "group": "aggregator",
        "countries": ["RU"],
        "server_criteria": ["query", "country", "remote_only", "experience_levels", "salary_from"],
        "source_limit": 100
      }
    }

This is intentionally small. `display_name` is not a selector and is not needed for agent planning. `transport` is internal runner metadata. `native_fields` is not a catalog field because raw listing types below define the dataset contract. Separate `default` and `hard` limits are collapsed into one `source_limit`; it is the source-local hard stop for normal operation.

Search-layer criterion support is binary. If a criterion is in `server_criteria`, the scraper may send it to the source as a native URL/API/form parameter before results are returned. If it is absent, it is unsupported for this source. There is no `client`, `best_effort`, regex, text-match, or heuristic support mode. Returned fields such as `posted_date`, `salary`, `location`, or remote labels are raw evidence only; local filtering and inference belong to `ResultPipeline`.

`salary_from` is a lower-bound amount. It is supported only when a source has a native lower-bound or target-salary search parameter, such as HH-family `salary`, Habr Career `salary`, Finder/Hirify `salary_from`, or IT-Jobs.uz `salaryMin`, and the implementation adds a source-specific fixture proving that parameter is sent and changes the server request semantics. Currency is not a separate v1 criterion. If a source cannot map the amount to an unambiguous native/default search currency, it must leave `salary_from` unsupported until a later plan adds `salary_currency`.

Everything outside `SearchCriterion` is not a search-layer criterion. Salary presence, salary upper bound, salary currency conversion, include/exclude keywords, excluded companies, grade inference, country inference, remote inference, location inference, ranking, and dedupe are downstream concerns unless this plan is explicitly revised.

The raw dataset has two files. `RawListing` is the target normalized return type of every scraper. `raw_search.jsonl` contains one `RawSearchRecord` per line, where the search layer wraps a scraper-produced `RawListing` with run metadata. This is not a second listing format and not a `JobListing.to_dict()` projection. Current scrapers may populate only a subset of these fields during migration; missing source facts serialize as `None`, empty tuples, or raw-only evidence as defined below. `description`, `requirements`, and `skills` are optional source-native facts, not required fields for every source.

    @dataclass(frozen=True)
    class RawListing:
        title: str
        url: str
        company: str | None
        country: CountryCode | None
        salary: str | None
        experience: str | None
        remote: bool | None
        location: str | None
        description: str | None      # source-provided description/detail/profile text
        requirements: str | None     # source-labeled requirements section only
        skills: tuple[str, ...]      # source-provided tags, skills, or company stack labels
        posted_date: IsoDateOrDateTime | None
        source: SourceId
        raw: dict[str, JsonValue]

    @dataclass(frozen=True)
    class RawSearchRecord:
        schema_version: Literal[1]
        type: Literal["raw_listing"]
        run_id: str
        source: SourceId
        collected_at: IsoDateOrDateTime
        listing: RawListing

Unknown values are `None`, empty tuples, or absent inside `raw`; `remote=False` means the source explicitly said non-remote. `posted_date` is ISO 8601 only when the source exposes a machine-readable date. Human-only date text remains in `raw`. `description` is populated only from text the source actually returns on a listing card, source API payload, employer profile, or detail page visited under the source runtime policy. `requirements` is populated only when the source explicitly exposes a requirements field or clearly labeled requirements section; otherwise source text stays in `description` or `raw`. `skills` is populated only from source-provided tags, skills objects, chips, or company stack labels; the search layer must not extract skills heuristically from prose. Downstream assessment fields such as `experience_levels`, `experience_origin`, `experience_confidence`, and `experience_evidence` are forbidden in `RawListing`.

Data model options considered:

1. Keep current `JobListing` as scraper output and add a raw serializer. Rejected because it keeps downstream fields in the raw path and relies on a lossy adapter to recover source facts.
2. Make scrapers return `RawSearchRecord`. Rejected because scrapers should not know run ids, collection timestamps, source limits, or artifact storage concerns.
3. Make scrapers return `RawListing`, then let the search layer wrap it in `RawSearchRecord`. Selected because the scraper output can be written to raw storage without reinterpretation, while storage metadata stays in the search layer.

Implementation must change the scraper-facing contract from `JobListing` to `RawListing` for search-layer output and migrate existing scrapers to that contract. Do not add a permanent compatibility adapter from `JobListing` to `RawListing`; tests must fail if the raw writer infers values from downstream `JobListing.to_dict()` fields. `JobListing` may remain as the downstream presentation/result-pipeline model, but not as the canonical raw scraper output.

`SourceState` remains the closed source lifecycle enum from `types.py`: `ok`, `partial`, `timeout`, `error`, `rate_limited`, `blocked`, `cancelled`, `skipped`, and `skipped_unsupported_flag`. `FailureMode` remains the closed reason taxonomy from `types.py`, including timeout reasons such as `goto_timeout`, `http_timeout`, `pool_acquire_timeout`, and `total_timeout`, rate-limit reasons such as `http_429` and `http_503_retry_after`, block reasons such as `anti_bot_page`, and skip reasons such as `not_in_country`, `not_in_profile`, and `unsupported_flag`. `raw_search_summary.json` or the `summary.json` raw section contains one typed source summary per selected source:

    @dataclass(frozen=True)
    class RawSourceSummary:
        source: SourceId
        group: SourceGroup
        state: SourceState
        failure_mode: FailureMode | None
        source_limit: PositiveInt
        deadline_ms: PositiveInt
        elapsed_ms: int | None
        requested_criteria: SearchCriteriaRequest
        supported_server_criteria: tuple[SearchCriterion, ...]
        server_criteria_used: tuple[SearchCriterion, ...]
        unsupported_requested_criteria: tuple[SearchCriterion, ...]
        pages_visited: int | None
        listings_written: int
        attempts: int
        retries: int
        limit_reached: bool
        error: str | None

There is no `applied` boolean. The source summary uses two explicit lists instead: `server_criteria_used` means requested criteria that were actually sent to the source, and `unsupported_requested_criteria` means requested criteria that this source could not apply. Sources that return zero listings, fail, time out, are blocked, or stop at `source_limit` still get a summary row.


## MCP Tool Contract

The MCP search tools are the public agent-facing serialization of `Typed Contracts`. MCP schemas, CLI parsing, journal request serialization, and contract tests must agree with these shapes.

`list_sources()` returns `dict[SourceId, SourceDescriptor]` exactly as defined above: object keys are exact source ids, and each value has `group`, `countries`, `server_criteria`, and `source_limit`.

`search_start(...)` accepts the fields of `SearchRequest`:

    search_start(
        query: str,
        sources: Literal["all"] | list[SourceId] | str = "all",
        source_groups: list[SourceGroup] | None = None,
        country: CountryCode | None = None,
        remote_only: bool = False,
        experience_levels: list[str] | None = None,
        location: str | None = None,
        salary_from: PositiveInt | None = None,
        freshness_days: PositiveInt | None = None,
        max_results: PositiveInt = PositiveInt(20),
    ) -> {
        "run_id": str,
        "run_dir": str,
        "raw_search_path": str,
        "results_path": str,
    }

For MCP compatibility, `sources` may remain a comma-separated string during migration, but the accepted values are still exact source ids from `list_sources` or `"all"`. `max_results` is the downstream presentation limit only. `salary_from` and `freshness_days` are requested criteria; per-source support is reported later in `RawSourceSummary.server_criteria_used` and `RawSourceSummary.unsupported_requested_criteria`. MCP does not accept timeout, retry, browser pool, HTTP fetch, or backoff parameters in v1; those are service-level settings.

`search_status(run_id)` returns run lifecycle state, artifact paths, and one `RawSourceSummary`-compatible source row for every selected source. `search_results(run_id, format="file" | "inline")` returns the downstream filtered export or preview, not raw search rows. If a raw export tool is added, it must return `raw_search.jsonl` by path and must not inline unbounded raw content.


## Source Runtime Policy

This section is the normative scraper runtime contract. It explains when a source stops, when it retries, and how the stop reason appears in `RawSourceSummary`. It applies to HTTP scrapers, browser scrapers, company career scrapers, and fake test scrapers.

The implementation must introduce a small `SourceRuntimeConfig` in `plugins/job-harness/src/job_harness/source_runtime.py`. `SearchEngine` receives this config at construction time, defaulting to `SourceRuntimeConfig()`. MCP tools and CLI commands do not expose this config in v1. Tests may instantiate `SearchEngine(runtime_config=...)` directly to exercise edge cases. If local deployment overrides are later needed, add a separate service configuration mechanism and keep it out of the agent-facing MCP schema unless a future plan explicitly changes that.

The v1 config contains only engine-level behavior that is missing today or currently exposed through public request fields:

    @dataclass(frozen=True)
    class SourceRuntimeConfig:
        total_run_timeout_ms: PositiveInt = PositiveInt(90_000)
        source_attempt_timeout_ms: PositiveInt = PositiveInt(30_000)
        company_probe_timeout_ms: PositiveInt = PositiveInt(8_000)
        source_max_attempts: PositiveInt = PositiveInt(2)
        source_retry_initial_backoff_ms: PositiveInt = PositiveInt(500)
        source_retry_backoff_multiplier: float = 2.0
        source_retry_max_backoff_ms: PositiveInt = PositiveInt(2_000)

The current runtime settings that already exist must be kept, not moved into this config in v1. `BrowserPool.__init__` already has `max_contexts=2`, `page_timeout_ms=30_000`, deadline-aware `acquire_timeout_ms=None`, and `recycle_after_consecutive_hangs=2`; leave those as `BrowserPool` constructor defaults. BR-007 superseded the older fixed `acquire_timeout_ms=5_000` default; callers may still pass an explicit acquire timeout for infrastructure guard tests. `HttpRunner.__init__` already has `max_workers=8`, `cooldown_threshold=4`, and `cooldown_window_s=10.0`; leave those as `HttpRunner` constructor defaults. `scrapers/http_common.py` already has `DEFAULT_RETRIES=2`, a 1,000 to 10,000 ms per-URL-attempt timeout range, deterministic 500 ms / 1,000 ms network backoff, HTTP 429/503 `Retry-After` handling, HTTP 5xx fetch-level retries, terminal HTTP 4xx, and terminal parse errors; preserve that behavior and test it as HTTP helper behavior. The new work is to connect these existing behaviors to one source attempt deadline and to add source-level retry around the whole source attempt.

Each run has a wall-clock `total_run_timeout_ms`. Today this value is public as `SearchRequest.total_timeout_ms`; the target moves it into `SourceRuntimeConfig`. The engine must record a summary row for every selected source even when the total run deadline fires. Sources that never produced a terminal source outcome before the total deadline are recorded with `state=cancelled` and `failure_mode=total_timeout`. The total deadline cancels outstanding source tasks; it does not silently drop selected sources from the summary.

Each source attempt receives `min(source_attempt_timeout_ms, remaining_total_run_timeout_ms)` as its wall-clock budget. Today this value is public as `SearchRequest.source_timeout_ms`; the target moves it into `SourceRuntimeConfig`. This budget covers all pages, cursors, detail fetches that are part of source-native collection, HTTP fetch retries inside the scraper, browser page acquisition, browser page work, and per-company probes inside `company_careers`. When the budget is exhausted before a source-native end marker, the result is `state=timeout` if no listings were collected and `state=partial` if at least one listing was collected and can be trusted.

Each source also has `source_limit` from `SourceDescriptor`. `source_limit` is a successful local hard stop, not an error. When a source reaches `source_limit`, the summary records `state=ok`, `limit_reached=true`, and `listings_written == source_limit`. When the source reaches its own native end marker before `source_limit`, the summary records `state=ok` and `limit_reached=false`. `source_limit` is independent from `max_results`; `max_results` never changes how much raw evidence a source may collect.

HTTP scrapers use the remaining source attempt deadline for each `fetch_text` or `fetch_json` call. HTTP fetch-level retries are not new work: keep the current `http_common.py` behavior. `DEFAULT_RETRIES=2` means up to three URL attempts inside one source attempt. Network and socket failures use the current deterministic linear backoff. HTTP 429 and HTTP 503 with `Retry-After` may sleep and retry only if the sleep fits inside the remaining source attempt budget; otherwise the source is `rate_limited` with `failure_mode=http_429` or `failure_mode=http_503_retry_after`. HTTP 5xx without `Retry-After` uses the fetch-level retry budget; if those URL attempts are exhausted, the source attempt records `state=error` and `failure_mode=http_5xx`. HTTP 4xx other than retryable rate-limit responses is terminal. Deterministic parse failures are terminal. HTTP fetch-level retries do not increment `RawSourceSummary.retries`; they are internal to one source attempt.

Browser scrapers use the existing `BrowserPool` defaults. Acquire and page work are capped by the remaining source attempt budget passed to `BrowserPool.run_with_page(...)`; do not add browser-pool fields to `SourceRuntimeConfig` in v1. Failing to acquire a browser context is `state=timeout` with `failure_mode=pool_acquire_timeout`. A page-level deadline or navigation timeout is `state=timeout` with `failure_mode=goto_timeout` when no listings were collected, or `state=partial` with `failure_mode=slow_pagination` when trusted listings were collected before the deadline. Anti-bot, captcha, and login probes are terminal `blocked` states, not retryable browser timeouts.

Company career scans use the same source attempt deadline and source limit as every other source. A single company probe receives at most `company_probe_timeout_ms`, capped by the remaining source attempt deadline. This standardizes the current mixed behavior: `SearchRequest.resolve_timeout_ms_per_company` and company-career helpers use 8,000 ms in some paths, while `CompanyCareersScraper` has `_PER_COMPANY_TIMEOUT_MS=5_000`; the implementation should make `company_careers` use `SourceRuntimeConfig.company_probe_timeout_ms` instead of its private constant. If the source deadline stops the company set before all selected companies are probed, the source is `partial` when it already wrote trusted raw listings or `timeout` when it wrote none.

Automatic source-level retry is the main new runtime behavior. It is separate from HTTP fetch-level retry. `source_max_attempts=2` means one retry after the original source attempt. Source-level retry backoff is deterministic exponential backoff with no jitter: for retry number `n` starting at 1, sleep `min(source_retry_initial_backoff_ms * source_retry_backoff_multiplier ** (n - 1), source_retry_max_backoff_ms, remaining_total_run_timeout_ms - 100)`. If the calculated sleep is not positive, do not retry and record the previous terminal outcome.

A source-level retry is allowed only when all of these are true: the source attempt wrote zero trusted listings, the attempt ended with one of `network_error`, `http_timeout`, `goto_timeout`, `pool_acquire_timeout`, `http_429`, or `http_503_retry_after`, `attempts < source_max_attempts`, and remaining total run budget can fund both the backoff sleep and another source attempt. A source attempt that already produced trusted raw listings is not automatically retried in the same run, because that would risk duplicate raw evidence; it is recorded as `partial` and can be retried later through the manual `search_retry` tool. Unsupported criteria, country/profile skips, HTTP 4xx, deterministic parse errors, HTTP 5xx after fetch-level retries, global network outage short-circuit, anti-bot blocks, captchas, login redirects, user cancellations, and total run timeout are not retried automatically.

`RawSourceSummary.attempts` counts source attempts, including the first. `RawSourceSummary.retries` is `attempts - 1` and counts only source-level retries, not HTTP fetch-level retries. `RawSourceSummary.deadline_ms` records the source attempt deadline used for the last attempt. `RawSourceSummary.elapsed_ms` records elapsed wall-clock time across all source attempts, including source-level backoff, when known. `RawSourceSummary.failure_mode` is `None` only for `state=ok`; every other state carries a closed `FailureMode` value from `types.py`. `RawSourceSummary.error` is a concise human-readable diagnostic, not a stack trace.


## Plan of Work

Milestone 1 establishes source descriptors. Add `SourceGroup`, `SearchCriterion`, and `SourceDescriptor` in `types.py` or a focused `source_catalog.py`. Extend `BaseScraper` with `group: ClassVar[SourceGroup]`, `server_criteria: ClassVar[frozenset[SearchCriterion]]`, and `source_limit: ClassVar[int]`. Update every registered scraper to declare those fields. Aggregator/job-board sources get `aggregator`; `company_careers`, `career:ibs`, and `career:vk` get `company_career`; `company_directory` gets `directory`. Update `registry.get_scraper_metadata()` so `list_sources()` returns exactly `dict[SourceId, SourceDescriptor]`: `group`, `countries`, `server_criteria`, and `source_limit`.

Acceptance for Milestone 1 is a contract test in `tests/test_source_catalog.py` or `tests/test_capability_matrix.py` proving every registered source has valid `group`, positive `source_limit`, and `server_criteria` values drawn only from `SearchCriterion`. The same test must prove `list_sources` returns no selector aliases: source ids are only dictionary keys, not `display_name` values.

Milestone 2 adds the raw dataset contract. Add `RawListing`, `RawSearchRecord`, and `RawSourceSummary` shapes matching `Typed Contracts`. Change `BaseScraper.search`, `BaseBrowserScraper.search_with_page`, and detail fetch methods in the search layer to return `RawListing` objects. Add a writer method such as `RunJournalWriter.write_raw_listing(record: RawSearchRecord)` or a separate artifact writer for `raw_search.jsonl`; the writer should only wrap and serialize scraper-produced `RawListing` rows, not reinterpret `JobListing`. Keep the existing event journal if it remains useful for polling. Acceptance is a unit test where a fake unsupported-experience source returns `RawListing(title="Senior QA", ...)`; `raw_search.jsonl` has no downstream experience assessment fields, while `results.json` may include them after `ResultPipeline`.

Milestone 3 separates search depth from presentation. Keep public `max_results` as the downstream presentation limit. Each selected source receives its own `source_limit` from the catalog; raw scraping never uses `request.max_results` as depth. Each scraper with reliable pagination must traverse pages, cursors, offsets, or next links until the source ends, `source_limit` is reached, the source deadline is reached, or an unrecoverable source error occurs. `company_careers` must keep scanning the selected company set until the same stop conditions; deadline-limited scans report `partial`.

Acceptance for Milestone 3 is a test where two fake sources each have `source_limit=3` and emit three listings. With `max_results=1`, `raw_search.jsonl` contains six rows while `results.json` or inline results returns one.

Milestone 4 makes source selection and MCP contracts explicit. Extend MCP `search_start` and CLI `job-harness search` to accept exact source ids as today and add optional `source_groups`, for example `source_groups=["aggregator", "company_career"]` in MCP and `--source-groups aggregator,company_career` in CLI. Add MCP and CLI parameters for every `SearchCriteriaRequest` field that is not already present, including `salary_from` and `freshness_days`. Remove the current public timeout fields from the agent-facing request surface during the migration: do not expose `source_timeout_ms`, `total_timeout_ms`, retry counts, retry backoff, browser pool size, HTTP fetch retries, or cooldowns through MCP or CLI. These are service/component runtime settings, not search criteria. The default remains `sources=all`, which means all groups unless a country or explicit source group narrows eligibility. If both `sources` and `source_groups` are present, the selected set is the union of exact source ids and group members, with duplicates removed in registry order. `list_sources` must return exact ids prominently so the agent can say `sources="hh_ru,career:vk"` without guessing. Acceptance is an MCP/registry test proving `source_groups=["company_career"]` selects `company_careers`, `career:ibs`, and `career:vk`, while `sources="hh_ru"` selects only `hh_ru`; MCP schema tests must prove `search_start` accepts the `SearchRequest` fields from `MCP Tool Contract` and rejects runtime knobs such as `source_timeout_ms`, `total_timeout_ms`, `source_max_attempts`, and `http_fetch_retries`; CLI tests must prove those options are not defined.

Milestone 5 adds honest freshness and salary lower-bound support. Add `freshness_days: int | None = None` and `salary_from: int | None = None` to `SearchRequest`, scraper params, MCP `search_start`, CLI `job-harness search`, and journal request serialization. A source supports freshness only by including `SearchCriterion.FRESHNESS` in `server_criteria`; merely returning `posted_date` is raw evidence and does not allow search-layer filtering. A source supports salary lower-bound search only by including `SearchCriterion.SALARY_FROM` in `server_criteria`; merely returning `salary`, `salary_from`, `salaryMin`, or salary text is raw evidence and does not allow local filtering. Acceptance is a fake server-supported source test proving freshness is sent as a source-native parameter, plus a fake unsupported source test proving recent, old, missing, and unparseable dates are all collected and the summary lists `freshness` in `unsupported_requested_criteria`. Salary acceptance is a source-specific test for every source that declares support, proving the scraper sends the native salary parameter and records `salary_from` in `server_criteria_used`; unsupported sources must keep all raw listings and list `salary_from` in `unsupported_requested_criteria`.

Milestone 6 implements `Source Runtime Policy`. Add `plugins/job-harness/src/job_harness/source_runtime.py` with the exact minimal `SourceRuntimeConfig` fields and defaults from [Source Runtime Policy](#source-runtime-policy). Keep `source_retry.py` focused on classifying manual retry candidates or move shared retry classification into `source_runtime.py`; either way, the retryable and non-retryable source-level failure modes must match the runtime policy exactly. Wire `SourceRuntimeConfig` into `SearchEngine` and the scraper factory path so engine-owned source attempts use `total_run_timeout_ms`, `source_attempt_timeout_ms`, `source_max_attempts`, and source-level exponential backoff. Make `company_careers` use `company_probe_timeout_ms` instead of its private per-company timeout constant. Do not move `BrowserPool`, `HttpRunner`, or `http_common.py` low-level defaults into `SourceRuntimeConfig`; keep their existing constructor/helper defaults and preserve their current tests, adding only focused tests where needed to prove they interact correctly with source attempt deadlines. Do not expose this config through MCP or CLI. Retry only the zero-listing transient failure modes named in the runtime policy, and do not retry sources that already produced trusted raw listings in the same run. Record `attempts`, `retries`, `deadline_ms`, `elapsed_ms`, `failure_mode`, and `limit_reached` in `RawSourceSummary`. Keep MCP `search_retry` as a manual post-run retry surface. Acceptance is an engine test where a fake source times out once without listings and succeeds on the second attempt, and the final source summary is `state=ok`, `attempts=2`, and `retries=1`; a fake source that writes one listing and then times out must be `partial` with `attempts=1`, not automatically retried. A separate config injection test must set tiny runtime budgets through `SearchEngine(runtime_config=...)` and prove the service config changes behavior without adding MCP parameters.

Milestone 7 moves downstream analysis out of raw search writes. Refactor `SearchEngine.execute` so it writes `RawListing` rows first. Then call a `ResultPipeline` function that reads raw `RawListing` rows, constructs downstream presentation objects as needed, applies `annotate_listing_experience`, builds the filter plan, filters, dedupes, ranks, slices to `max_results`, and writes the downstream `results.json`. This can be incremental: keep `SearchEngine` as the public facade, but move post-processing helpers into `plugins/job-harness/src/job_harness/result_pipeline.py`. Update `search_refine` to read raw listings and apply the same downstream pipeline. Acceptance is that raw listings remain unannotated in `raw_search.jsonl`, while `search_results(format="inline")` still returns annotated, filterable presentation listings.

Milestone 8 updates documentation and agent guidance. Update `README.md`, `AGENTS.md`, `plugins/job-harness/agents/job-searcher.md`, and `plugins/job-harness/commands/job-search.md` to explain that the search layer writes a raw exhaustive artifact and that `results.json` is a downstream filtered export. Document how an agent chooses sources: call `list_sources`, inspect exact ids and groups, then call `search_start` with `sources` and/or `source_groups`. Document that `max_results` is not the raw scraping limit. Document server-only freshness and salary lower-bound support plus unsupported-source behavior. Acceptance is that a novice can run `list_sources`, see exact ids and groups, start a run with a named source, and understand where the raw and downstream artifacts are.


## Concrete Steps

Work from the repository root:

    cd /Users/user/Documents/repos/qa-job-harness

Before implementation, confirm the branch:

    git branch --show-current

Expected output:

    codex/search-layer-architecture

Implement Milestone 1 by editing:

    plugins/job-harness/src/job_harness/types.py
    plugins/job-harness/src/job_harness/base.py
    plugins/job-harness/src/job_harness/registry.py
    plugins/job-harness/src/job_harness/scrapers/hh_ru.py
    plugins/job-harness/src/job_harness/scrapers/habr_career.py
    plugins/job-harness/src/job_harness/scrapers/cis_sources.py
    plugins/job-harness/src/job_harness/scrapers/company_careers.py
    plugins/job-harness/src/job_harness/scrapers/company_directory.py
    plugins/job-harness/src/job_harness/scrapers/career/ibs.py
    plugins/job-harness/src/job_harness/scrapers/career/vk.py
    plugins/job-harness/tests/test_capability_matrix.py
    plugins/job-harness/tests/test_countries_and_registry.py

Run focused metadata tests:

    uv --directory plugins/job-harness run python -m unittest \
      tests.test_capability_matrix \
      tests.test_countries_and_registry

Implement Milestones 2, 3, 5, and 7 by editing:

    plugins/job-harness/src/job_harness/types.py
    plugins/job-harness/src/job_harness/models.py
    plugins/job-harness/src/job_harness/search_engine.py
    plugins/job-harness/src/job_harness/run_journal.py
    plugins/job-harness/src/job_harness/dedupe_filter.py
    plugins/job-harness/src/job_harness/filters.py
    plugins/job-harness/src/job_harness/result_pipeline.py
    plugins/job-harness/tests/test_search_engine.py
    plugins/job-harness/tests/test_run_journal.py
    plugins/job-harness/tests/test_filters_and_formatters.py

The new `result_pipeline.py` file is optional but recommended. If the implementation keeps helpers in `search_engine.py`, it must still preserve the same boundary: raw writes happen before grade annotation and downstream filtering.

Run focused engine and journal tests:

    uv --directory plugins/job-harness run python -m unittest \
      tests.test_search_engine \
      tests.test_run_journal \
      tests.test_filters_and_formatters

Implement Milestone 4 by editing:

    plugins/job-harness/scripts/mcp-server.py
    plugins/job-harness/src/job_harness/cli.py
    plugins/job-harness/tests/test_mcp_async_surface.py
    plugins/job-harness/tests/test_cli.py

Run focused public-surface tests:

    uv --directory plugins/job-harness run python -m unittest \
      tests.test_mcp_async_surface \
      tests.test_mcp_server \
      tests.test_cli

Implement Milestone 6 by editing:

    plugins/job-harness/src/job_harness/source_retry.py
    plugins/job-harness/src/job_harness/source_runtime.py
    plugins/job-harness/src/job_harness/search_engine.py
    plugins/job-harness/src/job_harness/scrapers/company_careers.py
    plugins/job-harness/tests/test_source_retry.py
    plugins/job-harness/tests/test_search_engine.py
    plugins/job-harness/tests/test_company_careers_scraper.py

Do not edit `plugins/job-harness/src/job_harness/http_runner.py`, `plugins/job-harness/src/job_harness/browser_pool.py`, or `plugins/job-harness/src/job_harness/scrapers/http_common.py` just to move their existing defaults into `SourceRuntimeConfig`. Those components already have the low-level settings the runtime policy relies on. Touch them only if a focused test proves their current deadline plumbing conflicts with the source attempt deadline.

Run focused retry tests:

    uv --directory plugins/job-harness run python -m unittest \
      tests.test_source_retry \
      tests.test_search_engine \
      tests.test_company_careers_scraper

After all milestones, run the repository gate from the repository root:

    python scripts/verify_repo.py full

Expected result: the command exits with status 0. The full profile runs Ruff, mypy, detect-secrets, the unit suite, MCP stdio smoke, registered source smoke, and company batch smoke according to the repository verification script.


## Validation and Acceptance

The implementation is accepted only if behavior can be observed, not merely if classes exist.

For source discovery, `uv --directory plugins/job-harness run job-harness list-sources --json` must return the exact catalog shape from `Typed Contracts`: object keys are exact source ids, and each value has only `group`, `countries`, `server_criteria`, and `source_limit`. Contract tests must reject missing fields, unknown groups, unknown criteria, non-positive limits, selector aliases, and extra public fields such as `display_name`, `transport`, or `native_fields`.

For typed raw artifacts, tests must parse every `raw_search.jsonl` line and every raw summary source row against the `RawSearchRecord`, `RawListing`, and `RawSourceSummary` contracts. Tests must also prove the scraper-facing search contract returns `RawListing`, not `JobListing`. Tests must fail if a row includes downstream assessment fields, if `remote` uses `false` for unknown, if `posted_date` is non-ISO outside `raw`, if source-level criteria status is stored as a per-listing `applied` boolean, or if the raw writer depends on a `JobListing.to_dict()` adapter. Tests must not require every source to populate `description`, `requirements`, or `skills`; instead, source-specific fixture tests must prove these fields are populated only where the current scraper really extracts them and are `None` or empty elsewhere.

For exhaustive raw collection, a unit test must prove that `max_results=1` does not cap raw scraping. Two fake sources that each emit three listings must produce a raw artifact with six listings and a downstream export with one listing.

For pagination behavior, tests must prove the contract for any source that exposes a reliable next-page, page number, offset, or cursor mechanism. The acceptance statement is: no paginated source is first-page-only unless the source itself has no reachable page two or the local source limit/deadline stops traversal. HH-family and Habr Career should have explicit pagination tests because they already expose next-page mechanisms; API sources such as Getmatch should add offset/cursor tests if the implementation expands beyond a single API page.

For source runtime policy, tests must prove the concrete timeout, retry, backoff, and limit semantics from [Source Runtime Policy](#source-runtime-policy). A fake source that reaches `source_limit=3` after three rows must finish `ok` with `limit_reached=true`. A fake source that sleeps past `SourceRuntimeConfig.source_attempt_timeout_ms` before writing any listing must finish `timeout` with a timeout failure mode and `attempts=1` unless the zero-listing transient retry policy applies. A fake source that writes one listing and then exceeds the source deadline must finish `partial`, keep that listing in `raw_search.jsonl`, and must not be automatically retried. A fake source that times out once with zero listings and succeeds on the second attempt must finish `ok` with `attempts=2` and `retries=1`. A config-injection test must prove source-level retry backoff uses deterministic exponential backoff with no jitter. A `company_careers` test must prove per-company probes use `SourceRuntimeConfig.company_probe_timeout_ms`, capped by the remaining source attempt deadline. Existing HTTP helper tests must continue proving fetch-level network backoff is deterministic linear backoff and does not increment `RawSourceSummary.retries`; this is not a new config field. Existing browser-pool behavior must still map acquire timeout to `failure_mode=pool_acquire_timeout`; this is not a new config field. A total run timeout must still produce a summary row for every selected source, with unfinished sources marked `cancelled` and `failure_mode=total_timeout`. MCP and CLI tests must prove runtime knobs are not part of the public tool contract.

For search-layer purity, a test must prove raw search records are unranked, undeduped, and not grade-estimated. If a raw listing title is `Senior QA` from an unsupported-experience source, the raw artifact must not contain downstream `experience_levels=["senior"]`. A downstream result preview may contain that grade annotation because it is produced by `ResultPipeline`.

For freshness, tests must cover server and unsupported declarations. The critical case is unsupported freshness with returned dates: recent, old, missing, and unparseable dates are all raw evidence and all remain in `raw_search.jsonl`; the summary must list `freshness` in `unsupported_requested_criteria`. A server-supported freshness test must prove the freshness value is sent as a source-native parameter and appears in `server_criteria_used`, without adding any local date comparison.

For salary lower-bound search, tests must cover server and unsupported declarations. The critical case is unsupported salary with returned salary fields: high, low, missing, and unparseable salary values are all raw evidence and all remain in `raw_search.jsonl`; the summary must list `salary_from` in `unsupported_requested_criteria`. A server-supported salary test must prove the amount is sent as the source-native parameter and appears in `server_criteria_used`, without adding any local salary comparison. Initial candidate source mappings are HH-family `salary`, Habr Career `salary`, Finder and Hirify `salary_from`, and IT-Jobs.uz `salaryMin`; Getmatch remains unsupported until a native parameter is proven.

For retry behavior, deterministic parse-error, HTTP 4xx, unsupported-criterion, anti-bot, captcha, and login-redirect sources should not retry unless the policy explicitly changes and the decision log is updated. HTTP fetch-level retries inside `http_common.py` must be tested separately from source-level retries and must not increment `RawSourceSummary.retries`.

For fail-open source coverage, a test with one successful source, one timeout source, and one parser-error source must still produce raw listings from the successful source and source statuses for all three.

For agent-facing exports, `search_results(run_id, format="file")` should continue returning a path to `results.json`, while status or a new explicit return field must expose the raw search artifact path. Inline previews remain hard-capped for context safety, but that cap must not imply that the raw artifact is small.


## Idempotence and Recovery

All implementation steps are additive until old helper semantics are retired. Running tests repeatedly is safe. Existing local run data under `plugins/job-harness/data/.runs` should not be deleted during implementation. The run journal already skips torn trailing lines, so a crash during a raw search run should still leave a readable partial artifact.

If a migration partially lands and tests fail, fix forward by restoring the search-layer boundary rather than adding compatibility shortcuts that re-couple raw scraping with grade inference or ranking. If a live source smoke fails because of network access, anti-bot, or a temporary outage, inspect `source_statuses` and rerun the live profile once before changing source behavior. Do not weaken source catalog declarations or raw dataset typing to satisfy a transient network failure.

If the raw artifact and downstream export disagree, treat the raw artifact as the evidence layer. Downstream code may filter or annotate it, but it must not overwrite raw source observations.


## Artifacts and Notes

Current source ids and suggested groups:

    aggregator: hh_ru, hh_kz, hh_uz, rabota_by, headhunter_kg, habr_career, hirehi, hirify, staff_am, geekjob, talento, finder_work, it_jobs_uz, jobturbo, getmatch
    company_career: company_careers, career:ibs, career:vk
    directory: company_directory

Current important commands:

    uv --directory plugins/job-harness run job-harness list-sources --json
    uv --directory plugins/job-harness run job-harness search --query "QA engineer" --sources hh_ru --format json
    python scripts/verify_repo.py full

Current run artifact behavior:

    data/.runs/<run_id>/raw.jsonl        existing event journal, not currently a pure raw search artifact
    data/.runs/<run_id>/summary.json     current source/status snapshot
    data/.runs/<run_id>/results.json     current downstream materialized export

Target run artifact behavior:

    data/.runs/<run_id>/raw.jsonl              lifecycle event journal, if retained
    data/.runs/<run_id>/raw_search.jsonl       pure raw search listings, no downstream annotation
    data/.runs/<run_id>/raw_search_summary.json or summary.json section with source diagnostics
    data/.runs/<run_id>/results.json           downstream filtered/deduped/ranked/exported result set


## Interfaces and Dependencies

Use only the existing Python standard library and current project dependencies. Do not add a new third-party package for this architecture.

Implement the dataclasses and enums from `Typed Contracts` in `types.py` or a small `source_catalog.py` plus raw artifact module. Do not keep the old search-layer `CAPABILITY_FLAGS` contract as a second public truth. Existing `display_name`, `transport`, and runner status details can remain internal or appear in debug status, but they are not part of `list_sources` selection.

Replace scraper-facing `SearchParams.max_results` depth semantics with an explicit per-source `source_limit` value derived from `SourceDescriptor.source_limit`. Public `SearchRequest.max_results` remains presentation-only.

Replace scraper-facing listing return types with `RawListing`. `ResultPipeline` owns any conversion from `RawListing` to downstream presentation/result objects such as `JobListing`; the raw artifact writer must not perform that conversion or infer missing raw fields from downstream objects.

Implement `SourceRuntimeConfig` as the single source of truth only for engine-level source deadlines, source-level retries, source-level backoff, and company probe timeout. `SearchEngine` accepts this config at construction time, and tests may inject a smaller config. `BrowserPool`, `HttpRunner`, and `http_common.py` keep their existing low-level defaults in their own modules. MCP and CLI callers do not set runtime behavior in v1; any future public runtime override requires a separate plan change.

The public MCP surface should remain familiar:

    search_start(query, sources="all", source_groups=None, salary_from=None, freshness_days=None, max_results=20, ...)
    list_sources() -> dict[SourceId, SourceDescriptor]
    search_status(run_id) -> state, source statuses, raw artifact path, downstream summary
    search_results(run_id, format="file" | "inline", ...) -> downstream export or preview

If an explicit raw export tool is added, prefer `search_raw_results(run_id)` or `search_results(run_id, format="raw_file")`. Do not overload inline previews with unbounded raw content.

Change notes:

- 2026-06-08: Created the plan as a self-contained implementation guide.
- 2026-06-08: Generalized pagination from HH-specific behavior to a source-wide requirement.
- 2026-06-08: Simplified the contract after review: `list_sources` now exposes only `group`, `countries`, `server_criteria`, and `source_limit`; raw artifacts and source summaries are typed; `best_effort`, `client`, per-listing `applied`, and `default`/`hard` limit split are not part of the target search layer.
- 2026-06-08: Added `salary_from` as a server-only lower-bound search criterion after checking current scrapers and live/official source behavior, clarified that raw dataset fields are the target normalized contract, and made the MCP tool contract explicit.
- 2026-06-08: Clarified scraper output options and selected direct `RawListing` returns from scrapers, with `RawSearchRecord` as storage envelope only.
- 2026-06-08: Added explicit links from architecture responsibilities to `Typed Contracts`, `MCP Tool Contract`, and `Source Runtime Policy`; made timeout, retry, partial, source-limit, and runtime summary behavior concrete and testable.
- 2026-06-08: Clarified that `description`, `requirements`, and `skills` are optional source-native fields, not universally available scraper outputs; moved runtime controls out of MCP/CLI into service/component runtime policy.
- 2026-06-09: Reduced `SourceRuntimeConfig` to the exact new engine-level controls needed for v1, documented the runtime knobs that already exist in `BrowserPool`, `HttpRunner`, and `http_common.py`, and narrowed Milestone 6 to the files that actually need implementation changes.
- 2026-06-09: Added a brief current-to-target file map listing the files expected to change and the runtime files that should not be touched unless a focused test proves they conflict with the source attempt deadline.
- 2026-06-09: Completed the implementation, recorded the new feature list and live `middle qa manual` search outcome, fixed company-career deadline/access-issue handling, and verified with `python3 scripts/verify_repo.py full`.
