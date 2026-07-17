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
- `compensation: CompensationCriterion | null`: optional hard minimum with
  mandatory `minimum`, ISO 4217 `currency`, and `period` (`hour`, `day`,
  `month`, or `year`), plus optional `gross`. `RUR` normalizes to `RUB`; no
  other currency is converted.
- `published_since: date | null`: optional lower date bound for freshness. The name should avoid the ambiguity of "не позже"; this means "vacancy was published on or after this date".
- `exclude_companies: list[str] | null`: optional case-insensitive company exclusion. This applies both to returned listings and, when possible, to source/company target selection.
- `exclude_text: list[TextExclusion] | null`: optional post-processing exclusions over vacancy text.
- `relocation: bool | null`: optional relocation support filter.
- `work_formats: list[WorkFormat] | null`: optional workplace format set. Valid values are `remote`, `hybrid`, and `office`.
- `remote_scopes: list[RemoteScope] | null`: optional remote eligibility scopes. Valid values are `global`, `country:<code>`, and `region:<code>`. This field requires `work_formats` to contain `remote`.
- `vacancy_geographies: list[VacancyGeography] | null`: optional vacancy location. Valid values are `country:<code>`, `region:<code>`, and `city:<name>`.
- `employer_geographies: list[EmployerGeography] | null`: optional employer location with the same country/region/city syntax; it requires profile evidence and is not inferred from vacancy location.
- `scenarios: list[SearchScenario] | null`: optional OR branches over relocation, workplace, remote scope, vacancy geography, and employer geography. Scenarios cannot be combined with the corresponding flat fields.
- `sources: list[str] | null`: optional exact source ids.
- `source_types: list[SourceType] | null`: optional broad selection using `aggregator` or `company_career`.
- `append_to_run_id: str | null`: optional existing run/corpus id for append mode.

`TextExclusion`:

- `pattern: str`
- `mode: "substring" | "regex"`
- `case_sensitive: bool`
- `fields: list["title" | "description" | "requirements" | "skills" | "raw_text"] | null`

## Workplace And Geography Filter Semantics

This section is the target behavior for interpreting workplace, remote-scope,
and vacancy-geography filters together. It is intentionally explicit because a
source can use the same words for different concepts: the country where the
employer is located, the country shown on a vacancy card, the country from which
remote work is allowed, and a broader remote region such as `region:EU` or
`global`.

Definitions:

- Work format means a normalized workplace format in `SearchRequest.work_formats`: `remote`, `hybrid`, or `office`.
- Remote scope means a normalized remote eligibility scope in `SearchRequest.remote_scopes`: `global`, `country:<code>`, or `region:<code>`. Physical formats must not be stored here.
- Vacancy geography means a normalized location scope in `SearchRequest.vacancy_geographies`: `country:<code>`, `region:<code>`, or `city:<name>`.
- Vacancy country means the normalized country or region scope derived from source evidence such as `listing.country`, `location_text`, source `regions`, source `remote_restrictions`, source `remote_type`, or city inference.
- Remote global means the vacancy can be performed remotely from any country unless the source exposes explicit exclusions. A plain `remote` / `удаленно` marker is not enough evidence for global remote; it becomes country-limited or `unknown` unless the source explicitly exposes `global`, `worldwide`, `anywhere`, `Весь мир`, or an equivalent structured scope.
- Hybrid and office are physical work formats, not remote scopes. Request them through `work_formats`; constrain their location through `vacancy_geographies`.
- When source evidence lists remote together with hybrid or onsite options, all supported formats are preserved. Remote-scope rules apply to the remote branch of the filter AST. A country or city without any remote/work-format evidence remains `work_format=unknown` and does not satisfy requested workplace filters by default.
- A `remote_scopes=["global"]` request is global-only. A `country:<code>` or `region:<code>` request uses scope intersection, so globally remote rows also satisfy it.
- Timezone ranges such as `remote from GMT-7 to GMT+4` are eligibility hints, not geography. They can support the `remote` work format but do not create a country or region scope. A remote vacancy with city-only evidence gets a country-limited remote scope inferred from the city, and multi-city locations may produce multiple country scopes.
- Unknown means the source did not provide enough structured or text evidence.
  It is an internal criterion state, never a public filter value, and must not be
  silently converted to `false`. The graph may schedule a declared provider for
  the missing fact. If providers are exhausted, the final row is rejected with
  `insufficient_evidence:<criterion>` and remains visible in filtered-out
  diagnostics.

Search request parameters compile into a filter AST. For example:

```json
{
  "all": [
    {"field": "work_format", "op": "any_of", "values": ["remote"]},
    {"field": "remote_scope", "op": "any_of", "values": ["global"]},
    {"field": "vacancy_geography", "op": "intersects", "values": ["country:RU"]}
  ]
}
```

The search request must not expose `remote_in_country` or `remote_global` as standalone booleans. They are source facts that normalize into `remote_scope`.

The search request also must not expose a broad "any remote" final filter. A source-native broad remote parameter such as `remote=true` can still be useful for collecting candidate rows, but final post-processing should express user intent through `work_formats` and `remote_scopes`.

`docs/workplace-geography-filtering.md` is the focused contract for workplace
and geography edge cases. The matrix below keeps the broader search-system
summary in sync with that file.

For this matrix, vacancy remote scope is the normalized post-processing interpretation of source evidence. It does not have to be one raw field. Examples are `global`, `country:RU`, `country:TR`, `region:EU`, and `unknown`. Physical formats such as `hybrid` and `office` live in `work_format`, not in `remote_scope`.

Rows assume that earlier filters such as query, title, grade, compensation,
publication date, excluded company, and excluded text did not already remove
the vacancy.

| Case | Request `work_formats` | Request `remote_scopes` | Request `vacancy_geographies` | Request `relocation` | Vacancy geography | Vacancy scope / work format | Vacancy relocation | Decision | Reason if removed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | empty | empty | empty | `null` | any | any | any | keep | n/a |
| 2 | `remote` | `global` | empty | `null` | any | `global`, `remote` | any | keep | n/a |
| 3 | `remote` | `global` | empty | `null` | any | `country:US`, `remote` | any | remove | `remote_scope_mismatch` |
| 4 | `remote` | `country:RU` | `country:RU` | `null` | `RU` | `global`, `remote` | any | keep | n/a |
| 5 | `remote` | `country:RU` | empty | `null` | `RU` | `country:RU`, `remote` | any | keep | n/a |
| 6 | `remote` | `country:RU` | empty | `null` | `TR` | `country:TR`, `remote` | any | remove | `remote_scope_mismatch` |
| 7 | `remote` | `region:EU` | empty | `null` | `PL` | `region:EU`, `remote` | any | keep | n/a |
| 8 | `remote` | `region:EU` | empty | `null` | `RU` | `country:RU`, `remote` | any | remove, because the project EU scope excludes `RU` | `remote_scope_mismatch` |
| 9 | `remote` | `global` | `region:EU` | `null` | `PL` | `global`, `remote` | any | keep | n/a |
| 10 | `remote` | `global` | `region:EU` | `null` | unknown | `global`, `remote` | any | remove | `insufficient_evidence:vacancy_geographies` |
| 11 | `hybrid` | empty | `country:GB` | `null` | `GB` | `hybrid` | any | keep | n/a |
| 12 | `hybrid` | empty | `country:GB` | `null` | `PL` | `hybrid` | any | remove | `vacancy_geography_mismatch` |
| 13 | `remote` | `global` | empty | `null` | `GB` | `hybrid` | any | remove | `work_format_mismatch` |
| 14 | `hybrid, office` | empty | empty | `null` | any | `remote`, `global` | any | remove | `work_format_mismatch` |
| 15 | `remote` | `global` | empty | `null` | any | unknown scope, `remote` | any | remove | `insufficient_evidence:remote_scopes` |
| 16 | `unknown` | empty | empty | `null` | any | no work-format evidence | any | invalid request | `unknown` is not a public filter value |
| 17 | `remote` | `global` | empty | `true` | any | `global`, `remote` | `false` | remove | `relocation_mismatch` |
| 18 | `remote` | `global` | empty | `false` | any | `global`, `remote` | `false` | keep | n/a |
| 19 | `remote` | `global` | empty | `false` | any | `global`, `remote` | unknown | remove | `insufficient_evidence:relocation` |

Region scopes must be explicit. `EU` means the current European Union country list. `europe` means the project-defined Europe scope and intentionally does not include `GB` or `RU`. The system must not infer region membership from time zones, source domain, language, or source popularity.

The implementation preserves enough evidence for the agent to audit each
decision. A filtered card distinguishes explicit mismatch from missing evidence;
a requested hard fact cannot remain unknown on a kept row.

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
| `native_request` | The source can enforce the criterion before returning listings. | Habr `qualification=middle`; source-native query text. |
| `structured_output` | The source cannot safely enforce the full criterion, but returns stable fields that downstream selection can use. | HH salary amount/currency/period; API field `workType=REMOTE`. |
| `unsupported` | The source cannot enforce or expose the criterion honestly. | Relocation is absent from search params and listing fields. |

Free-text inference is downstream enrichment, not source support.

Native source query narrows collection but does not prove final relevance. Final
post-processing still applies the query to the vacancy title for every source;
native query hits whose title does not match the requested variant are removed
with `query_mismatch`.

The executable single-vacancy policy entrypoint is
`job_harness.v2.postprocessing.filter_policy.decide_vacancy_filter`. It accepts
`VacancyFilterCriteria` and `VacancyFilterFacts` and returns a
`VacancyFilterDecision` without reading batch state.

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
| Criterion state | One listing and one hard criterion | Selection policy | `match`, `mismatch`, or `unknown`, with a reason |
| Selection outcome | One canonical fact set | Graph coordinator | `keep`, `reject`, or `needs_evidence` |

These concepts must not be collapsed into one enum.

## Listing Observation Schema

Search parsers write immutable `SearchListingOutput` payloads to
`listing_observations` in the run's `run.sqlite`. The observation is source
evidence only; request settings, ranking, selection decisions, page rank, and
queue state are stored elsewhere or derived later.

```json
{
  "source_id": "hh_ru",
  "target_provider_id": "hh_ru",
  "source_listing_id": "123",
  "title": "QA Engineer",
  "company": {
    "name": "Acme",
    "target_provider_id": "hh_ru",
    "source_company_id": "456",
    "profile_url": "https://hh.ru/employer/456",
    "official_site_url": null,
    "source_vacancies_url": null
  },
  "location": {
    "text": "Москва или удаленно",
    "cities": ["Москва"],
    "countries": ["RU"],
    "regions": []
  },
  "salary": {
    "salary_from": 200000,
    "salary_to": null,
    "currency": "RUB",
    "gross": true,
    "period": "month"
  },
  "work_formats": ["remote"],
  "remote_scopes": [{"kind": "country", "code": "RU"}],
  "native_grade": "middle",
  "posted_at": "2026-06-20",
  "vacancy_url": "https://hh.ru/vacancy/123",
  "apply_url": null,
  "summary": "Short text exposed by the search result"
}
```

Required listing fields are source/provider identity, title, and absolute vacancy
URL. Structured location, salary dimensions, workplace, native grade, posted
date, company reference, and summary may be absent only when the listing page
does not expose them.

Full description, requirements, responsibilities, skills, application channels,
and detail-only location/salary evidence belong to an independent
`VacancyDetailOutput`. Company profile and company site facts likewise come from
their own parser observations. The coordinator merges immutable observations
into a versioned fact set; a listing parser never performs those downstream
calls itself.

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

Append mode is used when the agent wants to add additional query variants, work formats, remote scopes, vacancy geographies, or sources after inspecting early results.

Append behavior:

- New raw records are appended to the same raw corpus.
- Records include `append_sequence` and `query_variant`.
- Existing raw records are not rewritten.
- Source attempt records are appended per source attempt.
- Downstream post-processing is re-run over the full corpus.
- Post-processing output is deterministic and idempotent for the same raw corpus and processing config.

Manual retry is not the same thing as append mode. Retry re-dispatches failed or partial source attempts. Append mode broadens the evidence corpus with additional search intent.

## Canonical Facts And Selection

Every listing/detail/profile/site observation can advance its own graph branch
without waiting for all listing pages or sources. `FactMaterializer` merges the
immutable observations for one listing and runs pinned, versioned fact derivers.
The canonical selection snapshot contains:

- structured location (`raw_text`, cities, countries, regions);
- workplace formats and remote eligibility scopes;
- title/source grade evidence, resolved grades, and conflict state;
- compensation minimum, maximum, currency, period, and gross/net;
- relocation support and visa sponsorship as separate facts;
- employer geographies.

The shared `RoleMatcher` requires ordered title tokens and a versioned alias
table. Hard selection and ranking consume the same role match, so description
tokens cannot promote a title mismatch.

Each requested hard criterion evaluates to `match`, `mismatch`, or `unknown`.
An explicit mismatch rejects immediately. Unknown produces `needs_evidence`
while a declared provider remains; after provider exhaustion it becomes final
reject with `insufficient_evidence:<criterion>`. A compensation minimum matches
only an explicit lower bound with equal currency and period (and equal gross/net
when requested). Maximum-only or dimensionally incomplete compensation is
unknown. No FX conversion or compensation-period inference is performed.

Dedupe and ranking operate over final canonical fact sets. The public projection
uses those same location, workplace, grade, compensation, and relocation facts,
but omits evidence references, raw parser payloads, request settings, and queue
state. Re-materializing the same observations with the same pinned derivers and
request is deterministic and idempotent.

## Enrichment Jobs

When a listing fact is unknown and the fact requirement plan declares an
autonomous provider, the coordinator schedules only that provider for that
listing.

Examples:

- source lacks relocation filter, but detail text may mention relocation;
- source lacks global remote filter, but description includes global remote policy;
- source listing lacks grade evidence, but a detail parser declares
  `native_grade` or detail text;
- source search page lacks description, but detail page can be fetched.

Each detail/profile/site parser accepts its own URL input and does not read graph
storage or schedule another parser. Enrichment invocations are independently
leased, retried, and classified. Their consumer edges record whether the fact
was satisfied, the successful output lacked it, the parser failed, or no trusted
URL/provider existed.

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
    "requested": ["query", "compensation", "work_formats", "remote_scopes"],
    "native_applied": ["query"],
    "structured_evidence_available": ["compensation", "work_formats", "remote_scopes"],
    "unsupported": [],
    "postprocess": ["compensation", "work_formats", "remote_scopes"]
  },
  "evidence": {
    "no_results": false,
    "block_signal": null,
    "error": null
  }
}
```

Only `outcome` is the source-plan terminal state. Fields under `criteria` and
`evidence` explain that outcome; they are not additional states. Request retry
state belongs to the internal page/URL invocation, not the source summary or
public search input.

## Retry Contract

Retries are policy-driven and apply to one concrete page or URL request.
`RequestRetryPolicy` is the only component that decides them. No source-wide,
parser-wide, or run-wide retry policy may restart already successful work.

Automatic retry is allowed only when:

- the current request is explicitly marked safe;
- the current request failed with a timeout, network error, or transient status
  `408`, `425`, `429`, `500`, `502`, `503`, or `504`;
- the request has attempts remaining;
- retry backoff plus the next attempt fits within the request budget.

The default policy uses at most three attempts, exponential backoff with full
jitter, honors `Retry-After`, and never repeats a page whose result transaction
committed successfully.

Managed retries move only the failed invocation to
`waiting_reason = retry_backoff`. Resource pacing uses the separate
`waiting_reason = resource_pacing` before a parser attempt starts. Neither wait
holds a task lease or resource slot.

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

An active invocation uses a 30-second lease with a batched 10-second heartbeat.
If the worker disappears before the atomic result commit, the attempt becomes
`worker_lost` and the same invocation may be reassigned. The old lease token can
no longer commit. Retries must preserve request attempts, decisions, delays,
final outcome, and the final raw listing set.

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
