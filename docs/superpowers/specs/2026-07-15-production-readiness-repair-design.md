# Production Readiness Repair Design

## Status And Authority

This document is the approved target design for the Job Harness v2 production
readiness repair. It consolidates the previously agreed scraper-contract,
selection, graph, retry, persistence, and verification decisions.

The page-local retry rules in
`2026-07-14-page-retry-and-fast-leases-design.md` remain normative where this
document does not restate them. If an older architecture document, diagram, or
implementation disagrees with this document, this document wins. The
implementation must update those artifacts in the same change set.

## Goal

Build a production-ready search and enrichment system in which:

1. every scraper is independently callable through a complete typed contract;
2. every page or target URL is independently retryable and durable;
3. listing sources progress independently and stream candidates downstream;
4. hard selection decisions use exact canonical facts with explicit evidence;
5. optional detail, profile, site, and career discovery cannot dominate search
   latency;
6. final output is globally assembled, immutable, repairable, and honest about
   coverage;
7. normal repository access is batch-shaped rather than query-per-listing; and
8. deterministic, live, fault-injection, and performance gates prove the result.

## Non-Goals

- Remote Actor execution is not required. Scraper independence is a contract
  property, not a deployment topology.
- The runtime does not search the web by bare company name to invent profile,
  official-site, or career-page URLs.
- The runtime does not perform implicit currency conversion.
- Compatibility shims for the current v2 contracts are forbidden. Contracts,
  callers, fixtures, tests, and documentation change together.
- Operational controls such as timeout, retry count, pacing, lease duration,
  queue priority, and request budget are not public search input.

## Execution Boundaries

The product has two independently completable managed graphs under one optional
workflow id.

### Search Execution

A search execution owns:

- source selection and listing-page collection;
- preliminary and final hard-criteria evaluation;
- only the detail/profile calls required to resolve requested hard criteria;
- run-global exact vacancy identity and non-destructive duplicate grouping;
- the immutable search result and filtered-out diagnostics.

Its global assembly barrier contains all selected listing branches and all
`required_for_selection` dependencies. It does not contain presentation-only
description, contacts, company-site scanning, or career discovery.

### Enrichment Execution

An enrichment execution owns a bounded shortlist selected by the workflow. The
built-in job-search workflow uses a final shortlist size of 50 and a separate
speculative streaming budget of 25. While search is running, it opportunistically
admits the highest-ranked candidates known at each admission point that already
match every hard criterion, with at most 10 speculative admissions from any one
source so a fast source cannot monopolize the shared budget. Work already started
is not revoked when a higher-ranked candidate arrives from a later page. After
search assembly, it admits every
not-yet-enriched member of the final top 50. Therefore one workflow enriches at
most 75 distinct vacancy identities even if early candidates are displaced.
These limits are workflow policy, not scraper input and not network settings.

The execution may start while its parent search is still collecting later
pages. A candidate from HH page 1 can therefore start vacancy-detail work while
HH page 2 remains queued. Candidate admission closes when the parent search
completes. The enrichment execution then globally assembles its own immutable
artifact after all admitted detail/profile/site tasks are terminal.

The parent search artifact is never mutated by later enrichment. The workflow
may present the enriched artifact when available and retain the search artifact
as exact provenance.

Career endpoints discovered by enrichment may create bounded listing source
plans in a child search execution. They never silently extend the already
completed parent search.

## Canonical Fact Model

Parser observations contain only explicit source evidence. Pure, versioned
derivers may normalize that evidence, but cannot manufacture eligibility.

### LocationFact

```text
LocationFact
  raw_text: string | null
  cities: ordered unique city codes/names
  countries: ordered unique ISO 3166-1 alpha-2 codes
  regions: ordered unique project region codes
  evidence: observation and field references
```

Mixed locations preserve every explicit component. `London | Vilnius` retains
both cities and both countries. A city criterion matches an explicit city, not
only its derived country.

### WorkplaceFact

```text
WorkplaceFact
  formats: subset of onsite, hybrid, remote
  remote_scopes: explicit country, region, or worldwide scopes
  evidence: observation and field references per format/scope
```

Physical office locations never widen remote eligibility. A statement such as
`London, Vilnius; Remote, Germany` yields physical London/Vilnius evidence and
remote scope `country:DE`, not remote scopes for GB and LT. Generic `remote`
without eligibility scope remains scope-unknown.

### GradeFact

```text
GradeFact
  title_evidence: explicit grades in the vacancy title
  source_evidence: explicit provider grade or experience bucket
  resolved: ordered set of grades or unknown
  conflict: boolean
  evidence: exact source fields
```

An explicit grade in the vacancy title outranks a coarse source bucket. If the
title says `Middle` and the source bucket says `Senior`, `resolved` is Middle
and `conflict` is true. Multi-grade titles preserve all explicit grades and
match when the requested set intersects them.

### CompensationFact

```text
CompensationFact
  minimum: integer | null
  maximum: integer | null
  currency: ISO 4217 | null
  period: hour, day, month, or year | null
  gross: boolean | null
  evidence: exact source fields
```

The search criterion is dimensioned as minimum, currency, and period. Those
three values are mandatory together. Gross/net is optional. A hard minimum
matches only when the explicit lower bound is at least the requested minimum
and currency/period are equal. Maximum-only and dimensionless values are
unknown, not matches. `RUR` is normalized to `RUB`; no other currency is
converted or guessed.

The direct request contract replaces scalar `salary_from` with
`compensation: CompensationCriterion | null`. The CLI exposes the business
dimensions `--salary-minimum`, `--salary-currency`, `--salary-period`, and
optional `--salary-gross`; supplying only part of the mandatory triple is an
input error.

### RelocationFact

Relocation assistance and visa sponsorship are separate facts with separate
evidence. Visa sponsorship alone never proves relocation support.

## Matching And Selection

One deterministic `RoleMatcher` produces a reusable `RoleMatch` consumed by
both filtering and ranking. Filter and ranker cannot implement different title
semantics.

The matcher normalizes case and punctuation, applies one versioned token-level
alias table, and requires query role tokens to occur in order with no more than
three intervening title tokens between adjacent query tokens. It does not use
unrestricted fuzzy substring similarity. Thus `Data Analyst`
matches `BI/Data analyst`, while `Java Engineer` does not match `QA Automation
Engineer (Java)` because the ordered role evidence is absent. Ranking may use
match strength, but a mismatch can never be promoted into the final set.

Every requested hard criterion evaluates to exactly one state:

- `match`: explicit canonical evidence satisfies it;
- `mismatch`: explicit canonical evidence contradicts it;
- `unknown`: the required evidence is absent or dimensionally incomparable.

Only candidates with `match` for every required branch are final keeps. An
unknown hard criterion is rejected as `insufficient_evidence:<criterion>` and
retained in filtered-out diagnostics. OR scenarios keep a candidate when one
complete branch matches; unresolved alternatives stop scheduling once another
branch proves the keep.

The public projection is built from the same canonical facts used by selection.
It includes structured location, workplace formats, exact remote scopes,
resolved grade/conflict, compensation dimensions, and relocation. Evidence
references remain in the internal fact set and audit view rather than cluttering
the default result. Public output excludes query input, page/rank, request
settings, raw intermediate arrays, queue state, and debug transport fields.

## Independent Scraper Contracts

The four parser types remain separate:

- `search_listing` accepts normalized business intent plus its own cursor;
- `vacancy_detail` accepts a vacancy URL and optional provider identity;
- `company_profile` accepts a company-profile URL and optional provider identity;
- `company_site` accepts a site URL.

Each bundle owns its manifest, typed input/output schemas, pure action builder,
parser implementation, provider ids, supported URL patterns, output facts, and
transport requirement. It reads no coordinator storage and schedules no other
parser.

`ParserRegistry` performs exact lookup by pinned parser id and version only.
`TargetParserResolver` is a separate planner component built from manifests. It
resolves `(parser_type, provider_hint, normalized_url)` before task creation:

1. provider-compatible, URL-matching specific parsers are considered first;
2. a manifest-declared fallback is considered only when no specific parser
   matches;
3. zero matches returns `unsupported_target`;
4. multiple matches returns `ambiguous_target`;
5. registration order is never a tie-breaker.

All production detail/profile parsers must declare routable provider and URL
capabilities. The graph never constructs `{source}.detail`,
`{source}.company-profile`, or any other parser id by naming convention.

`source_catalog.sql` remains the canonical advertised source inventory. Each
source row pins its listing parser id and implementation version. Source
selection uses that explicit reference and does not derive a source id by
removing a parser-name suffix.

Trusted URL flow is exact:

```text
listing/detail observation -> vacancy URL and optional profile URL
profile observation        -> verified official site URL
site observation           -> explicit career/ATS endpoint
target resolver            -> independently callable parser task
```

Missing or ambiguous URLs terminate explicitly. Bare-name lookup is not a
fallback.

## Streaming Graph And Scheduling

One `ParserInvocation` represents one logical page or target URL. Listing
continuations, vacancy details, profiles, sites, and discovered career pages are
separate invocations with deterministic task identities.

When a listing page commits, the same transaction stores its observations,
reserves valid continuations, and emits one page event. The coordinator consumes
a bounded batch, performs cheap preliminary rejection, and schedules the
minimum unresolved required provider chain. Other sources and page
continuations continue independently.

The planner consults declared parser output facts and source criterion
capabilities before scheduling. It schedules the lowest-cost viable provider,
re-evaluates after that provider settles, and only then considers the next
provider. If no trusted provider can produce a requested fact, the candidate is
rejected immediately as insufficient evidence without a futile network call.

Search scheduling has two work-conserving lanes:

- `listing`, weight 2;
- `required_enrichment`, weight 1.

Deficit round-robin uses those weights when both lanes are ready and immediately
uses idle capacity when only one lane has work. This allows page 2 and required
detail from page 1 to overlap without letting one class starve the other.

Optional enrichment runs in its child execution and cannot consume search
worker slots. Both executions still share deployment resource limits, so the
workflow starts no new optional request for a resource while a ready search
request for that same resource is awaiting admission.

The initial resource key is derived from the pure network action and persisted
in the internal invocation envelope. One workflow scheduler dispatches its
parent and child executions and checks ready parent work for that key before
admitting child work. Redirect targets are revalidated and re-admitted under
their actual resource key. This priority requires no public parser-input field.

Resource keys and policies are runtime-owned. They are derived from the pure
network action and normalized target host/platform, never accepted from public
search input. Shared ATS hosts share one resource policy. Policy values are
changed only from measured live evidence, not to hide a scheduling defect.

Coordinator batches are bounded by both event count and affected listing count:
at most 20 events and at most 250 affected listings per transaction. Remaining
work stays unprocessed for the next batch. Newly created runnable tasks become
leaseable immediately after that bounded commit.

## Page-Local Retry, Leases, And Resume

The request policy has one service-owned implementation:

```text
max_attempts = 3
attempt_timeout_seconds = 15
base_delay_seconds = 1
max_delay_seconds = 8
jitter = full
request_budget_seconds = 55
```

Timeouts, network errors, and HTTP 408, 425, 429, 500, 502, 503, and 504 are
retryable for safe actions. Backoff applies only to the failed page invocation.
A succeeded committed page is never fetched again.

Backoff and resource pacing store `available_at`, release the lease and resource
slot, and occupy no worker. An active lease lasts 30 seconds and is renewed in
one batch every 10 seconds. Lease expiry requeues only an invocation without a
terminal commit. A stale worker token cannot commit.

Scheduler wakeup is the minimum of:

- ready/waiting `available_at`;
- active invocation `lease_until`;
- coordinator lease expiry; and
- the next active-runtime deadline check.

The application exposes `resume_execution(execution_id)` and the CLI exposes
`job-harness-v2 resume --execution-id <id>`. Resume loads the persisted intent, pinned parser
versions, source plans, and runtime configuration version; it does not create a
new execution.

Execution time budget counts active scheduler time. Each scheduler heartbeat
persists accumulated active milliseconds. After a crash, time after the last
heartbeat and before resume is downtime and is not charged. A resumed execution
therefore does not fail merely because the process was stopped overnight.

## Monotonic Outcomes And Coverage

Invocation terminal outcomes are immutable. Source-plan status is derived from
all owned invocations and moves monotonically:

```text
planned -> running -> succeeded | no_results | limit_reached | partial | failed | cancelled
```

Rules:

- `limit_reached` means a configured collection/item/invocation budget ended an
  otherwise healthy branch;
- any unrecovered page failure forbids `succeeded`, `no_results`, and
  `limit_reached`;
- usable observations plus an unrecovered page failure produce `partial`;
- no usable observations plus an unrecovered page failure produce `failed`;
- a later successful sibling page cannot rewrite failure truth.

Execution lifecycle and execution quality are separate fields. Lifecycle is
`running`, `assembling`, `artifacts_pending`, `completed`, or `failed`. Quality
is `complete`, `degraded`, or `failed` and includes required enrichment as well
as listing source coverage.

VK pagination uses one ownership mode per response: either bounded parallel
fan-out or a single sequential continuation, never both. URLs are canonicalized
before task-key creation so scheme/query aliases cannot create overlapping page
chains.

## Identity And Deduplication

Normalized canonical vacancy URL is a run-global strong identity claim.
Provider plus source listing id is an alias claim, not a namespace that can
create a second resource for the same canonical URL.

Identity claims are acquired transactionally. Concurrent claims for one
canonical URL converge on one vacancy resource. All provider aliases,
observations, listing occurrences, and provenance remain attached. Probable
title/company similarity creates a non-destructive duplicate group only and
never shares facts or removes a result automatically.

Profiles and sites are deduplicated by strong company id/profile URL/verified
domain. One profile/site invocation can satisfy many shortlisted vacancies.

## Persistence Access Shape

Immutable observations remain row-per-observation because they are
non-recomputable evidence. Reads, task scheduling, dependency settlement, and
derived writes are batch-shaped.

Required rules:

1. No `SELECT` occurs inside a per-listing or per-consumer materialization loop.
2. Requirements, current fact snapshots, enrichment edges, provider consumers,
   identity claims, and existing tasks are loaded for the whole bounded batch.
3. New tasks, consumer edges, fact sets, derivations, and evaluations use
   set-based statements or `executemany` within one transaction.
4. A listing produces a new fact set/evaluation only when its materialized fact
   fingerprint changes.
5. Provider output settles every consumer in one batch update.
6. Ready-task leasing does not scan all historical invocations for every slot.
7. Indexes cover ready task priority, source-plan status, unprocessed events,
   enrichment invocation/status, latest fact set, and canonical identity claim.
8. SQLite write transactions never span network I/O.

The coordinator transaction cap of 20 events/250 listings bounds lock time.
Tests instrument SQLite statement counts: materializing 100 consumers may use
at most four more `SELECT` statements than materializing one consumer, excluding
deterministic parameter-limit chunks.

## Crash-Atomic Finalization

Final assembly transitions lifecycle as follows:

```text
running -> assembling -> artifacts_pending -> completed
```

`assembling` writes the immutable final-vacancy snapshot in SQLite.
`artifacts_pending` stores the expected artifact paths, schema versions, and
digests. Each artifact is written to a sibling temporary file, flushed with
`fsync`, atomically installed with `os.replace`, and followed by a parent
directory `fsync`. Only after every digest verifies does one transaction mark
the execution `completed`.

Resume repairs `assembling` or `artifacts_pending` executions from the SQLite
snapshot without re-fetching pages. A completed execution with a missing or
wrong-digest artifact fails verification and is repaired before being returned.

## Failure Handling

- Parser/schema/unsafe-target/unsupported-target failures are terminal and are
  never hidden as no-results.
- Retry exhaustion records the exact page and degrades source coverage.
- Unknown hard facts reject with an explicit insufficient-evidence reason.
- Required provider failure rejects affected candidates and degrades enrichment
  coverage; it does not rewrite a source listing failure.
- Optional enrichment failure affects only the child enrichment quality.
- Ambiguous parser routing and identity conflicts fail closed with persisted
  diagnostics.
- A global active-time deadline is a safety boundary, not workload control. If
  reached, remaining tasks are cancelled and quality is explicitly degraded.

## TDD And Verification

Every production change begins with a failing test through the narrowest real
production path. Reproduced user-visible defects also receive an application or
graph integration regression before implementation changes.

### Deterministic Contract Tests

- city preservation, including `London | Vilnius`;
- mixed physical and explicit remote locations without scope widening;
- relocation versus visa sponsorship;
- title-grade precedence and conflict evidence;
- dimensioned salary comparison and `RUR` normalization;
- one shared ordered/proximity role match for filter and ranker;
- unknown hard facts rejected as insufficient evidence;
- public projection equals the facts used by selection;
- manifest/provider/URL routing with unsupported and ambiguous outcomes;
- no parser-name convention in graph planning.

### Graph And Fault-Injection Tests

- page 1 can schedule required detail while page 2 remains queued;
- optional enrichment creates no search barrier or search worker task;
- each failed page retries independently with deterministic jitter;
- committed pages are never repeated;
- worker death before commit requeues only that invocation;
- wakeup includes lease expiry and resume continues the same execution;
- downtime is excluded from active runtime;
- mixed successful/failed sibling pages remain partial/failed, never healthy;
- VK page identities never overlap;
- concurrent canonical URL claims converge transactionally;
- crash after DB assembly and during each artifact write is repairable;
- concurrent processes respect shared resource limits;
- SQLite statement-count and transaction-size bounds hold.

### Source Fixtures

Every source advertised by `source_catalog.sql` must have source-specific,
non-empty success evidence. Each parser/platform family also has explicit empty,
blocked/rate-limited, malformed, and representative first/middle/last pagination
fixtures. A source cannot self-exempt because its fixture directory is absent.
Unsupported or unverified sources are removed from the advertised catalog.

### Release Gates

All of the following must pass:

1. `python3 scripts/verify_v2.py --skip-live`;
2. `python3 scripts/verify_v2.py --live-profile light`;
3. the full 149-source live profile three consecutive times;
4. the fixed ten-scenario realistic search suite;
5. focused live-page audits for city, grade, remote scope, salary, role, and VK;
6. crash/resume, artifact-repair, and concurrent-process fault suites;
7. repository secret scan with every fixture finding removed or explicitly
   classified by a committed allowlist rule.

No scenario may contain a known hard-filter false positive, misleading
`complete` coverage, or a final fact that disagrees with its sampled live page.

## Performance Invariants

Measured on the existing production-readiness scenario harness:

1. A healthy full-catalog search completes in at most 120 seconds on each of
   three consecutive runs.
2. The deterministic widespread-transient-failure profile settles in at most
   180 active seconds with explicit degraded coverage.
3. A base search with no criterion requiring detail performs zero detail,
   profile, site, or career-discovery requests.
4. After a listing page event commits, a required detail task becomes ready
   within 500 ms of coordinator CPU/DB time and starts within one configured
   resource interval plus 1 second when capacity is available.
5. The wide QA scenario never creates more than 75 presentation-enrichment
   vacancy identities in its child execution and emits at most 50 enriched
   shortlist items.
6. No retry backoff, resource pacing wait, or expired worker lease occupies an
   active worker slot.
7. A 250-listing coordinator batch holds the SQLite write transaction for less
   than 500 ms in the deterministic benchmark environment.
8. Materializing 100 shared consumers adds no more than four non-chunking
   `SELECT` statements compared with one consumer.

If a safe provider pacing policy makes an exhaustive request incompatible with
the latency SLO, the workflow narrows bounded enrichment work. It never raises
provider load or silently truncates listing coverage to manufacture a passing
benchmark.

## Delivery Order

Implementation is split into dependency-ordered slices:

1. regression harness and canonical fact contracts;
2. shared role/grade/location/workplace/compensation selection semantics;
3. self-describing parser routing and explicit catalog bindings;
4. search versus enrichment execution boundaries and priority scheduling;
5. monotonic outcomes, page retry, leases, wakeup, and resume;
6. global identity and batch-shaped repository operations;
7. crash-atomic finalization and repair;
8. VK pagination and remaining source-specific contract repairs;
9. fixture/catalog completeness and secret-scan cleanup;
10. deterministic, live, fault, and repeated performance release proof.

Each slice must leave deterministic gates green before the next slice begins.
Production-ready status is granted only after the complete release-gate set
passes; partial implementation remains internal alpha or controlled beta.
