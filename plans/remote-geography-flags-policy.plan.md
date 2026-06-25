# Implement Applicant Geography Remote Policy

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository does not currently check in `PLANS.md`; this file follows the plan specification from `/Users/user/.codex/plugins/cache/ai-engineer-workbench/ai-engineer-workbench/0.2.1/skills/plan-file/references/PLANS.md`. The repository-specific scraper guidance read while authoring this plan is `.agents/skills/job-harness-scraper-development/SKILL.md`.


## Purpose / Big Picture

After this change, `job-harness-v2 search` will express remote intent from the applicant's point of view. A user will no longer ask for `remote_in_country` as a standalone flag, because that phrase is only meaningful when tied to where the applicant wants to work from. Instead, the public request will separate two geography concepts: where the applicant can work from, and where the vacancy itself is located.

The observable behavior is a search where `--work-from europe --remote-mode compatible-remote` keeps a globally remote United States vacancy, because global remote is compatible with working from Europe, but `--vacancy-geography europe` still removes that same United States vacancy because vacancy geography is a separate office or market constraint. The report and processed results will show remote scope and geography decisions clearly enough that a filtered card explains whether it failed remote eligibility, vacancy geography, or missing evidence.


## Progress

- [x] (2026-06-24 21:01Z) Read the ExecPlan authoring skill and canonical plan specification.
- [x] (2026-06-24 21:01Z) Read the repository scraper development skill.
- [x] (2026-06-24 21:01Z) Inspected the current request contract, CLI, source criteria catalog, post-processing geography logic, report expectations, and tests that mention `remote_in_country`, `remote_global`, and `countries`.
- [x] (2026-06-24 21:01Z) Captured the new policy decisions from the user: remove broad `any_remote`, do not expose standalone `remote_in_country`, and keep globally remote listings compatible with any requested work-from geography.
- [x] (2026-06-24 21:01Z) Authored this plan.
- [x] (2026-06-24 21:07Z) Added the mandatory test cases that must pass for request validation, CLI behavior, source criteria, post-processing decisions, diagnostics, and report presentation.
- [x] (2026-06-24 21:18Z) Expanded mandatory test cases to include invalid request combinations, invalid geography tokens, multi-country remote scopes, region intersection, source evidence precedence, and rows that fail more than one geography or remote criterion.
- [x] (2026-06-24 21:32Z) Implemented Milestone 1: updated the public request and enum contract to `remote_mode`, `work_from_geographies`, and `vacancy_geographies`.
- [x] (2026-06-24 21:32Z) Implemented Milestone 2: updated CLI, runtime skill text, and request serialization for the new flags.
- [x] (2026-06-24 21:32Z) Implemented Milestone 3: updated source criterion declarations and source-native collection hints without claiming broad remote collection is final filtering.
- [x] (2026-06-24 21:32Z) Implemented Milestone 4: implemented post-processing geography and remote-compatibility decisions from the new policy.
- [x] (2026-06-24 21:32Z) Implemented Milestone 5: updated processed result and report presentation fields so remote scope and geography decisions are inspectable.
- [x] (2026-06-24 21:34Z) Implemented Milestone 6: updated unit tests, source tests, deterministic verification, plugin version, and documentation.
- [x] (2026-06-24 21:32Z) Ran focused unit tests for contracts, CLI, post-processing, criteria planner, source catalog, source request mapping, and formatters.
- [x] (2026-06-24 21:34Z) Ran `python3 scripts/verify_v2.py --skip-live`; deterministic verification passed.


## Surprises & Discoveries

- Observation: The current public request exposes `remote_in_country`, `remote_global`, and `countries` directly.
  Evidence: `plugins/job-harness/src/job_harness/v2/contracts/search.py` defines those three fields on `SearchRequest`, and `plugins/job-harness/src/job_harness/v2/cli.py` exposes `--remote-in-country`, `--remote-global`, and `--country`.

- Observation: Source catalog capabilities currently treat broad source-native remote filtering as the same criterion as final remote compatibility.
  Evidence: `plugins/job-harness/src/job_harness/v2/source_catalog.sql` marks `career:vk` and `career:ibs` `remote_in_country` as `native_request`, while the actual user policy now needs remote compatibility with applicant geography.

- Observation: Post-processing already owns country normalization and region-scope matching.
  Evidence: `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py` normalizes country text using Babel CLDR territory names and defines explicit region scopes used by `_country_matches_requested_countries`.

- Observation: The current report contract still presents `Remote in country` and `Remote global` as primary card fields.
  Evidence: `plugins/job-harness/tests/v2/test_formatters.py` asserts that `report_template.html` renders `Remote in country` and `Remote global` fields.

- Observation: Source catalog country metadata is not precise enough to pre-filter the new `work_from_geographies` and `vacancy_geographies` contract safely.
  Evidence: `plugins/job-harness/src/job_harness/v2/runtime/catalog.py` previously filtered sources by `request.countries`; the implementation removes that request-country prefilter and leaves geography decisions to post-processing.


## Decision Log

- Decision: Replace request booleans `remote_in_country` and `remote_global` with one enum field named `remote_mode`.
  Rationale: Booleans allow unclear combinations and make `remote_in_country` look meaningful without a country. An enum makes the user's intent explicit: no remote filter, remote compatible with where the applicant works from, global remote only, or non-remote only.
  Date/Author: 2026-06-24 / Codex

- Decision: Do not include a public `any_remote` remote mode.
  Rationale: The user clarified that "show any vacancy that has any remote" is not a useful applicant-level intent because an applicant lives in or plans to move to a concrete country or region. Broad source-native remote filters can still help collect candidates, but final filtering must express compatibility.
  Date/Author: 2026-06-24 / Codex

- Decision: Split the old request `countries` concept into `work_from_geographies` and `vacancy_geographies`.
  Rationale: A globally remote United States vacancy should pass a request to work from Europe, but should not pass a request for vacancies located in Europe. One field cannot represent both meanings without surprising users.
  Date/Author: 2026-06-24 / Codex

- Decision: Keep remote evidence as listing facts, but do not expose those facts directly as request flags.
  Rationale: Sources still emit facts such as `remote_in_country`, `remote_global`, `country`, `location_text`, and raw remote restrictions. These facts are useful evidence for post-processing and presentation, but the public request should describe the applicant's intent rather than source internals.
  Date/Author: 2026-06-24 / Codex

- Decision: Change the v2 contract directly and update all callers, fixtures, tests, runtime skills, and plugin version in the same implementation sequence.
  Rationale: The plugin is in active early development. Direct contract changes are easier to reason about than compatibility shims or alternate old and new request paths.
  Date/Author: 2026-06-24 / Codex

- Decision: Positive remote and geography filters must distinguish unknown evidence from explicit mismatch.
  Rationale: A listing with no usable remote or geography evidence is different from a listing that clearly says `country:US` or `remote_scope=country:US`. Separate reasons such as `remote_eligibility_unknown`, `remote_global_unknown`, and `vacancy_geography_unknown` make filtered cards auditable.
  Date/Author: 2026-06-24 / Codex

- Decision: `non_remote_only` keeps only listings that are known to be onsite or non-remote.
  Rationale: Unknown remote evidence must not be silently converted to non-remote. This keeps the policy consistent with the rule that unknown is not false.
  Date/Author: 2026-06-24 / Codex

- Decision: `work_from_geographies` is valid only with `remote_mode=compatible_remote`.
  Rationale: Work-from geography has no filtering effect for `any`, `global_remote_only`, or `non_remote_only`. Rejecting unused input prevents a user or agent from thinking a geography constraint was applied when it was ignored.
  Date/Author: 2026-06-24 / Codex

- Decision: Remote scope may contain more than one country or region.
  Rationale: Some sources can expose several allowed countries or regions for one vacancy. The policy should keep a row when any allowed remote scope intersects the applicant's work-from geography and should render the scope as a concise list in presentation.
  Date/Author: 2026-06-24 / Codex

- Decision: Post-processing should preserve all remote and geography mismatch reasons that apply to a row.
  Rationale: The report already supports a list of decision reasons and the user needs red highlights for the parameter or parameters that removed a vacancy. A row that is both remote-incompatible and outside requested vacancy geography should show both reasons.
  Date/Author: 2026-06-24 / Codex


## Outcomes & Retrospective

Status: implementation complete; deterministic repository verification passed.

Expected final outcome: a user can run `job-harness-v2 search --queries "QA | AQA" --work-from europe --remote-mode compatible-remote` and receive globally remote jobs plus jobs whose country or remote scope intersects the project Europe scope. A row with vacancy country `US` and remote scope `global` is kept. A row with vacancy country `US` and remote scope `country:US` is removed with `remote_eligibility_mismatch`. A row with vacancy country `US` and remote scope `global` is removed only if the request also has `--vacancy-geography europe`, in which case the reason is `vacancy_geography_mismatch`.


## Context and Orientation

The repository root is `/Users/user/Documents/repos/qa-job-harness`. The installable plugin lives under `plugins/job-harness`. The v2 engine source code lives under `plugins/job-harness/src/job_harness/v2`.

The current search engine has three layers that must agree on public criteria. The public request contract is in `plugins/job-harness/src/job_harness/v2/contracts/search.py`. The closed enum list for criteria and other contract values is in `plugins/job-harness/src/job_harness/v2/contracts/enums.py`. The source catalog in `plugins/job-harness/src/job_harness/v2/source_catalog.sql` declares which criteria each source can apply natively, expose as structured output, or cannot support. Post-processing in `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py` reads raw listings and decides which rows are kept or filtered out.

Important current fields:

- `SearchRequest.remote_in_country` is a request boolean. This plan removes it from the public request.
- `SearchRequest.remote_global` is a request boolean. This plan removes it from the public request.
- `SearchRequest.countries` is currently used as a single country filter. This plan replaces it with two fields because the old field mixes applicant work-from geography and vacancy location geography.
- `RawListing.remote_in_country`, `RawListing.remote_global`, `RawListing.country`, `RawListing.city`, `RawListing.location_text`, and `RawListing.raw` are source evidence fields. This plan keeps source evidence available, but changes how post-processing interprets it.

Definitions used in this plan:

`Remote mode` is the user's remote-work intent. The target values are `any`, `compatible_remote`, `global_remote_only`, and `non_remote_only`.

`Work-from geography` is where the applicant wants to be located while doing remote work. It can be an ISO country code such as `RU` or `US`, or an explicit region scope such as `EU` or `europe`.

`Vacancy geography` is where the vacancy, office, employer market, or source card is located. It can also be an ISO country code or explicit region scope.

`Remote scope` is the post-processing interpretation of source evidence. It can contain one or more scopes. Examples are `global`, `country:RU`, `country:TR`, `region:EU`, `region:europe`, `onsite`, and `unknown`. If a source exposes multiple allowed remote geographies, the processed row should preserve them as multiple scopes rather than choosing one arbitrary country.

`Compatible remote` means that the listing is globally remote or its remote scope intersects at least one work-from geography requested by the user. For example, `remote_scope=global` matches `work_from_geographies=["europe"]`; `remote_scope=country:PL` matches `work_from_geographies=["europe"]`; `remote_scope=country:US` does not.

`Region scope` is a project-defined list of countries. `EU` means the current European Union country list. `europe` means the project-defined Europe scope and intentionally excludes `RU`.


## Plan of Work

Milestone 1 changes the public Python contract. In `plugins/job-harness/src/job_harness/v2/contracts/enums.py`, add a `RemoteMode` `StrEnum` with values `any`, `compatible_remote`, `global_remote_only`, and `non_remote_only`. Replace `SearchCriterion.REMOTE_IN_COUNTRY`, `SearchCriterion.REMOTE_GLOBAL`, and `SearchCriterion.COUNTRIES` with criteria that match the new contract: `REMOTE_MODE`, `WORK_FROM_GEOGRAPHIES`, and `VACANCY_GEOGRAPHIES`. Keep `CITIES` because city remains a vacancy-location criterion. In `plugins/job-harness/src/job_harness/v2/contracts/search.py`, replace the old request fields with `remote_mode: RemoteMode | None`, `work_from_geographies: tuple[str, ...]`, and `vacancy_geographies: tuple[str, ...]`. Normalize country codes to uppercase, normalize the region aliases `eu` and `europe` to `EU` and `europe`, dedupe case-insensitively, and reject unknown request geographies such as `global`, `moon`, or arbitrary free text that Babel cannot normalize to one explicit country code or approved region scope. Reject `remote_mode=compatible_remote` unless `work_from_geographies` is non-empty. Reject non-empty `work_from_geographies` unless `remote_mode=compatible_remote`. Treat `remote_mode=None` and `remote_mode=RemoteMode.ANY` as no requested remote criterion. Update `plugins/job-harness/src/job_harness/v2/contracts/__init__.py` to export `RemoteMode`.

Milestone 2 changes the user-facing CLI and agent-facing runtime guidance. In `plugins/job-harness/src/job_harness/v2/cli.py`, remove `--remote-in-country`, `--remote-global`, and `--country`. Add `--remote-mode` with choices `any`, `compatible-remote`, `global-remote-only`, and `non-remote-only`; convert CLI kebab-case to enum snake_case before constructing `SearchRequest`. Add repeatable `--work-from` for applicant work-from geography and repeatable `--vacancy-geography` for vacancy or office geography. Keep `--city` as a vacancy city filter. Update the JSON payload emitted from searches and processed results so `search_request` shows `remote_mode`, `work_from_geographies`, and `vacancy_geographies`. Update `plugins/job-harness/skills/job-search-workflow/SKILL.md` so agents use the new flags when turning natural-language requests into CLI commands.

Milestone 3 updates source criteria and source-native collection behavior. In `plugins/job-harness/src/job_harness/v2/contracts/criteria.py`, replace descriptors for the old remote and country criteria with descriptors for `remote_mode`, `work_from_geographies`, and `vacancy_geographies`. `remote_mode` should list source fact fields such as `remote_in_country`, `remote_global`, `location_text`, and `raw` because the postprocessor uses those facts to derive remote scope. `work_from_geographies` should list fields that can reveal remote eligibility, including `remote_in_country`, `remote_global`, `country`, `location_text`, and raw remote restrictions. `vacancy_geographies` should list `country`, `city`, `location_text`, and raw region facts. In `plugins/job-harness/src/job_harness/v2/source_catalog.sql`, update the `source_criteria` CHECK constraint and rows to use the new criteria. Treat broad source-native remote parameters such as VK `remote=true` and IBS remote URLs as collection hints, not final native support for `compatible_remote`. If the current architecture has no collection-hint declaration, keep those sources as `structured_output` for `remote_mode`; make the behavior explicit through helper names and tests rather than compatibility comments. Update `plugins/job-harness/src/job_harness/v2/runtime/sources/companies/vk.py` and `plugins/job-harness/src/job_harness/v2/runtime/sources/companies/ibs.py` so they use `request.remote_mode` to decide whether broad remote collection is worth requesting, but still rely on post-processing for final eligibility.

Milestone 4 implements the post-processing policy. In `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py`, replace `_country_matches_requested_countries` with geography helpers that can compare country codes to explicit region scopes. Keep this logic in the post-processing layer; do not hardcode country or region mapping inside individual source parsers. Add a helper that derives normalized `remote_scopes` for each row from existing listing facts. It should return `global` when `remote_global` is true, one or more `country:<code>` or `region:<scope>` values when remote is country or region limited and evidence is available, `onsite` when the source clearly says the listing is not remote, and `unknown` when the source does not provide enough evidence. If `remote_global` is true, treat the scope as global even if other country-limited hints are also present, unless the source exposes explicit country exclusions and the implementation models those exclusions. Update `_removal_reason` into a small reason collector so it can return every geography and remote reason that applies after query, company, text, grade, salary, and published-date checks. Evaluate remote mode, relocation, vacancy geography, and city. For `compatible_remote`, keep rows where one derived remote scope is `global` or intersects `work_from_geographies`; remove rows with explicit non-intersecting remote scopes as `remote_eligibility_mismatch` and rows with only `unknown` scope as `remote_eligibility_unknown`. For `global_remote_only`, keep only `global`; remove explicit non-global remote scopes as `remote_global_mismatch` and `unknown` as `remote_global_unknown`. For `non_remote_only`, keep only `onsite`; remove known remote scopes as `remote_mismatch` and `unknown` as `remote_scope_unknown`. For `vacancy_geographies`, do not let `global` remote satisfy the vacancy geography constraint; use only vacancy country, location, city, and raw region evidence. If vacancy geography evidence is absent, remove with `vacancy_geography_unknown`.

Milestone 5 updates processed results and presentation. Add `remote_scope` and a concise remote eligibility diagnostic to the row dictionaries produced by `_listing_rows` in `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py`. Update `plugins/job-harness/src/job_harness/v2/presentation/report_template.html` so the primary card shows `Remote scope` instead of separate headline fields `Remote in country` and `Remote global`. Keep raw `remote_in_country` and `remote_global` values in the debug section if they are still useful for source auditing. Update `plugins/job-harness/src/job_harness/v2/presentation/formatters.py` to render the same user-facing fields in markdown. Filtered-out cards should highlight the field related to `remote_eligibility_mismatch`, `remote_global_mismatch`, `vacancy_geography_mismatch`, or `work_from_geography_required` according to the same red-highlight mechanism already used for other reasons.

Milestone 6 updates tests, documentation, and plugin version. Update contract tests in `plugins/job-harness/tests/v2/test_contracts_search.py` and `plugins/job-harness/tests/v2/test_contracts_criteria.py`. Update `plugins/job-harness/tests/v2/test_application_cli.py` to assert that old flags are absent and new flags are present. Update post-processing tests in `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py` with a focused matrix: `work_from=europe` keeps `US + global`, removes `US + country:US`, keeps `PL + country:PL`, removes `RU + country:RU` because `europe` excludes `RU`, keeps `CY + region:EU`, and removes `RU + region:EU`. Add tests that `vacancy_geographies=europe` removes `US + global` with `vacancy_geography_mismatch`. Update source catalog tests in `plugins/job-harness/tests/v2/test_source_catalog.py`, source request tests in `plugins/job-harness/tests/v2/test_runtime_sources_contract_first.py`, formatter tests in `plugins/job-harness/tests/v2/test_formatters.py`, and any support fixtures that serialize the request. Update `docs/search-system-spec.md` if implementation names differ from the current draft. Because this changes the installable plugin runtime, bump versions together in `plugins/job-harness/.codex-plugin/plugin.json`, `plugins/job-harness/pyproject.toml`, and the local package entry in `plugins/job-harness/uv.lock`.


## Required Test Cases

The implementation must add or update the following deterministic tests. These tests are part of the acceptance contract for this plan; do not rely only on `scripts/verify_v2.py` to catch these behaviors.

In `plugins/job-harness/tests/v2/test_contracts_search.py`, add request contract tests. `test_normalizes_remote_mode_and_geographies` should construct a request with `remote_mode=RemoteMode.COMPATIBLE_REMOTE`, `work_from_geographies=(" europe ", "EU", "pl", "PL")`, and `vacancy_geographies=(" cy ", "CY")`; it should assert that the remote mode is preserved, the region values normalize to `("europe", "EU", "PL")`, and the vacancy geography dedupes to `("CY",)`. `test_rejects_compatible_remote_without_work_from_geography` should assert that `SearchRequest(query_variants=("QA",), remote_mode=RemoteMode.COMPATIBLE_REMOTE)` raises a `ValueError` mentioning `work_from_geographies`. `test_rejects_work_from_without_compatible_remote` should assert that `work_from_geographies=("RU",)` with `remote_mode=None`, `RemoteMode.ANY`, `RemoteMode.GLOBAL_REMOTE_ONLY`, or `RemoteMode.NON_REMOTE_ONLY` raises a `ValueError` explaining that work-from geography is only valid for compatible remote. `test_rejects_invalid_request_geographies` should assert that request values such as `global`, `moon`, and `not a country` are rejected for both work-from and vacancy geographies. `test_rejects_old_request_fields` should assert that passing old keyword arguments such as `remote_in_country`, `remote_global`, or `countries` raises `TypeError`. `test_requested_criteria_reflects_new_fields` should assert that a request with `remote_mode`, `work_from_geographies`, `vacancy_geographies`, and `cities` yields `SearchCriterion.REMOTE_MODE`, `SearchCriterion.WORK_FROM_GEOGRAPHIES`, `SearchCriterion.VACANCY_GEOGRAPHIES`, and `SearchCriterion.CITIES`, with none of the removed criteria present.

In `plugins/job-harness/tests/v2/test_contracts_criteria.py`, add criterion descriptor tests. One test should assert that `ALL_SEARCH_CRITERIA` contains exactly `query`, `grades`, `salary_from`, `published_since`, `relocation`, `remote_mode`, `work_from_geographies`, `vacancy_geographies`, and `cities`. Another test should assert that the descriptors for `remote_mode`, `work_from_geographies`, and `vacancy_geographies` point to source fact fields that post-processing can actually read, including remote flags, country, city, location text, and raw source facts where relevant.

In `plugins/job-harness/tests/v2/test_application_cli.py`, add CLI surface tests. `test_search_help_exposes_new_remote_geography_flags` should assert that help contains `--remote-mode`, `--work-from`, and `--vacancy-geography`, and does not contain `--remote-in-country`, `--remote-global`, or `--country`. `test_cli_rejects_any_remote_mode` should assert that `--remote-mode any-remote` fails argument parsing. `test_cli_rejects_invalid_remote_geography_combination` should assert that `--work-from RU --remote-mode global-remote-only` fails with a clear validation error. `test_cli_rejects_invalid_geography_token` should assert that `--work-from global --remote-mode compatible-remote` and `--vacancy-geography moon` fail validation. `test_cli_builds_remote_geography_request` should run the CLI through the existing application stub or test harness with `--queries QA --remote-mode compatible-remote --work-from europe --vacancy-geography CY`, then assert that the constructed or serialized request contains `remote_mode: "compatible_remote"`, `work_from_geographies: ["europe"]`, and `vacancy_geographies: ["CY"]`.

In `plugins/job-harness/tests/v2/test_source_catalog.py`, add source catalog tests. One test should assert that no catalog row uses removed criteria `remote_in_country`, `remote_global`, or `countries`. Another test should assert that sources previously using broad native remote collection, such as `career:vk` and `career:ibs`, do not claim native final support for `compatible_remote`; their final remote criteria should be `structured_output` or `unsupported` according to the implemented catalog model, with post-processing responsible for final eligibility.

In `plugins/job-harness/tests/v2/test_runtime_sources_contract_first.py`, update source request-mapping tests for VK and IBS. When the request has `remote_mode=RemoteMode.COMPATIBLE_REMOTE` and a non-empty `work_from_geographies`, a source may still use its broad source-native remote request parameter to collect likely remote candidates. The test must assert the source request URL shape and also assert that this does not mark the final remote criterion as natively satisfied. When `remote_mode` is absent or `RemoteMode.ANY`, the broad source-native remote parameter should not be added only because a vacancy geography was requested.

In `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py`, add the main decision matrix. For `remote_mode=RemoteMode.COMPATIBLE_REMOTE` and `work_from_geographies=("europe",)`, assert that `remote_scope=global` with vacancy country `US` is kept, `remote_scope=country:US` is removed with `remote_eligibility_mismatch`, `remote_scope=country:PL` is kept, `remote_scope=country:RU` is removed with `remote_eligibility_mismatch`, `remote_scope=region:europe` intersects the request and is kept, `remote_scope=region:EU` intersects the request and is kept, `remote_scope=onsite` is removed with `remote_eligibility_mismatch`, and `remote_scope=unknown` is removed with `remote_eligibility_unknown`. Add country-specific region tests too: with `work_from_geographies=("CY",)`, `remote_scope=region:EU` is kept; with `work_from_geographies=("RU",)`, `remote_scope=region:EU` is removed. With `work_from_geographies=("RU", "EU")`, `remote_scope=country:CY` is kept and `remote_scope=country:US` is removed.

In the same post-processing test file, add remote-scope derivation tests. If `remote_global` is true, the derived remote scope is `global` even when country or region hints are also present. If `remote_in_country` is true and raw evidence contains several allowed countries such as `PL` and `CY`, the row preserves both `country:PL` and `country:CY` and compatible remote keeps the row when any requested work-from geography intersects either country. If `remote_in_country` is true but no country or region evidence is available, the scope is `unknown` and compatible remote removes the row with `remote_eligibility_unknown`. If both remote flags are false and the source gives no contradictory raw remote evidence, the scope is `onsite`.

In the same post-processing test file, add separate tests for vacancy geography. With `vacancy_geographies=("europe",)` and no work-from requirement, a listing whose vacancy country is `PL` should be kept, a listing whose vacancy country is `US` and remote scope is `global` should be removed with `vacancy_geography_mismatch`, and a listing with no usable vacancy geography should be removed with `vacancy_geography_unknown`. With both `work_from_geographies=("europe",)` and `vacancy_geographies=("europe",)`, the `US + global` row should still be removed with `vacancy_geography_mismatch`, proving that global remote only satisfies the work-from dimension.

In the same post-processing test file, add remote-mode-specific tests. For `RemoteMode.GLOBAL_REMOTE_ONLY`, `remote_scope=global` is kept, country-limited and region-limited remote scopes are removed with `remote_global_mismatch`, onsite is removed with `remote_global_mismatch`, and unknown is removed with `remote_global_unknown`. For `RemoteMode.NON_REMOTE_ONLY`, onsite is kept, global/country/region remote scopes are removed with `remote_mismatch`, and unknown is removed with `remote_scope_unknown`. For `RemoteMode.ANY` or `None`, remote scope alone does not remove a row.

In the same post-processing test file, add multiple-reason tests. A row with `work_from_geographies=("europe",)`, `vacancy_geographies=("europe",)`, `remote_mode=RemoteMode.COMPATIBLE_REMOTE`, vacancy country `US`, and remote scope `country:US` should be filtered with both `remote_eligibility_mismatch` and `vacancy_geography_mismatch`. A row that is globally remote but has no vacancy geography should be filtered only with `vacancy_geography_unknown` when vacancy geography is requested, proving that `global` remote satisfied the remote dimension. A row with `remote_scope=unknown` and no vacancy geography should include both `remote_eligibility_unknown` and `vacancy_geography_unknown` when both dimensions are requested.

In `plugins/job-harness/tests/v2/test_formatters.py`, add presentation tests. The report should render `Remote scope` as a primary card field, should not render `Remote in country` or `Remote global` as primary card fields, and should still expose raw remote facts in a debug area when present. A filtered row with `remote_eligibility_mismatch`, `remote_eligibility_unknown`, `remote_global_mismatch`, `remote_global_unknown`, `remote_scope_unknown`, `vacancy_geography_mismatch`, or `vacancy_geography_unknown` should highlight the relevant `Remote scope` or `Vacancy geography` field using the same red highlight mechanism as other filtered parameters.

In `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py` or a small dedicated serialization test, assert the processed payload's `search_request` shape. It must contain `remote_mode`, `work_from_geographies`, and `vacancy_geographies`, and must not contain `remote_in_country`, `remote_global`, or `countries`.


## Concrete Steps

Work from the repository root:

    cd /Users/user/Documents/repos/qa-job-harness

Confirm the current branch before implementation:

    git branch --show-current

Expected output during this plan's creation:

    codex/detail-enrichment-flow-plan

Before changing code for this plan, inspect the current dirty worktree and avoid reverting unrelated work:

    git status --short

Run a baseline focused test group:

    uv --directory plugins/job-harness run python -m unittest \
      tests.v2.test_contracts_search \
      tests.v2.test_contracts_criteria \
      tests.v2.test_application_cli \
      tests.v2.test_postprocessing_pipeline \
      tests.v2.test_source_catalog \
      tests.v2.test_formatters

Implement Milestone 1 by editing:

- `plugins/job-harness/src/job_harness/v2/contracts/enums.py`
- `plugins/job-harness/src/job_harness/v2/contracts/search.py`
- `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`
- `plugins/job-harness/tests/v2/test_contracts_search.py`
- `plugins/job-harness/tests/v2/test_contracts_criteria.py`

Implement Milestones 2 and 3 by editing:

- `plugins/job-harness/src/job_harness/v2/cli.py`
- `plugins/job-harness/skills/job-search-workflow/SKILL.md`
- `plugins/job-harness/src/job_harness/v2/contracts/criteria.py`
- `plugins/job-harness/src/job_harness/v2/source_catalog.sql`
- `plugins/job-harness/src/job_harness/v2/runtime/sources/companies/vk.py`
- `plugins/job-harness/src/job_harness/v2/runtime/sources/companies/ibs.py`
- `plugins/job-harness/tests/v2/test_application_cli.py`
- `plugins/job-harness/tests/v2/test_source_catalog.py`
- `plugins/job-harness/tests/v2/test_runtime_sources_contract_first.py`

Implement Milestones 4 and 5 by editing:

- `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py`
- `plugins/job-harness/src/job_harness/v2/presentation/report_template.html`
- `plugins/job-harness/src/job_harness/v2/presentation/formatters.py`
- `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py`
- `plugins/job-harness/tests/v2/test_formatters.py`

After each milestone, update this plan's `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` sections if implementation reveals new facts or changes course.

Run focused tests after each runtime milestone:

    uv --directory plugins/job-harness run python -m unittest tests.v2.test_contracts_search
    uv --directory plugins/job-harness run python -m unittest tests.v2.test_application_cli
    uv --directory plugins/job-harness run python -m unittest tests.v2.test_postprocessing_pipeline
    uv --directory plugins/job-harness run python -m unittest tests.v2.test_source_catalog
    uv --directory plugins/job-harness run python -m unittest tests.v2.test_formatters

Run the deterministic v2 gate before handoff:

    python3 scripts/verify_v2.py --skip-live

Run the bounded live profile only after deterministic tests pass and only as operational evidence:

    python3 scripts/verify_v2.py --live-profile light


## Validation and Acceptance

The implementation is accepted when the following behaviors are observable.

CLI surface:

    uv --directory plugins/job-harness run job-harness-v2 search --help

The help output includes `--remote-mode`, `--work-from`, and `--vacancy-geography`. It does not include `--remote-in-country`, `--remote-global`, or `--country`.

Request validation:

Constructing `SearchRequest(query_variants=("QA",), remote_mode=RemoteMode.COMPATIBLE_REMOTE)` raises `ValueError` mentioning `work_from_geographies`. Constructing `SearchRequest(query_variants=("QA",), remote_mode=RemoteMode.COMPATIBLE_REMOTE, work_from_geographies=("europe",))` succeeds and normalizes the region as `europe`. Constructing a request with `work_from_geographies=("RU",)` and `remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY`, `RemoteMode.NON_REMOTE_ONLY`, `RemoteMode.ANY`, or `None` raises `ValueError`, because work-from geography is meaningful only for compatible remote. Invalid request geographies such as `global`, `moon`, and arbitrary unrecognized free text are rejected.

Remote compatibility:

Use a post-processing unit test with multiple raw listings and `work_from_geographies=("europe",)`, `remote_mode=RemoteMode.COMPATIBLE_REMOTE`. A listing with vacancy country `US` and remote scope `global` is kept. A listing with vacancy country `US` and remote scope `country:US` is filtered with `remote_eligibility_mismatch`. A listing with vacancy country `PL` and remote scope `country:PL` is kept. A listing with vacancy country `RU` and remote scope `country:RU` is filtered because `RU` is not in the project `europe` scope. A listing with multiple remote scopes such as `country:PL` and `country:CY` is kept when any requested work-from geography intersects those scopes. A listing with unknown remote scope is filtered with `remote_eligibility_unknown`.

Vacancy geography remains separate:

Use a post-processing unit test with `work_from_geographies=("europe",)`, `vacancy_geographies=("europe",)`, and `remote_mode=RemoteMode.COMPATIBLE_REMOTE`. A listing with vacancy country `US` and remote scope `global` is filtered with `vacancy_geography_mismatch`, proving that global remote satisfies work-from eligibility but not vacancy geography. A listing with no usable vacancy geography is filtered with `vacancy_geography_unknown` when vacancy geography is requested.

Multiple reasons:

Use a post-processing unit test with `work_from_geographies=("europe",)`, `vacancy_geographies=("europe",)`, and `remote_mode=RemoteMode.COMPATIBLE_REMOTE`. A listing with vacancy country `US` and remote scope `country:US` is filtered with both `remote_eligibility_mismatch` and `vacancy_geography_mismatch`. A listing with unknown remote scope and no usable vacancy geography is filtered with both `remote_eligibility_unknown` and `vacancy_geography_unknown`.

Global remote only:

Use a post-processing unit test with `remote_mode=RemoteMode.GLOBAL_REMOTE_ONLY`. Listings with remote scope `global` are kept. Listings with remote scope `country:PL`, `region:EU`, or `onsite` are filtered with `remote_global_mismatch`. Listings with unknown remote scope are filtered with `remote_global_unknown`.

Non-remote only:

Use a post-processing unit test with `remote_mode=RemoteMode.NON_REMOTE_ONLY`. Listings with remote scope `onsite` are kept. Listings with remote scope `global`, `country:PL`, or `region:EU` are filtered with `remote_mismatch`. Listings with unknown remote scope are filtered with `remote_scope_unknown`.

Presentation:

Open `report.html` from a deterministic fixture-backed run. Kept and filtered cards show `Remote scope` as a primary field. Raw booleans such as `remote_in_country` and `remote_global`, if still present, appear only in debug fields. A filtered row removed by remote compatibility highlights the remote-scope field in red. A filtered row removed by vacancy geography highlights the vacancy-geography field in red.

Repository verification:

    python3 scripts/verify_v2.py --skip-live

Expected result: command exits with status 0. If live evidence is needed:

    python3 scripts/verify_v2.py --live-profile light

Expected result: command exits with status 0 or reports source-specific live access outcomes that are classified according to the v2 taxonomy.


## Idempotence and Recovery

The implementation is safe to repeat because it changes Python contracts, SQL catalog declarations, tests, and generated plugin metadata in place. There is no data migration for old run artifacts in this plan. Existing `run.sqlite` artifacts created by old request shapes are historical outputs; new runs should serialize only the new request fields.

If implementation reaches a broken intermediate state, inspect the diff rather than resetting the worktree:

    git status --short
    git diff -- docs/search-system-spec.md plugins/job-harness/src/job_harness/v2 plugins/job-harness/tests/v2

Do not run `git reset --hard` or revert unrelated user changes. If old run artifacts created during manual testing are untracked under `.job-harness/`, remove only the specific throwaway run directory after recording any evidence needed for validation.


## Artifacts and Notes

The current draft specification in `docs/search-system-spec.md` already describes the target request language:

    remote_mode: RemoteMode | null
    work_from_geographies: list[Country | RegionScope] | null
    vacancy_geographies: list[Country | RegionScope] | null

The target `RemoteMode` enum should be:

    class RemoteMode(StrEnum):
        ANY = "any"
        COMPATIBLE_REMOTE = "compatible_remote"
        GLOBAL_REMOTE_ONLY = "global_remote_only"
        NON_REMOTE_ONLY = "non_remote_only"

The target request fields should look like:

    @dataclass(frozen=True)
    class SearchRequest:
        query_variants: tuple[str, ...]
        grades: tuple[Grade, ...] = ()
        salary_from: int | None = None
        published_since: date | None = None
        exclude_companies: tuple[str, ...] = ()
        exclude_text: tuple[TextExclusion, ...] = ()
        relocation: bool | None = None
        remote_mode: RemoteMode | None = None
        work_from_geographies: tuple[str, ...] = ()
        vacancy_geographies: tuple[str, ...] = ()
        cities: tuple[str, ...] = ()
        sources: tuple[str, ...] = ()
        source_types: tuple[SourceType, ...] = ()
        append_to_run_id: str | None = None

Example CLI commands after the change:

    uv --directory plugins/job-harness run job-harness-v2 search \
      --queries "QA | AQA | SDET" \
      --work-from europe \
      --remote-mode compatible-remote

    uv --directory plugins/job-harness run job-harness-v2 search \
      --queries "QA | AQA | SDET" \
      --vacancy-geography CY \
      --remote-mode any

    uv --directory plugins/job-harness run job-harness-v2 search \
      --queries "QA | AQA | SDET" \
      --remote-mode global-remote-only

Expected processed `search_request` payload shape:

    {
      "query_variants": ["QA", "AQA", "SDET"],
      "remote_mode": "compatible_remote",
      "work_from_geographies": ["europe"],
      "vacancy_geographies": [],
      "cities": []
    }


## Interfaces and Dependencies

Use only existing project dependencies for geography normalization. Babel is already present in `plugins/job-harness/pyproject.toml` and `plugins/job-harness/uv.lock`; keep country and region normalization in post-processing code rather than individual source parsers.

In `plugins/job-harness/src/job_harness/v2/contracts/enums.py`, define `RemoteMode` and replace the old search criteria with:

    class SearchCriterion(StrEnum):
        QUERY = "query"
        GRADES = "grades"
        SALARY_FROM = "salary_from"
        PUBLISHED_SINCE = "published_since"
        RELOCATION = "relocation"
        REMOTE_MODE = "remote_mode"
        WORK_FROM_GEOGRAPHIES = "work_from_geographies"
        VACANCY_GEOGRAPHIES = "vacancy_geographies"
        CITIES = "cities"

In `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py`, define small helpers with behavior equivalent to:

    def _remote_scope(row: dict[str, object]) -> str:
        ...

    def _remote_scope_matches_work_from(remote_scope: str, geographies: tuple[str, ...]) -> bool:
        ...

    def _vacancy_matches_geographies(row: dict[str, object], geographies: tuple[str, ...]) -> bool:
        ...

These helpers should use the same explicit region country sets for `EU` and `europe`. They must not infer region membership from time zones, source language, source domain, or popularity.

In `plugins/job-harness/src/job_harness/v2/cli.py`, parse CLI values like this:

    --remote-mode compatible-remote -> RemoteMode.COMPATIBLE_REMOTE
    --remote-mode global-remote-only -> RemoteMode.GLOBAL_REMOTE_ONLY
    --remote-mode non-remote-only -> RemoteMode.NON_REMOTE_ONLY
    --work-from europe -> work_from_geographies=("europe",)
    --vacancy-geography CY -> vacancy_geographies=("CY",)


## Change Note

2026-06-24 / Codex: Created the initial ExecPlan for migrating remote and geography search flags to applicant-centered semantics. The plan incorporates the user's design constraints: no standalone `remote_in_country` request flag, no broad `any_remote` final filter, explicit split between work-from geography and vacancy geography, globally remote jobs compatible with any requested work-from geography, country normalization in post-processing, and direct v2 contract changes without legacy compatibility paths.

2026-06-24 / Codex: Added mandatory test cases for request validation, CLI flags, source criteria, source-native collection hints, post-processing decision matrices, processed payload serialization, and report presentation. The update also made unknown remote/geography evidence explicit in diagnostics and clarified that `non_remote_only` keeps only known onsite/non-remote rows.

2026-06-24 / Codex: Expanded the test matrix to cover invalid request combinations, invalid geography tokens, multiple remote scopes, region-to-region and country-to-region intersection, remote evidence precedence, and rows with multiple simultaneous removal reasons.
