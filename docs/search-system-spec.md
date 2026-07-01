# Job Search System Specification

## Purpose

This document defines the target behavior of `job-harness` as an agent-facing job search system. It is a specification, not an implementation plan.

The system must let an AI agent search broadly across job sources, preserve raw evidence, apply honest post-processing, and then let the agent perform final human-style analysis over a stable dataset.

## Core Concepts

### Agent

The agent is the primary user of this system. Humans do not operate scrapers directly. The agent translates a natural-language search brief into structured search requests, inspects source diagnostics, appends additional search variants when needed, and prepares the final answer.

### Source

A source is exactly one registered job data provider.

Allowed source types:

- `aggregator`: a job board or aggregator such as hh.ru, Habr Career, Getmatch, or similar.
- `company_career`: a direct employer career site or a source that probes known employer career pages.

Directory-like datasets can exist as lookup helpers, but they are not search sources unless they return confirmed vacancy records. A company directory should help discover employer targets; it should not be mixed into raw vacancy search output as if it were a vacancy source.

### Scraper

A scraper is a source-specific collector. It must know the real structure of its source. It must not be a generic best-guess parser pretending to support arbitrary sites.

Supporting a source means adding a full source-specific scraper and its full test suite. There is no shortcut path where a source is marked supported because a generic parser, broad CSS selector, search-engine result, or LLM-generated extraction appears to work once. If the parser and required fixtures are not present, the source is not supported.

Transport is an implementation detail declared by the scraper:

- `http`: structured API, SSR payload, JSON-LD, static HTML, or ordinary HTTP plus parser.
- `browser`: Playwright/rebrowser path when source behavior genuinely requires a browser.
- `hybrid`: optional future mode when HTTP search and browser detail enrichment are both required.

Default engineering policy: start with `http` when the source exposes stable HTTP/structured data; use browser automation only when the source requires rendered interaction, anti-bot-compatible navigation, or browser-only data.

### Raw Search Corpus

Every source writes source-native normalized facts into one append-only raw search corpus. The corpus is the evidence layer and must not contain downstream ranking, dedupe, final filtering, grade estimation, or presentation truncation.

Post-processing reads this raw corpus and produces derived views. Re-running post-processing must be idempotent.

## Search Request Contract

The public search request should contain:

- `query_variants: list[str]`: required; one or more text formulations, such as `["QA", "AQA", "SDET", "тестировщик", "quality assurance"]`. The CLI accepts repeated `--query` flags and compact pipe-separated `--queries` strings for the same engine contract.
- `grades: list[Grade] | null`: optional exact grade list. Valid values: `intern`, `junior`, `middle`, `senior`, `lead`. The exact enum can be narrowed by implementation, but the semantics must stay exact-match, not minimum seniority.
- `salary_from: int | null`: optional lower salary bound.
- `published_since: date | null`: optional lower date bound for freshness. The name should avoid the ambiguity of "не позже"; this means "vacancy was published on or after this date".
- `exclude_companies: list[str] | null`: optional case-insensitive company exclusion. This applies both to returned listings and, when possible, to source/company target selection.
- `exclude_text: list[TextExclusion] | null`: optional post-processing exclusions over vacancy text.
- `relocation: bool | null`: optional relocation support filter.
- `remote_mode: RemoteMode | null`: optional remote intent. Valid values are `any`, `compatible_remote`, `global_remote_only`, and `non_remote_only`.
- `hybrid_ok: bool`: whether hybrid vacancies may satisfy the search when their geography matches the requested search geography.
- `office_ok: bool`: whether office vacancies may satisfy the search when their geography matches the requested search geography.
- `work_from_geographies: list[Country | RegionScope] | null`: optional countries or explicit regions from which the applicant wants to work remotely, for example `["RU"]` or `["europe"]`.
- `vacancy_geographies: list[Country | RegionScope] | null`: optional countries or explicit regions where the vacancy, office, employer market, or source card is located.
- `cities: list[str] | null`: optional vacancy city list. Cities must not require an exhaustive enum.
- `max_results: int`: final desired result count after filtering. `0` means no final presentation cap; source-local limits and run deadlines still apply.
- `sources: list[str] | null`: optional exact source ids.
- `source_groups: list[SourceType] | null`: optional broad source selection.
- `append_to_run_id: str | null`: optional existing run/corpus id for append mode.

`TextExclusion`:

- `pattern: str`
- `mode: "substring" | "regex"`
- `case_sensitive: bool`
- `fields: list["title" | "description" | "requirements" | "skills" | "raw_text"] | null`

## Country And Remote Filter Semantics

This section is the target behavior for interpreting geography and remote filters together. It is intentionally explicit because a source can use the same words for different concepts: the country where the employer is located, the country shown on a vacancy card, the country from which remote work is allowed, and a broader remote region such as `EU`, `europe`, or `global`.

Definitions:

- Work-from geography means a country or explicit region in `SearchRequest.work_from_geographies`, for example `RU`, `EU`, or `europe`. This is where the applicant wants to be located while working remotely.
- Vacancy geography means a country or explicit region in `SearchRequest.vacancy_geographies`. This is where the vacancy, office, employer market, or source card is located.
- Vacancy country means the normalized country or region scope derived from source evidence such as `listing.country`, `location_text`, source `regions`, source `remote_restrictions`, source `remote_type`, or city inference. It can be an ISO country code such as `RU`, the `EU` region scope, or `null`.
- Remote global means the vacancy can be performed remotely from any country unless the source exposes explicit exclusions. A plain `remote` / `удаленно` marker is not enough evidence for global remote; it becomes country-limited or `unknown` unless the source explicitly exposes `global`, `worldwide`, `anywhere`, `Весь мир`, or an equivalent structured scope.
- Hybrid and office are physical work formats, not remote scopes. When `hybrid_ok` or `office_ok` is true with `compatible_remote`, a physical-format vacancy may satisfy the search only when the vacancy country or region intersects `work_from_geographies`; when `vacancy_geographies` is present, the same row must satisfy that location constraint too. These flags cannot be combined with `global_remote_only`, because that mode means only globally remote vacancies. They do not weaken `non_remote_only`. If the source does not expose enough geography evidence for an accepted physical format, the row is removed with a work-format geography diagnostic. When source evidence lists remote together with hybrid or onsite options, remote wins and the row is evaluated with remote-scope rules.
- Timezone ranges such as `remote from GMT-7 to GMT+4` are eligibility hints, not geography. They can support the `remote` work format but do not create a country or region scope. A remote vacancy with city-only evidence gets a country-limited remote scope inferred from the city, and multi-city locations may produce multiple country scopes.
- Unknown means the source did not provide enough structured or text evidence. Unknown must not be silently converted to `false`; if a positive filter is requested, unknown rows should be removed with a diagnostic reason that distinguishes missing evidence from explicit mismatch.

`RemoteMode` values:

- `any`: do not filter by remote eligibility.
- `compatible_remote`: keep listings that are globally remote or whose remote scope intersects `work_from_geographies`. This mode requires at least one work-from geography.
- `global_remote_only`: keep only globally remote listings.
- `non_remote_only`: keep only listings that are known not to be remote.

The search request must not expose `remote_in_country` as a standalone boolean. It is a listing fact or a normalized remote scope, not a useful user intent without a work-from geography.

The search request also must not expose a broad "any remote" final filter. A source-native broad remote parameter such as `remote=true` can still be useful for collecting candidate rows, but final post-processing should express user intent as `compatible_remote`, `global_remote_only`, `non_remote_only`, or no remote filter.

`docs/workplace-geography-filtering.md` is the focused contract for workplace
and geography edge cases. The matrix below keeps the broader search-system
summary in sync with that file.

For this matrix, vacancy remote scope is the normalized post-processing interpretation of source evidence. It does not have to be one raw field. Examples are `global`, `country:RU`, `country:TR`, `region:EU`, `onsite`, and `unknown`.

Rows assume that earlier filters such as query, title, grade, salary, publication date, excluded company, and excluded text did not already remove the vacancy. When both `work_from_geographies` and `vacancy_geographies` are specified, both dimensions must pass. A limited remote vacancy can satisfy those dimensions with separate row facts: its remote scope must intersect `work_from_geographies`, and its vacancy countries must satisfy `vacancy_geographies`. Physical formats cannot bridge non-intersecting request geographies. A global remote vacancy satisfies the vacancy geography dimension when the user explicitly searches remote through `compatible_remote` or `global_remote_only`, because global remote is not tied to one vacancy country.

| Case | Request `work_from_geographies` | Request `vacancy_geographies` | Request `remote_mode` | Request physical flag | Request `relocation` | Vacancy country / region | Vacancy remote scope / format | Vacancy relocation | Decision | Reason if removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | empty | empty | `any` or `null` | false | `null` | any | any | any | keep | n/a |
| 2 | empty | empty | `compatible_remote` | false | `null` | any | any | any | request error | `work_from_geography_required` |
| 3 | `europe` | empty | `compatible_remote` | false | `null` | `US` | `global` | any | keep | n/a |
| 4 | `europe` | empty | `compatible_remote` | false | `null` | `US` | `country:US` | any | remove | `remote_eligibility_mismatch` |
| 5 | `europe` | empty | `compatible_remote` | false | `null` | `PL` | `country:PL` | any | keep | n/a |
| 6 | `europe` | empty | `compatible_remote` | false | `null` | `RU` | `country:RU` | any | remove, because the project Europe scope excludes `RU` | `remote_eligibility_mismatch` |
| 7 | `RU` | empty | `compatible_remote` | false | `null` | `US` | `global` | any | keep | n/a |
| 8 | `RU` | empty | `compatible_remote` | false | `null` | `TR` | `country:TR` | any | remove | `remote_eligibility_mismatch` |
| 9 | `CY` | empty | `compatible_remote` | false | `null` | `region:EU` | `region:EU` | any | keep | n/a |
| 10 | `RU` | empty | `compatible_remote` | false | `null` | `region:EU` | `region:EU` | any | remove, because `RU` is not an EU country | `remote_eligibility_mismatch` |
| 11 | empty | `europe` | `any` or `null` | false | `null` | `PL` | any | any | keep | n/a |
| 12 | empty | `europe` | `any` or `null` | false | `null` | `US` | `global` | any | remove, because global remote does not satisfy vacancy location intent | `vacancy_geography_mismatch` |
| 13 | `europe` | `europe` | `compatible_remote` | false | `null` | `US` | `global` | any | keep | n/a |
| 14 | empty | empty | `global_remote_only` | false | `null` | any | `global` | any | keep | n/a |
| 15 | empty | empty | `global_remote_only` | false | `null` | any | country-limited, region-limited, or physical | any | remove | `remote_global_mismatch` |
| 16 | empty | empty | `global_remote_only` | false | `null` | any | `unknown` | any | remove | `remote_global_unknown` |
| 17 | empty | empty | `non_remote_only` | false | `null` | any | `global`, country-limited, or region-limited | any | remove | `remote_mismatch` |
| 18 | empty | empty | `non_remote_only` | false | `null` | any | `onsite` | any | keep | n/a |
| 19 | empty | empty | `non_remote_only` | false | `null` | any | `unknown` | any | remove | `remote_scope_unknown` |
| 20 | `RU` | empty | `compatible_remote` | `hybrid_ok` | `null` | `RU` | `hybrid` | any | keep | n/a |
| 21 | `RU` | empty | `compatible_remote` | `hybrid_ok` | `null` | `TR` | `hybrid` | any | remove | `hybrid_geography_mismatch` |
| 22 | `RU` | empty | `compatible_remote` | `office_ok` | `null` | unknown | `office` | any | remove | `office_geography_unknown` |
| 23 | empty | `CY` | `global_remote_only` | `office_ok` | `null` | `CY` | `office` | any | request error | `global_remote_only_conflicts_with_physical_flags` |
| 24 | empty | `CY` | `global_remote_only` | `office_ok` | `null` | `RU` | `office` | any | request error | `global_remote_only_conflicts_with_physical_flags` |
| 25 | `RU` | empty | `compatible_remote` | false | `true` | any | compatible with `RU` | `true` | keep | n/a |
| 26 | `RU` | empty | `compatible_remote` | false | `true` | any | compatible with `RU` | `false` or `unknown` | remove | `relocation_mismatch` |
| 27 | `RU` | empty | `compatible_remote` | false | `false` | any | compatible with `RU` | `true` | remove | `relocation_mismatch` |
| 28 | `RU` | empty | `compatible_remote` | false | `false` | any | compatible with `RU` | `false` or `unknown` | keep | n/a |

Region scopes must be explicit. `EU` means the current European Union country list. `europe` means the project-defined Europe scope and intentionally does not include `GB` or `RU`. The system must not infer region membership from time zones, source domain, language, or source popularity.

The implementation should preserve enough debug evidence for the agent to audit each decision. A filtered card should make it clear whether the row was removed because the country was explicitly different, the remote eligibility was explicitly different, or the source did not provide enough evidence.

## Source Capability Contract

Every source must declare:

- stable `source_id`;
- `source_type`: `aggregator` or `company_career`;
- `countries`: supported country codes, or empty if the source is multi-country but cannot predeclare exact coverage;
- `transport`: `http`, `browser`, or `hybrid`;
- `source_limit`: source-local raw collection cap;
- supported native request criteria;
- supported structured output fields;
- unsupported requested criteria diagnostics.

Capabilities are static declarations. They are not runtime outcomes.

For each search criterion, a source declares exactly one collection capability:

| Capability | Meaning | Example |
| --- | --- | --- |
| `native_request` | The source can enforce the criterion before returning listings. | hh.ru `salary=200000`; Habr `qualification=middle`. |
| `structured_output` | The source cannot filter by the criterion, but returns a stable field that downstream processing can use. | API field `workType=REMOTE`; JSON field `publishedAt`. |
| `unsupported` | The source cannot enforce or expose the criterion honestly. | Relocation is absent from search params and listing fields. |

Free-text inference is downstream enrichment, not source support.

During a run, the orchestrator derives criterion diagnostics from these static declarations. Diagnostics are data on the source attempt record, not separate outcomes:

- `requested_criteria`;
- `native_criteria_applied`;
- `structured_evidence_available`;
- `unsupported_criteria`;
- `postprocess_criteria`.

Unsupported criteria must not make a source silently disappear unless the request explicitly asks for strict native-only behavior.

There are exactly three related concepts in the system:

| Concept | Scope | Owner | Allowed values |
| --- | --- | --- | --- |
| Source capability | Static source catalog | Scraper registry | `native_request`, `structured_output`, `unsupported` per criterion |
| Source attempt outcome | One source run for one query variant | Orchestrator | The outcome taxonomy below |
| Processing decision | One listing and one downstream rule | Post-processing pipeline | `kept`, `removed`, `unknown`, with a reason |

These concepts must not be collapsed into one enum.

## Raw Listing Schema

The raw search corpus is stored in the `raw_listings` table inside the run's
`run.sqlite` database. Each row keeps normalized columns for common access and
the immutable JSON record payload:

```json
{
  "schema_version": 1,
  "record_type": "raw_listing",
  "run_id": "r-...",
  "append_sequence": 0,
  "query_variant": "QA",
  "source": "hh_ru",
  "source_type": "aggregator",
  "collected_at": "2026-06-22T10:00:00Z",
  "listing": {
    "source_listing_id": "123",
    "title": "QA Engineer",
    "url": "https://example.com/jobs/123",
    "company": "Acme",
    "country": "RU",
    "city": "Москва",
    "location_text": "Москва или удаленно",
    "salary_text": "от 200000 RUB",
    "salary_min": 200000,
    "salary_max": null,
    "salary_currency": "RUB",
    "posted_at": "2026-06-20",
    "remote_in_country": true,
    "remote_global": null,
    "relocation": null,
    "native_grade": "middle",
    "description": "Full vacancy description when available",
    "requirements": "Requirements when separately available",
    "skills": ["Python", "API"],
    "raw_text": "Concatenated source text used for downstream text filters",
    "raw": {}
  },
  "evidence": {
    "description_availability": "present",
    "detail_fetched": true,
    "source_url": "https://example.com/search?q=QA"
  }
}
```

Required listing fields are `title`, `url`, `source`, and a stable source identity. Fields such as `description`, `requirements`, salary, remote, relocation, country, city, and posted date must always exist in the serialized schema but can be `null` when unavailable.

`description` is part of the raw schema because post-processing quality depends on it. A source may still return `description=null` only when it genuinely cannot expose full text within the source runtime policy. That absence must be visible through `evidence.description_availability`, for example `present`, `not_exposed`, `detail_timeout`, `detail_blocked`, or `not_requested`.

Vacancy URLs must be absolute and stripped of tracking parameters.

## Source Execution Contract

Each source runs independently. One source failure must not stop other sources.

For each selected source and each query variant, the orchestrator must:

1. Build the source-native request only from declared supported criteria.
2. Run the scraper within a source deadline.
3. Paginate until one of these boundaries is reached:
   - source-native end of results;
   - source-local `source_limit`;
   - run/source deadline;
   - enough post-filtered results were collected and the request allows early stop;
   - a classified terminal failure occurs.
4. Write valid raw listing records through the raw corpus writer.
5. Write a source attempt record even when no listings are found.

Zero listings is not an error by itself. It is a successful no-result outcome only when the source exposes explicit no-result evidence. A zero-card page without no-result evidence must fail closed as a classified blocked/parse outcome.

## Append Mode

The search tool must support appending to an existing run/corpus.

Append mode is used when the agent wants to add additional query variants, work-from geographies, vacancy geographies, cities, or sources after inspecting early results.

Append behavior:

- New raw records are appended to the same raw corpus.
- Records include `append_sequence` and `query_variant`.
- Existing raw records are not rewritten.
- Source attempt records are appended per source attempt.
- Downstream post-processing is re-run over the full corpus.
- Post-processing output is deterministic and idempotent for the same raw corpus and processing config.

Manual retry is not the same thing as append mode. Retry re-dispatches failed or partial source attempts. Append mode broadens the evidence corpus with additional search intent.

## Post-Processing Contract

Post-processing reads raw records and produces derived result views. It owns:

- dedupe;
- text exclusions;
- company exclusions;
- grade estimation when no native grade exists;
- salary parsing and lower-bound filtering when source-native filtering was unavailable;
- freshness filtering when source-native filtering was unavailable and `posted_at` exists;
- remote/relocation inference from description and structured fields;
- country/city filtering when source-native filtering was unavailable;
- ranking;
- final `max_results` slicing.

Post-processing must be idempotent:

- same raw corpus plus same processing config produces the same output;
- dedupe keys are stable;
- filter decisions are explainable;
- unknown values are handled explicitly, not silently converted to false.

For each listing removed or kept by a requested post-processing criterion, the derived output should preserve a machine-readable decision reason. The agent can then audit why a vacancy survived or disappeared.

## Enrichment Jobs

When a source does not natively support a requested criterion but exposes enough text or detail pages to infer it, the orchestrator must schedule post-processing or enrichment jobs.

Examples:

- source lacks relocation filter, but detail text may mention relocation;
- source lacks global remote filter, but description includes global remote policy;
- source lacks grade filter, but title/requirements include seniority signals;
- source search page lacks description, but detail page can be fetched.

Enrichment jobs must be source-aware and bounded by runtime policy. They must record whether they succeeded, timed out, were blocked, or were not available.

## Source Attempt Record

Each source attempt record must contain exactly one primary `outcome`:

- `success`: one or more valid raw listings collected and no stop/failure condition occurred.
- `no_results`: source explicitly reported zero results.
- `partial_success`: valid listings were collected, but source-owned pagination/detail/deadline limits prevented complete collection.
- `skipped_by_policy`: source was intentionally skipped by country, source selection, profile, or strict policy.
- `cancelled`: user or run lifecycle cancelled the attempt.
- `source_timeout`: source attempt deadline expired.
- `run_timeout`: whole run deadline cancelled unfinished sources.
- `blocked`: anti-bot, captcha, login wall, access denied, geo/VPN block, or anti-abuse redirect.
- `rate_limited`: rate limit response or retry-after policy.
- `http_client_error`: terminal 4xx not classified as blocked/rate-limited.
- `http_server_error`: terminal 5xx not classified as rate-limited.
- `network_error`: DNS, TLS, connection reset, socket timeout, or transport failure.
- `parse_error`: reachable response does not match parser contract and is not another classified state.
- `invalid_source_output`: scraper returned malformed normalized output, wrong type, missing required fields, or too many records.
- `resource_failure`: local runtime failure such as browser disconnect, poisoned context, pool failure, worker crash, or artifact writer failure.

Reaching `source_limit` is normal completion with `limit_reached=true`, not `partial_success`.

The source attempt record shape is:

```json
{
  "source": "hh_ru",
  "source_type": "aggregator",
  "query_variant": "QA",
  "attempt": 1,
  "outcome": "success",
  "started_at": "2026-06-22T10:00:00Z",
  "finished_at": "2026-06-22T10:00:12Z",
  "elapsed_ms": 12000,
  "source_limit": 100,
  "limit_reached": false,
  "counts": {
    "raw_listings_written": 34,
    "pages_visited": 3
  },
  "criteria": {
    "requested": ["query", "salary_from", "remote_mode"],
    "native_applied": ["query", "salary_from"],
    "structured_evidence_available": [],
    "unsupported": ["remote_mode"],
    "postprocess": ["remote_mode"]
  },
  "retry": {
    "attempts": 1,
    "max_attempts": 2,
    "next_action": "none"
  },
  "evidence": {
    "no_results": false,
    "block_signal": null,
    "error": null
  }
}
```

Only `outcome` is the source attempt terminal state. Fields under `criteria`, `retry`, and `evidence` explain that outcome; they are not additional states.

## Retry Contract

Retries are policy-driven and per-source.

Automatic retry is allowed only when:

- previous attempt produced no usable listings;
- outcome is transient;
- source has attempts remaining;
- retry backoff fits within the run budget.

Default retryable outcomes:

- `source_timeout`;
- `network_error`;
- retryable `rate_limited`;
- selected retryable `http_server_error`;
- selected local runtime acquire failures.

Do not automatically retry:

- `success`;
- `no_results`;
- `partial_success`;
- `skipped_by_policy`;
- `cancelled`;
- `blocked` without an explicit access-route retry policy;
- deterministic `parse_error`;
- `invalid_source_output`;
- permanent `http_client_error`.

Retries must preserve attempts, retries, final outcome, and final raw listing set.

## Test Contract

Each scraper must have tests at three levels:

- Request mapping: every declared native criterion changes the source-native URL, API payload, form state, or query shape.
- Parser fixtures: real captured source artifacts plus manually reviewed golden expected output.
- Source diagnostics: unsupported requested criteria are reported and do not fabricate or delete raw facts.

Every supported source must have a required parser fixture suite. No fixture suite means the source is not supported and must not appear in the supported source catalog.

The required fixture suite for a source includes:

- at least one successful non-empty search result capture;
- an explicit no-results capture when the source exposes a recognizable no-results state;
- pagination or cursor captures when the scraper implements pagination;
- detail-page captures when the scraper enriches descriptions or requirements from detail pages;
- real examples of optional fields that affect downstream processing when the source exposes them, such as salary, posted date, remote format, location, skills, requirements, description, and native grade;
- real captured block, rate-limit, login, geo, VPN, or malformed-source states only when those states have actually been observed for that source.

Parser fixtures are the parser regression contract. A parser fixture case must contain:

- a real captured source response: HTML, JSON, XML, SSR payload, API response, HAR, or browser-captured page artifact;
- fixture metadata with source id, captured URL, capture date, capture method, query, country/profile when relevant, and redactions performed;
- a manually reviewed golden answer that states which raw listings must be extracted and which source outcome is expected.

Generated, invented, model-written, or hand-authored source responses are not parser fixtures. They may be used only in generic orchestrator or fault-injection tests. A minimized fixture is allowed only when it is a redacted or shortened copy of a real capture and keeps the original DOM/API keys, nesting, URL shapes, and source text needed by the parser.

The golden answer must be written by manually inspecting the captured response. It must not be produced by the parser under test, by an LLM, or by downstream estimation code. Golden expected output asserts source facts only: title, URL, company, location, salary, posted date, description, requirements, skills, source-native grade, no-results evidence, and source-owned block/rate-limit/parse outcome when present. It must not contain inferred grade, ranking, dedupe result, remote/relocation guesses, or any other post-processing estimate.

Fake pages are acceptable for generic orchestrator and fault-injection tests, but they do not prove a real source parser.

Adding or changing a supported source therefore means doing the complete work: source-specific scraper, honest capability declaration, real fixture suite, manually reviewed golden answers, request-mapping tests, parser regression tests, source outcome tests, and orchestrator compatibility. Partial implementations belong in backlog or experimental code paths, not in the supported source catalog.

The orchestrator must have fake-source tests for:

- successful source;
- explicit no-results source;
- partial source;
- timeout;
- never-resolving source;
- sync and async exceptions;
- malformed normalized output;
- too many returned records;
- missing required fields;
- cancellation;
- run timeout;
- one source failing while others complete.

The writer must have tests proving append-only writes, line integrity, concurrency safety, atomic summaries, torn-line handling, and disk/runtime failure classification.

## Agent Workflow Contract

The intended agent workflow is:

1. Inspect source catalog.
2. Start a search over chosen sources and query variants.
3. Poll run and source outcomes until terminal or enough evidence is collected.
4. Retry transient failed sources when useful.
5. Append additional query variants or source groups when coverage is weak.
6. Run or request post-processing over the full raw corpus.
7. Inspect diagnostics and derived result slices.
8. Perform final manual analysis and present a coherent result to the user.

The final agent answer should be based on the post-processed corpus plus source diagnostics, not on a single truncated search response.
