# Replace Experience Filtering With a Grade Engine

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository does not currently check in `PLANS.md`; this file follows the plan specification from `/Users/user/.codex/skills/public/plan-file-author/references/PLANS.md`.

## Purpose / Big Picture

The current job-harness experience filter treats `middle` as "middle or higher" and skips sources that cannot natively filter by grade. After this change, a user can request exact grade levels such as only `middle`, while job-harness still keeps broad source coverage by estimating grade for sources without native grade data. Every returned listing will say whether its grade came from native source data, a deterministic estimate, or remained unknown.

The observable result is that `search_start(experience_levels=["middle"])` no longer returns senior-only listings as strict matches, does not skip unsupported sources solely because of grade filtering, and includes explicit grade assessment fields in JSON, Markdown, CSV, and MCP inline results.

## Progress

- [x] (2026-06-07 00:00 MSK) Researched current `experience` behavior and documented it in `docs/experience-filtering.md`.
- [x] (2026-06-07 00:00 MSK) Created this ExecPlan file.
- [x] (2026-06-07 00:00 MSK) Added deterministic grade assessment types and engine in `job_harness.experience_engine`.
- [x] (2026-06-07 00:00 MSK) Replaced public `experience` API with exact `experience_levels` in MCP, CLI, `SearchRequest`, and `SearchParams`.
- [x] (2026-06-07 00:00 MSK) Wired grade assessment into search, journal materialization, refine, formatting, and summaries.
- [x] (2026-06-07 00:00 MSK) Updated HH/Habr URL behavior, `career:vk`, docs, agent guidance, and unit tests.
- [x] (2026-06-07 00:00 MSK) Ran targeted changed-area tests and full unit discovery successfully.
- [x] (2026-06-07 MSK) Ran `python3 scripts/verify_repo.py full`; it exited with status 0.
- [x] (2026-06-07 MSK) Added a small real-world offline fixture for the grade engine from live Habr, Finder.work, HireHi, Getmatch, and IT-Jobs.uz samples.
- [x] (2026-06-07 MSK) Reran `python3 scripts/verify_repo.py full` after adding the fixture; it exited with status 0.
- [x] (2026-06-08 MSK) Expanded the real-world fixture to 23 total cases, including 18 non-native `estimated` cases from HireHi, Hirify, Talento, Getmatch, and IT-Jobs.uz.
- [x] (2026-06-08 MSK) Reran `python3 scripts/verify_repo.py full` after expanding the fixture; it exited with status 0.
- [x] (2026-06-08 MSK) Tightened the real-world fixture so estimated cases do not include pre-normalized `listing.experience`; they now validate title-based engine inference directly.
- [x] (2026-06-08 MSK) Reran `python3 scripts/verify_repo.py full` after tightening estimated fixture cases; it exited with status 0.
- [x] (2026-06-08 MSK) Removed scraper-level experience estimation from best-effort/unsupported scrapers and made the engine ignore `listing.experience` for non-native sources.
- [x] (2026-06-08 MSK) Reran `python3 scripts/verify_repo.py full` after removing scraper-level grade estimation; it exited with status 0.
- [x] (2026-06-08 MSK) Audited the bundled company directory, public career-page cache, and live company-career probing paths for grade responsibility.
- [x] (2026-06-08 MSK) Added `company_careers` as a registered source over known employer career URLs so ordinary search runs include company sites and still use post-search grade analysis.
- [x] (2026-06-08 MSK) Removed artificial company-count/result-count caps from `company_careers`; if configured time is insufficient for every company target, the source reports `partial`.
- [x] (2026-06-08 MSK) Fixed the `company_careers` import path to avoid a `company_career_batch` / `scrapers.__init__` circular import.
- [x] (2026-06-08 MSK) Reran `python3 scripts/verify_repo.py full`; it exited with status 0.
- [x] (2026-06-08 MSK) Closed this ExecPlan as implemented.

## Surprises & Discoveries

- Observation: `career:vk` previously stored a specialty name in `JobListing.experience`, even though its capability declared `experience=unsupported`.
  Evidence: the implementation was changed so `plugins/job-harness/src/job_harness/scrapers/career/vk.py` no longer assigns specialty to `experience`.
- Observation: `search_refine(strict_refine=True)` currently only echoes policy text and does not change behavior.
  Evidence: `plugins/job-harness/scripts/mcp-server.py` returns `"policy": "strict" if strict_refine else "lenient"` after applying the same predicates.
- Observation: source strictness currently uses `experience` as a requested flag and skips unsupported sources before scraping.
  Evidence: `plugins/job-harness/src/job_harness/search_engine.py` includes `experience` in `_requested_flags`.
- Observation: `search_results` materialization previously read journal listings without applying the request filter plan, so exported results could diverge from engine-returned filtered results.
  Evidence: `plugins/job-harness/src/job_harness/run_journal.py` previously deduped and truncated in `materialize_listings` without rebuilding `build_filter_plan`.
- Observation: A small live sample produced useful native, estimated, and unknown cases across multiple source support modes.
  Evidence: `plugins/job-harness/tests/fixtures/experience_engine_real_world_samples.json` now freezes Habr, Finder.work, HireHi, Getmatch, and IT-Jobs.uz examples for offline regression tests.
- Observation: Several best-effort scrapers still wrote estimated grades into `JobListing.experience`, which blurred responsibility between scrapers and the grade engine.
  Evidence: HireHi, Hirify, GeekJob, Talento, JobTurbo, and Getmatch previously called `_experience_from_text(...)` before the grade engine ran.
- Observation: The 410-company directory and 409-entry public career-page cache are company-level lookup data, not per-vacancy native grade sources.
  Evidence: `CompanyDirectoryScraper` declares `experience=unsupported` and returns `JobListing` objects without `experience`; `search_company_jobs` returns `CompanyProfile` dictionaries without grade assessment.
- Observation: Live company-career probing returns `CompanyVacancyHit` records, not `JobListing` records, and uses role terms such as `lead` only for vacancy-link relevance scoring.
  Evidence: `company_career_search.py` and `company_career_batch.py` construct `CompanyVacancyHit` with title, URLs, matched text, score, countries, stack, job types, and remote signal, but no experience field.
- Observation: The ordinary `sources=all` path previously included `company_directory` and dedicated `career:*` scrapers, but not the 400-company live probing logic as a normal source.
  Evidence: the live probing code was reachable through `company-live-batch` and verification smoke, while registered-source coverage only exercised `company_directory`.
- Observation: Importing `company_career_batch` can transitively import `job_harness.scrapers.__init__` through `company_career_search -> scrapers.http_common`.
  Evidence: a full verification run failed when `company_careers` imported `company_career_batch` helpers at module import time; the source now uses lazy wrappers for those helpers.

## Decision Log

- Decision: Remove the old `experience` API instead of keeping a compatibility alias.
  Rationale: The user explicitly requested no legacy or backward compatibility path. Keeping both names would preserve ambiguous semantics and increase test surface.
  Date/Author: 2026-06-07 / Codex
- Decision: Use deterministic grade estimation first, with no LLM call.
  Rationale: The requested v1 must be reproducible, cheap, testable, and usable without credentials.
  Date/Author: 2026-06-07 / Codex
- Decision: Estimated grades participate in exact filtering.
  Rationale: The user wants broad coverage from sources that do not have native grade parsing.
  Date/Author: 2026-06-07 / Codex
- Decision: Unknown grade listings remain inline but are marked and ranked after matched listings.
  Rationale: This preserves coverage while making it clear that unknown listings are not strict grade matches.
  Date/Author: 2026-06-07 / Codex
- Decision: Keep `JobListing.experience` only for native structured/server grade input and ignore it for best-effort or unsupported sources.
  Rationale: Scrapers should extract source facts, not estimate grade. Centralizing grade estimation in `experience_engine` keeps behavior auditable and prevents duplicate heuristics.
  Date/Author: 2026-06-08 / Codex
- Decision: Treat company directory and career-page cache data as unknown-grade by default unless promoted vacancy text gives the grade engine reliable evidence.
  Rationale: Company-level metadata cannot honestly prove a specific vacancy grade. Keeping those records unknown preserves coverage without inventing certainty.
  Date/Author: 2026-06-08 / Codex
- Decision: Add `company_careers` separately from `company_directory` rather than changing directory entries into live vacancies.
  Rationale: A company profile entrypoint and a probed vacancy hit have different truth guarantees. Keeping separate source ids preserves clear reporting while allowing `sources=all` to include known employer sites.
  Date/Author: 2026-06-08 / Codex
- Decision: Do not let `max_results` or a fixed company cap limit `company_careers` scraping scope.
  Rationale: `max_results` is a result presentation limit after scraping/filtering, not a permission to skip source coverage. If runtime budgets are insufficient, the source must report `partial`.
  Date/Author: 2026-06-08 / Codex

## Outcomes & Retrospective

Status: implemented and closed.

Implemented behavior now exists in code and is covered by unit tests. `experience_levels` is the public request field, exact filtering uses the grade assessment fields, unsupported grade sources are no longer skipped solely for grade requests, and unknown-grade listings remain visible after matched listings. The grade engine also has an offline real-world sample fixture that covers native, estimated, and unknown assessments without live network access. The fixture now explicitly guards that at least 15 real-world samples exercise estimated grade detection rather than native passthrough, and that those estimated fixture listings do not carry pre-normalized `experience` values. Best-effort and unsupported scrapers no longer perform grade estimation; the grade engine owns that responsibility. The company directory, public career cache, and company-career probing paths are documented as non-native grade sources; directory entrypoints usually remain unknown. `company_careers` now promotes live employer-site hits into normal `JobListing` records and relies on the centralized grade engine for post-search grade assessment; incomplete company coverage due to timeout is reported as `partial`.

Final verification passed with `python3 scripts/verify_repo.py full`. The latest gate completed Ruff, mypy, detect-secrets, all 254 unit tests, MCP stdio smoke, live registered source smoke for 19 sources, and the company batch smoke. The `company_careers` live source smoke returned results and reported partial coverage under the standard 30s source timeout. The company batch reported 4 external access issues for career pages requiring a reachable LinkedIn/Telegram-style network path, while the command still exited successfully.

## Context and Orientation

The installable plugin lives in `plugins/job-harness`. The core data model is `plugins/job-harness/src/job_harness/models.py`, where `SearchParams` and `JobListing` are plain dataclasses. The newer immutable engine request is `SearchRequest` in `plugins/job-harness/src/job_harness/types.py`. The async orchestrator is `plugins/job-harness/src/job_harness/search_engine.py`; it resolves eligible sources, runs scrapers, applies filters, deduplicates, and writes run journals.

The public MCP tools are in `plugins/job-harness/scripts/mcp-server.py`. The CLI is in `plugins/job-harness/src/job_harness/cli.py`. Filtering lives in `plugins/job-harness/src/job_harness/filters.py` and `plugins/job-harness/src/job_harness/dedupe_filter.py`. Output rendering lives in `plugins/job-harness/src/job_harness/formatters.py`.

In this plan, "native" grade means a source supplied grade as a server-side filter or structured client field, and the parsed grade is one of `junior`, `middle`, or `senior`. "Estimated" grade means job-harness inferred grade from vacancy text using deterministic rules. "Unknown" means the engine did not have enough reliable evidence to assign a grade.

## Plan of Work

First, add a new module `plugins/job-harness/src/job_harness/experience_engine.py`. Define a small strict contract: valid levels are `junior`, `middle`, and `senior`; valid origins are `native`, `estimated`, and `unknown`; valid confidence values are `high`, `medium`, `low`, and `none`. The public function should accept a `JobListing`, a source id, and that source's declared `FilterSupport` for `experience`, then return an assessment object. Native data is trusted only when source support is `SERVER` or `CLIENT` and the parsed listing grade is valid. All other sources are estimated from title, description, requirements, skills, and raw values. Evidence strings must be short and deterministic.

Second, extend `JobListing` with new grade assessment fields: `experience_levels`, `experience_origin`, `experience_confidence`, and `experience_evidence`. Do not keep `experience` as a public filter field. `JobListing.experience` is only for native structured/server grade values from sources with `FilterSupport.SERVER` or `FilterSupport.CLIENT`; best-effort and unsupported scrapers must leave it empty.

Third, replace the public request API. In `SearchRequest` and `SearchParams`, remove `experience` and add `experience_levels: tuple[str, ...] = ()`. In MCP `search_start` and `search_refine`, expose `experience_levels` and require a comma-separated string or list input, normalized to a tuple of valid levels. In the CLI, replace `--experience` with `--experience-levels`, accepting comma-separated values. Empty lists and invalid levels must fail with clear structured errors in MCP and `ValueError` in the engine/CLI path.

Fourth, change source eligibility. `experience_levels` must not cause `_resolve_sources` to skip sources with `experience=unsupported`. Other unsupported flags continue to use the strict skip policy. After every scraper returns listings, annotate each listing with the grade engine before writing it to the journal. `flag_enforcement` for experience must show that the flag is applied by `grade_engine` and still report native support per source.

Fifth, replace `min_experience` with `experience_in`. The filter keeps listings whose assessed levels intersect the requested exact list. Unknown grade listings remain in the returned list but are ranked after matched listings and counted as `unknown_kept` in the summary. Senior-only listings must not pass an exact `["middle"]` filter. The filter summary must count native matches, estimated matches, unknown kept, and removed listings.

Sixth, update scrapers only where necessary. HH and Habr should send native server params only for a single requested level. Multi-level requests should not guess a server encoding and should rely on local filtering. `career:vk` must stop writing specialty into grade fields. Existing scrapers may keep raw grade evidence in `raw` so the grade engine can inspect it.

Seventh, add an offline evaluation harness at `plugins/job-harness/scripts/evaluate-experience-engine.py`. It should accept one or more saved `results.json` files and an optional CSV with labels. Without labels, it reports origin/confidence/level distributions and examples. With labels, it reports accuracy, macro F1, precision for `middle`, unknown rate, and false-positive examples. It must not perform network I/O.

Finally, update tests and documentation. Tests should cover the grade engine directly, exact filter behavior, MCP validation, source policy, CLI help, HH/Habr URL behavior, and formatter output. Update `docs/experience-filtering.md`, README feature text, and `plugins/job-harness/agents/job-searcher.md` to use `experience_levels` and explain exact grade semantics.

## Concrete Steps

Work from the repository root:

    cd /Users/user/Documents/repos/qa-job-harness

Create or edit the files described above using small patches. Run fast tests after the core model/filtering changes:

    uv --directory plugins/job-harness run python -m unittest \
      tests.test_filters_and_formatters \
      tests.test_search_engine \
      tests.test_mcp_async_surface \
      tests.test_hh_ru \
      tests.test_habr_career \
      tests.test_career_scrapers

Run the full verification gate before handoff:

    python3 scripts/verify_repo.py full

Expected final result: the full gate exits with status 0. The live company batch smoke may report access issues for LinkedIn/Telegram-style URLs, but the command must still return success.

## Validation and Acceptance

Acceptance is behavior-based. A unit test must prove that `experience_in(["middle"])` keeps a `middle` listing, removes a `senior` listing, and keeps an unknown listing marked as unknown. A search-engine test must prove that a source declaring `experience=unsupported` is still dispatched when `experience_levels=("middle",)` is requested. MCP tests must prove that invalid levels return a structured error and that the old `experience` parameter is absent from tool signatures.

Formatter tests must prove that JSON includes `experience_levels`, `experience_origin`, `experience_confidence`, and `experience_evidence`; Markdown and CSV must expose the same information in a human-readable compact form. Scraper URL tests must prove that HH/Habr single-level requests use server params and multi-level requests do not.

## Idempotence and Recovery

The implementation is additive until the final removal of old `experience` request plumbing. If tests fail after a milestone, fix forward by making the new API consistent rather than reintroducing legacy compatibility. Do not delete run data under `plugins/job-harness/data/.runs`; those paths are ignored by Git and are local runtime artifacts.

The verification commands can be rerun safely. If a live source smoke fails due to a network outage, rerun `python3 scripts/verify_repo.py live` once after confirming connectivity; do not weaken source behavior to satisfy a transient network failure.

## Artifacts and Notes

The research document `docs/experience-filtering.md` records the previous behavior and should be updated after implementation so it describes the new behavior rather than only the old semantics.

## Interfaces and Dependencies

The new internal module must expose stable names:

    VALID_EXPERIENCE_LEVELS: tuple[str, str, str]
    ExperienceAssessment
    assess_listing_experience(listing, source, support) -> ExperienceAssessment
    annotate_listing_experience(listing, source, support) -> JobListing
    parse_experience_levels(value) -> tuple[str, ...]

The exact implementation may use dataclasses and standard-library regex only. Do not add new third-party dependencies.

Change note: This plan was created to make the grade engine migration executable without prior conversation context. It intentionally records the user's decision to remove legacy `experience` compatibility.

Change note: The plan is now closed as implemented after the full verification gate passed and the final plugin manifest cleanup was requested.
