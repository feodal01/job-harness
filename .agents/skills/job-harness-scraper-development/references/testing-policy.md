# Scraper Testing Policy

This policy defines how to test a scraping service with many independent job
sources. It is written for agents and maintainers who add sources, repair
parsers, or change the search engine.

The central rule: deterministic tests prove code correctness; live checks prove
live operational health. A live website can change HTML, rate-limit traffic,
serve anti-bot pages, vary by country, or return a different job inventory at
any time. Normal merge confidence must therefore come from recorded real inputs,
source contracts, normalized outputs, fault handling, and orchestration tests.

## Principles

1. Code correctness and live source health are separate questions.
   A live failure can mean parser drift, access blocking, rate limiting, network
   trouble, geo blocking, or a real empty result set.

2. Parser tests must exercise parser input, not mocked final output.
   A test that bypasses HTML/API/HAR/page parsing and returns already normalized
   jobs does not test the scraper.

3. Parser fixtures must be grounded in real source behavior.
   Source-specific parser fixtures come from captured real pages/API responses,
   HARs, or minimized copies of those captures. Screenshots are supporting
   evidence, not parser input. Agents must not invent a website response or
   materially edit a real response to manufacture a case.

4. Synthetic inputs are allowed only at generic layers.
   Fake sources, fake browsers, fake HTTP statuses, and malformed payloads are
   appropriate for orchestrator and fault-injection tests. They are not evidence
   that a particular real source parser works.

5. Every source outcome must be classified.
   Success, no results, partial results, timeout, blocking, rate limit, network
   failure, parse failure, skipped policy, and cancellation are distinct
   outcomes. Unsupported requested criteria are diagnostics, not terminal
   outcomes.

6. One bad source must not poison the run.
   A timeout, exception, browser crash, malformed response, or blocked page from
   one source must not prevent unrelated sources from completing.

7. Raw evidence and presentation output are separate artifacts.
   Raw source output is stored before global filtering, ranking, dedupe, grade
   estimation, or result caps. Presentation exports may be filtered or capped,
   but they are not the evidence layer.

8. Search criterion support is binary per source.
   A source either supports a criterion with native request parameters or
   structured source data, or it does not support that criterion. Free-text
   inference may happen downstream, but it must not be declared as source
   support.

## Gate Matrix

Rows marked "Blocks merge" are the deterministic merge gate. Rows marked "Does
not block merge" are operational gates unless a release policy explicitly makes
them blocking for a release.

| Gate | Type | Composition | Merge impact | Purpose |
| --- | --- | --- | --- | --- |
| G0 | Static quality | Ruff linting, import hygiene, formatting rules, mypy type checks, detect-secrets scanning | Blocks merge | Prevent low-level maintainability, type, and credential regressions. |
| G1 | Pure unit tests | URL builders, request mapping, selector helpers, normalization functions, salary/location/remote parsing, dedupe keys, ranking predicates | Blocks merge | Prove deterministic code paths without browser, network, filesystem state, or live source access. |
| G2 | Parser fixture tests | Real captured HTML, JSON, XML, JSON-LD, SSR payloads, HARs, redirects, or minimized real captures plus expected normalized raw listings and outcomes; screenshots are only supporting evidence | Blocks merge | Prove extraction and source-owned page classification from known real source shapes. |
| G3 | Source contract tests | Source id, source group, country scope, criterion support, source limits, required fields, output schema, unsupported-criteria diagnostics | Blocks merge | Ensure every source is honest about what it can collect and how it reports outcomes. |
| G4 | Orchestrator and isolation tests | Registered fake sources that return, hang, raise, exceed limits, or produce malformed normalized output | Blocks merge | Prove the orchestrator reacts correctly to source behavior: timeout enforcement, cancellation, retry policy, concurrency limits, isolation, and raw artifact writing. |
| G5 | Fault-injection tests | Fake transport/runtime symptoms: HTTP statuses, retry-after, redirects, final URLs, page titles/bodies, captcha iframes, network errors, malformed payloads, browser disconnects, resource failures | Blocks merge | Prove shared runners and detectors classify observable external symptoms deterministically. |
| G6 | Golden regression tests | Reviewed snapshots of normalized output for representative fixture suites, with volatile fields normalized away | Blocks merge only for approved baselines | Detect broad behavior changes during refactors; snapshot creation or update requires review. |
| G7 | Live smoke tests | Broad real-source queries with structural assertions, bounded timeouts, and classified outcomes | Does not block merge | Report whether sources appear reachable and broadly compatible now. |
| G8 | Drift monitoring | Scheduled live probes with status history, raw response samples, screenshots/HARs, selector counts, timings, and last-known-good comparison | Does not block merge | Detect site changes, anti-bot changes, access degradation, suspicious count drops, and parser drift. |
| G9 | Access-route checks | Source x country, source x proxy/provider, source x browser profile, source x request rate, headless/headful/stealth mode | Does not block merge | Validate operational access routes separately from parser correctness. |

## Canonical Outcome Taxonomy

All gates must use this taxonomy instead of inventing local lists. If a new
terminal outcome is needed, update this table first. Then add only the affected
coverage: G3 for contracts; G4 for orchestration; G5 for classifiers.

| Outcome | Meaning | Who must handle it | Blocking test coverage |
| --- | --- | --- | --- |
| `success` | Source completed and returned at least one valid normalized raw listing. | Source, orchestrator | G2 parser fixture; G3 source contract |
| `no_results` | Source completed with zero listings because the site/API exposed an explicit no-results state. | Source, orchestrator | G2 parser fixture; G3 source contract |
| `partial_success` | Source returned valid listings but stopped before the expected boundary due to bounded pagination, detail-fetch, or deadline failure. Reaching the configured source limit is not partial. | Source, orchestrator | G2 for source-owned partial states; G4 for orchestration |
| `skipped_by_policy` | Source intentionally did not run because of country, profile, source selection, or other explicit policy. | Orchestrator | G4 |
| `cancelled` | User cancellation or run-level cancellation stopped the source. | Orchestrator | G4 |
| `source_timeout` | One source exceeded its local deadline. | Orchestrator | G4 source behavior; G5 timeout boundary |
| `run_timeout` | Overall run deadline cancelled unfinished sources. | Orchestrator | G4 |
| `blocked` | Site served anti-bot, captcha, login wall, access denied, geo/VPN/proxy block, or anti-abuse redirect. | Shared access detector, source-owned detector when present, orchestrator | G5 for generic detector behavior; G4 for source wiring; G2 for captured source-specific block shapes |
| `rate_limited` | Site returned a rate-limit response such as HTTP 429, retry-after, or a source-specific rate-limit payload. | HTTP layer, source-owned detector when present, orchestrator | G5 for generic status handling; G2 for captured source-specific payloads |
| `http_client_error` | Terminal HTTP 4xx that is not classified as blocked or rate-limited. | Source or HTTP layer, orchestrator | G5 |
| `http_server_error` | Terminal HTTP 5xx that is not classified as rate-limited. | Source or HTTP layer, orchestrator | G5 |
| `network_error` | DNS, TLS, connection reset, socket timeout, or transport-level failure. | HTTP/browser layer, orchestrator | G5 |
| `parse_error` | A reachable response does not satisfy the parser contract and is not recognized as another canonical outcome. | Source, orchestrator | G2 for real drift captures; G5 for generic malformed inputs |
| `invalid_source_output` | A source returns malformed normalized output, too many records, missing required fields, or the wrong return type. | Orchestrator | G4 source behavior; G5 validation boundary |
| `resource_failure` | Browser disconnect, poisoned browser context, pool acquire timeout, worker crash, disk/artifact write failure, or other local runtime resource failure. | Orchestrator/runtime | G5 runtime boundary; G4 only if source isolation is affected |

`blocked` covers anti-bot, captcha, login wall, access denied, geo block,
VPN/proxy warning redirects, and anti-abuse redirects as subreasons. Known block
signals include status codes, final URLs, page titles, body markers, iframe
markers, and redirects such as `vpncheck`/`vpncheeck`.

`success` and `no_results` are mutually exclusive. A zero-listing source with
explicit no-result evidence is `no_results`, not `success`.

Unsupported requested criteria are recorded as diagnostics, for example in
source summaries or status annotations. They do not by themselves change the
terminal outcome, fabricate facts, delete raw listings, or prevent a source from
running. Use `skipped_by_policy` only when an explicit policy or strict mode
forbids running the source.

## Composite Outcome Precedence

Gate map: G2 covers real composite artifacts. G4 covers source behavior. G5
covers transport and runtime symptoms.

Each source has one primary outcome. Usable listings collected before a terminal
problem are preserved as raw evidence, but they do not turn the primary outcome
into `partial_success`.

Precedence rules:

- `success` applies only when the source returns at least one valid listing and
  no stop/failure condition occurred.
- `no_results` applies only when the source returns zero listings with explicit
  no-result evidence and no stop/failure condition occurred.
- `partial_success` applies only when the source returns valid listings and
  stops by a bounded source-owned condition, such as slow pagination or skipped
  detail enrichment, without block, rate-limit, timeout, parse, network, or
  runtime failure.
- Reaching the configured source-level raw collection limit is normal completion,
  not `partial_success`. Record `success` with `limit_reached=true` when at
  least one valid listing was collected.
- `blocked`, `rate_limited`, `source_timeout`, `network_error`, `parse_error`,
  `invalid_source_output`, and `resource_failure` remain the primary outcome
  when they occur after partial listings. Preserve the listings and record
  `listings_written`.
- `skipped_by_policy`, `cancelled`, and `run_timeout` describe policy or
  run-control decisions. They are primary for sources that did not complete
  normally.

## Access And Block Classification Rule

Gate map: G2 is for real source artifacts. G4 is for source wiring through the
engine. G5 is for shared detector and runtime symptoms.

The engine must safely record every canonical outcome for every source.
Source-owned tests cover only outcomes the source can observe or classify.
Coverage is split by ownership:

- Shared access detectors are responsible for generic status, redirect, title,
  body, iframe, network, rate-limit, and browser-resource classification. G5
  covers classifier-owned access and transport outcomes with synthetic inputs
  because these are engine and transport contracts.
- Every source must have wiring coverage proving that its transport path goes
  through the shared detector or runner. Browser sources must not bypass the
  browser block probe; HTTP/API sources must not bypass the HTTP helper or
  equivalent error mapper. G4 proves engine wiring; G5 proves detector behavior.
- Source-owned block detection requires real source evidence. If a scraper has
  custom logic for a source-specific VPN redirect, captcha variant, login wall,
  geo page, or rate-limit body, the test must use a captured artifact or a
  minimized capture from that source. This is G2 coverage.
- If a real source serves an unknown page that is neither a valid result page nor
  a documented no-results page, the parser must fail closed: classify it as
  `blocked` when a known access signal is present, otherwise `parse_error`. It
  must not return `success` or `no_results` from "zero cards" alone.

Agents must not invent source-specific access pages. A fake captcha or fake VPN
page proves only the shared detector rule in G5; it does not prove that a real
source parser handles that site's actual access page.

## Source Contract Gate

Gate map: G3 is primary. G1 covers request builders. G2 covers real source
fixtures.

Each source must declare:

- stable source id;
- source group or category;
- supported countries or region behavior;
- transport type: HTTP/API, browser, hybrid, directory, or batch probe;
- source-level raw collection limit;
- supported search criteria;
- unsupported search criteria.

Search criteria are binary at the source level:

- `supported`: the source can enforce or expose the criterion through native
  request parameters, API filters, structured response fields, or stable DOM
  markers.
- `unsupported`: the source cannot enforce or expose the criterion from source
  data. Downstream text inference may still annotate or filter presentation
  results, but the source remains unsupported for that criterion.

Source contract tests must prove:

- every supported native request criterion changes the outgoing request shape;
- every supported structured criterion is present in real parser fixtures;
- unsupported requested criteria are reported in source summary diagnostics;
- unsupported criteria do not cause the source to fabricate or delete raw facts;
- source limits are enforced at raw collection boundaries.

## Request Mapping Gate

Gate map: G1 is primary. G3 validates that request mapping matches declared
source support.

Request mapping tests are not a separate policy layer; they are the unit-test
part of source contracts. They cover how a normalized user search becomes a
source-native URL, API payload, form state, or structured query.

For each supported criterion, test the smallest observable native change:

- query text changes the search term sent to the source;
- country or location changes the source-native region parameter when supported;
- remote/hybrid/onsite changes the native work-format parameter when supported;
- salary changes the native salary parameter when supported;
- freshness changes the native date/period parameter when supported;
- multi-value criteria either map to a real native multi-value form or are
  marked unsupported;
- emitted vacancy URLs are absolute and stripped of tracking parameters.

If a real source does not support one of these criteria, do not write a request
mapping test that pretends it does. Assert the unsupported-criteria diagnostic
instead.

## Parser Fixture Gate

Gate map: G2 is primary. G3 checks contract consistency. G6 may snapshot these
fixtures.

Parser fixtures must be based on real source artifacts:

- captured HTML page;
- captured API/JSON/XML response;
- captured SSR payload such as JSON-LD or framework boot data;
- captured HAR for browser/network flows;
- screenshot only as supporting evidence, not parser input;
- minimized real capture that preserves the structure and fields under test.

Allowed fixture edits:

- redact secrets, cookies, personal data, tracking ids, and irrelevant long text;
- shorten repeated records while preserving the original DOM/API shape;
- replace private company/person names only when the replacement cannot change
  parser behavior;
- remove unrelated scripts/styles/assets;
- normalize timestamps only when they are not the behavior under test.

Forbidden fixture edits:

- inventing a source response from memory;
- deleting salary, company, location, or remote fields from a real vacancy to
  manufacture a missing-field case;
- adding a captcha, login wall, geo block, or rate-limit body that has not been
  observed for that source;
- changing class names, data attributes, JSON keys, URL shapes, or nesting to
  fit the parser;
- creating a fixture solely to satisfy a checklist item when that state is not
  known to occur on the source.

For each source, maintain real fixtures for the states that the source actually
exposes:

- normal non-empty result;
- explicit no-result state, if the source has a recognizable one;
- pagination or batching, if implemented;
- optional fields that naturally vary on the source, such as vacancies with and
  without salary, location, company, remote marker, skills, or posted date;
- detail page enrichment, if implemented;
- duplicate cards or repeated pages, if observed or produced by the source;
- blocked, rate-limited, login, geo, VPN/proxy, captcha, and anti-abuse states
  when they have been captured or otherwise verified for that source.

If an access state has not been observed for that source, do not invent a G2
fixture for it. G5 still tests the generic detector behavior. G4 still tests
that the source is wired through the engine path that uses that detector. When
live smoke or drift monitoring later captures the state, add the real artifact
as a G2 fixture before changing source-specific parser or detector logic.

If a developer wants to handle a block pattern before it has been observed on
the source, implement it as a shared detector rule and test it in G5. Do not add
source-specific parser code backed only by a fabricated page.

## Repeatable ATS Fixture Policy

Gate map: G2 proves the shared ATS parser shape. G3 proves each configured
company source is wired to the right ATS request and declares honest
capabilities.

For company career pages backed by a repeatable ATS, maintain the parser under a
platform-level implementation and keep each company as data. A new company on an
already supported ATS must still be proven from a real source artifact unless
the contract model explicitly supports platform-level fixture suites. In the
current v2 contract that means one source-specific non-empty success fixture per
configured company.

Once an ATS parser has fixtures from multiple independent companies, additional
testing can be narrower:

- add the company config and catalog row;
- assert that request mapping fetches the real captured board/API URL;
- keep one real non-empty success artifact for the company;
- rely on the shared ATS parser's multi-company fixture suite for deep parser
  behavior such as optional fields, pagination, detail enrichment, and
  work-format edge cases;
- add source-specific extra fixtures only when that company exposes a new ATS
  variation, a new source-owned state, or company-specific config behavior.

Custom company pages do not get this reduction. They need source-specific
fixtures for every parser-owned state because there is no shared ATS contract to
carry parser confidence.

## Fixture Layout And Assertions

Gate map: G2 is primary. G6 may snapshot these fixtures.

Recommended layout:

```text
tests/fixtures/scrapers/<source>/<case>/
  input.json
  response.html
  response.json
  network.har
  screenshot.png
  expected.raw.json
  expected.status.json
  meta.json
```

Use only the files needed for the case.

`input.json` describes the search intent. `response.*` contains the captured
page/API payload. `expected.raw.json` asserts normalized raw listing fields.
`expected.status.json` asserts the canonical outcome. `meta.json` records source,
captured URL, capture date, capture method, country/profile when relevant,
redactions/minimizations performed, and why the fixture exists.

Good assertions:

- required fields are present;
- URLs are absolute and canonicalized;
- source id is stable;
- supported fields are normalized from actual source data;
- unsupported requested criteria are visible in source diagnostics;
- duplicate input cards do not produce duplicate raw listings;
- blocked pages do not become no-result pages;
- no-result pages do not become parse errors.

Bad assertions:

- exact whitespace from raw HTML;
- full-page snapshots where field-level assertions are enough;
- live result count from a public site;
- parser internals that can change without changing behavior;
- assertions against fabricated fields not present in the source artifact.

## Orchestrator And Fault Gates

Gate map: G4 is fake source behavior. G5 is fake transport or runtime symptoms.

Both gates must induce outcomes through the same signals production code sees.
They must not set final source statuses by hand, except in tests whose explicit
subject is status serialization.

G4 uses registered fake sources to prove source isolation and run orchestration.
The fake source stands in for a scraper selected by the engine. The orchestrator
learns what happened from the fake source's observable behavior: it returned
listings, returned an explicit no-results outcome, returned partial data, raised
an exception, never completed, exceeded a deadline, or produced invalid
normalized output.

G5 uses fake transport and runtime boundaries to prove canonical classification.
The runner or detector learns what happened from the same low-level symptoms it
would see in production: HTTP status, `Retry-After`, redirect target, final URL,
page title, page body marker, captcha iframe, malformed JSON, network exception,
browser disconnect, pool acquire timeout, or artifact writer failure.

| Gate | Test double | Injected signal | Production code under test | Example |
| --- | --- | --- | --- | --- |
| G4 | Fake registered source | `search()` sleeps past local deadline | Orchestrator deadline wrapper | Engine records `source_timeout` and other sources still finish. |
| G4 | Fake registered source | Source raises parser exception | Source runner and orchestrator isolation | Failed source is classified and unrelated source results are preserved. |
| G4 | Fake registered source | Source returns missing required fields or too many records | Output validation and source limits | Engine records `invalid_source_output` or enforces source limit. |
| G5 | Fake browser pool/page | Blocked wrapper, partial wrapper, disconnect, poisoned context, or acquire timeout | Browser dispatch path and runtime classifier | Partial listings are written when available and status is classified. |
| G5 | Fake HTTP response/helper | HTTP 429 with `Retry-After` | HTTP helper and runner classifier | Runner records `rate_limited`; retry decision follows retry policy. |
| G5 | Fake browser page | Final URL path is `/vpncheck` or `/vpncheeck` | Shared browser block detector | Detector returns `blocked` without the scraper parsing zero cards. |
| G5 | Fake browser page | Title/body/captcha iframe contains a known block marker | Shared browser block detector | Detector returns `blocked` or captcha-specific failure mode. |
| G5 | Fake network boundary | DNS, TLS, socket, or malformed JSON failure | HTTP/browser transport classifier | Runner records `network_error` or `parse_error`. |
| G5 | Fake local runtime | Browser disconnect, pool acquire timeout, disk full | Runtime cleanup and artifact handling | Run remains bounded and later sources are isolated. |

The distinction is ownership. G4 asks, "If a source behaves this way, does the
engine remain correct?" G5 asks, "If the network, browser, or runtime exposes
this symptom, does the shared classifier turn it into the right canonical
outcome?" G2 asks, "Does this specific real source artifact parse or classify
correctly?"

G4 must cover:

- fast successful source;
- explicit no-result source;
- partial-success source;
- source that sleeps near the local timeout;
- source that never resolves;
- source that raises synchronously;
- source that raises asynchronously;
- source that returns malformed normalized output;
- source that returns too many records;
- source that returns records missing required fields;
- cancellation while sources are active;
- global run timeout with unfinished sources.

G5 must cover every transport/runtime/classifier-owned non-success outcome
assigned to G5 in the Canonical Outcome Taxonomy. It does not own source
contracts, policy skips, or user cancellation. Do not maintain a separate
fault-outcome list in tests or documentation.

Each G4 or G5 test must assert:

- canonical outcome;
- user-visible error/summary shape;
- retry decision when applicable;
- raw artifacts written or intentionally absent;
- cleanup and isolation behavior for later sources.

## Browser And API Recording Policy

Gate map: G2 is for real parser fixtures. G5 is for synthetic replay and fault
signals.

For browser-heavy sources, prefer HAR replay or a fake browser page over live
navigation in merge-blocking tests. HAR artifacts must be committed with the
test, scoped to relevant endpoints when possible, and scrubbed of cookies,
tokens, personal data, and unnecessary payload.

For HTTP/API sources, use recorded cassettes or explicit mocked responses when
the payload is too large for inline fixtures. Recorded cassettes must reject
unexpected new network requests during deterministic tests.

Do not record secrets, authenticated user data, private cookies, or personal
contact/payment data in fixtures, HARs, screenshots, or cassettes.

## Retry Policy

Gate map: G4 covers retry after source outcomes. G5 covers retry after transient
transport or runtime symptoms.

Retry policy must be explicit and tested. A retry is allowed only when all of
the following are true:

- the previous attempt produced no usable listings;
- the outcome is in the configured transient retry set;
- the source has remaining attempts;
- retry backoff fits inside the remaining run budget.

The default transient retry set is:

- `source_timeout`;
- `network_error`;
- `rate_limited` when retry budget and server timing allow it;
- selected `http_server_error` cases such as retryable 5xx or retry-after;
- runtime acquire failures such as browser pool acquire timeout.

Retry must not apply to:

- `success`;
- `no_results`;
- `partial_success`;
- `skipped_by_policy`;
- `cancelled`;
- `blocked`, unless a separate access-route retry strategy is explicitly
  implemented and tested;
- `parse_error` from deterministic source fixtures;
- `invalid_source_output`;
- permanent `http_client_error`.

Manual re-run, proxy rotation, or access-route retry is a separate operational
policy. It must not be hidden inside normal source-level retry without explicit
tests.

Every retry test must preserve attempts, retries, final outcome, and final raw
listing set.

## Golden Regression Policy

Gate: G6. Input fixtures come from G2.

Golden tests are useful for agent-written or legacy code where behavior is broad
and refactors are risky. They are not a substitute for curated expected fields.

Golden tests must:

- run only on deterministic real-source fixtures;
- compare normalized output after removing volatile fields such as timestamps,
  run ids, local paths, request timings, and unordered diagnostic metadata;
- fail on meaningful changes in extracted facts, source outcomes, or summary
  semantics;
- require reviewer approval when snapshots are updated.

If existing output is known to be wrong, do not bless it as a golden truth.
Create a failing real-source fixture with intended expected output, fix the
parser, then update the golden file.

## Live Smoke Policy

Gate: G7.

Live smoke tests are operational probes. They run broad, stable queries against
real sources with strict time budgets. They assert only structural facts:

- the run exits cleanly;
- output is valid structured data;
- the selected source reports a canonical outcome;
- success contains required normalized fields;
- `no_results` and failure states are classified through the canonical taxonomy;
- source summaries include elapsed time and collection limits.

Live smoke tests must not assert exact vacancy title, exact company, exact
salary, exact number of live results, or exact ordering of live results.

A live smoke failure creates a health event or ticket with evidence. It must not
cause broad allowlists in merge gates.

## Drift Monitoring Policy

Gate: G8. Feeds new G2 fixtures.

Drift monitoring compares live source behavior over time. It tracks:

- canonical outcome distribution by source;
- raw listing count for broad queries;
- required-field completeness;
- selector match counts;
- final URL and response status;
- block markers;
- median and tail latency;
- pagination depth;
- detail-page success rate;
- request-shape or structured-payload changes.

When drift is detected, preserve evidence:

- raw response or minimized real fixture;
- screenshot for browser flows;
- HAR when network replay is useful;
- extracted raw listings;
- source configuration;
- country/proxy/browser profile;
- first failing time and last known good time;
- suspected selector/API contract that changed.

The fix workflow is:

1. Capture or minimize the failing live artifact.
2. Add a G2 fixture or G5 fault test that reproduces the failure.
3. Fix the parser, source contract, or access handling.
4. Run all blocking gates.
5. Re-run live smoke only to confirm operational recovery.

## Access Route Policy

Gate: G9.

Anti-bot, geo-blocking, proxy reputation, and browser fingerprint issues are
operational access concerns unless a real G2 fixture proves parser logic is
wrong.

Access checks should be modeled as a matrix:

- source x country;
- source x proxy/provider;
- source x browser profile;
- source x headless/headful/stealth mode;
- source x request rate;
- source x time of day when a source is known to vary.

These checks report access health and recommended routing. They do not redefine
parser correctness.

## Data Quality Policy

Gate map: G2 validates real extraction. G3 validates declared fields. G6 guards
approved broad snapshots.

Parser tests must protect downstream data quality. For each source, assert only
fields the source actually exposes in real artifacts:

- title;
- canonical URL;
- company;
- country and location;
- salary text or structured salary;
- remote/hybrid/onsite;
- experience or grade when structurally supported by the source;
- description and requirements when detail parsing is supported;
- skills/tags;
- posted date or freshness;
- source-native id when available.

Do not create missing-field cases by deleting real data from a captured listing.
Use a real vacancy that naturally lacks that field, or omit that case until such
a vacancy is observed.

## Anti-Patterns

Do not:

- make live source success a normal merge requirement;
- treat "returned an empty list" as proof of success without explicit no-result
  evidence;
- patch a verification script to allow a source failure that production code
  cannot classify;
- use one generic exception bucket for canonical outcomes;
- mock final normalized listings instead of exercising parser input;
- update golden snapshots without reviewing the behavior change;
- hide unsupported request criteria;
- let one source's browser/session failure break unrelated sources;
- ship a scraper that only works for the one query used during manual testing;
- invent parser fixtures or materially alter real fixtures to satisfy tests;
- record secrets or private user data in fixtures.

## Agent Review Checklist

Before handing off a scraper change, an agent must confirm:

- G0 static quality passes: Ruff, mypy, secret scan.
- G1 unit tests cover request mapping and pure parsing helpers.
- G2 parser fixtures are real captured artifacts or minimized real captures.
- G3 source contract declares only supported or unsupported criteria.
- G4 orchestrator tests prove isolation, deadlines, cancellation, and raw
  artifact behavior.
- G5 fault tests cover every transport/runtime outcome touched by the change.
- Access/block handling is split correctly: G5 covers detector behavior; G4
  covers source wiring; G2 covers real source-specific block artifacts.
- G6 golden updates, if any, were reviewed as behavior changes.
- G7 live smoke, if run, was interpreted as health signal, not correctness
  proof.
- G8 drift evidence was converted into a deterministic fixture before parser
  changes.
- G9 access-route issues were not treated as parser correctness failures.
