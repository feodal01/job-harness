# V2 To-Be Independent Scraper Architecture

Status: implemented contract for the current HTTP source catalog. Browser
session execution remains an explicit extension point; no current v2 source
bundle declares browser transport.

Scraper independence is defined by the contract boundary, not by remote
execution. Every current scraper runs in-process but remains independently
callable because it receives complete typed input, uses only `ParserRuntime`,
returns its own typed facts, and never reads coordinator storage.

The executable diagrams are:

- [event and task graph](./v2-to-be-scraper-contract-event-graph.svg);
- [database schema](./v2-to-be-scraper-contract-db-schema.svg).

## Architectural Invariants

1. A scraper receives a versioned typed input and returns a versioned typed
   result. It never reads coordinator storage.
2. A scraper does not create workflow tasks, choose another scraper, derive
   inferred facts, filter candidates, resolve entities, or write storage.
3. Direct execution returns the scraper result without creating a run, task, or
   database row. Managed execution wraps the same call in the durable graph.
4. One `ParserInvocation` is one independently retryable page or URL call.
   A successful result and all observations are committed atomically, so a
   committed page is never fetched again.
5. Listing, vacancy-detail, company-profile, and company-site scrapers have
   separate result types. Illegal cross-type states cannot be represented.
6. A listing scraper bundle includes its manifest, schemas, pure initial-input
   planner, and parser implementation. No matching source-specific adapter is
   registered elsewhere.
7. A `stateless_unit` listing invocation emits at most one collection unit and
   may return zero or more self-contained continuations. A `session_batch`
   invocation may emit several pre-reserved units before its browser session
   closes.
8. Downstream work is driven by missing facts and declared fact/derivation
   providers, not by a fixed listing-detail-profile-site stage sequence.
9. Stored parser emissions are immutable observations. Versioned pure
   `FactDeriver` components produce separate workflow-derived facts with exact
   observation provenance.
10. A detail/profile/site invocation can run and be persisted without a previous
    listing invocation.
11. Shared enrichment tasks have many-to-many consumers. A single company
    profile task may satisfy multiple listings.
12. Profile/site work requires a trusted URL from parser facts, the source
    catalog, or a previously verified redirect. Bare company-name web search is
    not part of the first implementation.
13. Cross-source vacancy resources merge only from strong identity evidence.
    Heuristic duplicate candidates never share scraper facts or disappear from
    output automatically.
14. Resource concurrency and pacing are deployment-scoped across concurrent CLI
    processes through a shared limiter backend.
15. Source branches progress independently. Final assembly is the only global
    execution barrier.
16. Search intent contains business criteria only. Delay, timeout, retries,
    concurrency, queue priority, and request budgets are service configuration.

## Component Model

| Component | Owns | Must not own |
| --- | --- | --- |
| CLI/Application | User intent, run selection, execution creation | Source-native parameters, pacing |
| `DirectScraperExecutor` | Validate and execute one scraper call without graph persistence | Search workflow and durable tasks |
| `SourceSelector` | Initial source instances selected from intent and catalog | Parser input construction |
| `SearchScraperBundle.planInitial` | Pure mapping from normalized intent to initial typed inputs | Network calls, task persistence |
| `TargetParserResolver` | Deterministic target URL/provider to scraper resolution while planning | Runtime implementation lookup |
| `EmployerTargetResolver` | Resolve trusted stored company URLs into profile/site tasks | Bare-name web search, scraping |
| `ParserRegistry` | Exact lookup by pinned parser id and version | URL routing and business policy |
| `GraphCoordinator` | Source-plan lifecycle, event handling, dependencies, task derivation, drain | HTTP/browser implementation |
| `TaskRunner` | Lease, preflight admission, exact parser lookup, invoke, acknowledge | Search/enrichment policy or retry classification |
| `RequestRetryPolicy` | Decide retryability, request budget, exponential backoff, jitter, and `Retry-After` | Task selection or source workflow |
| `ResourceGate` | Deployment-scoped concurrency and start pacing | Retry decisions, parser selection, public output |
| Scraper | Source-specific fetching and fact extraction | Storage, workflow, cross-parser calls |
| `FactDeriver` | Pure versioned derivation from exact stored facts | Network access and source parsing |
| Selectors | Evaluate explicit fact sets against criteria | Fetching and parsing |
| `DuplicateResolver` | Strong identity linking and non-destructive probable grouping | Parser execution |
| Final assembler | Execution-scoped dedupe, ranking, projection | Source parsing |

`TargetParserResolver` is used before task creation. Once an invocation stores
`parserId` and `parserVersion`, `TaskRunner` performs an exact
`ParserRegistry` lookup and must not route the URL again.

## Direct and Managed Execution

The architecture exposes two explicit execution surfaces:

```text
DirectScraperExecutor
  execute(parserRef, typedInput) -> typedResult

V2SearchApplication / GraphSearchPipeline
  execute(searchRequest) -> GraphSearchPipelineExecution
```

Direct execution validates the same schemas and uses the same `ParserRuntime`
and deployment-scoped `ResourceGate`, but creates no run, invocation, event,
observation, or final snapshot. The caller owns any returned listing
continuations.

Managed search creates an execution and durable invocations, persists
observations, follows continuations, and participates in selection and final
assembly. The repository and `ManagedTaskRunner` also support a standalone
one-URL detail/profile/site invocation without a listing or source plan; there
is no separate public managed-single-target facade yet.

Public standalone scraper APIs use direct execution by default. Persistence is
an explicit managed option, never an implicit side effect of calling a scraper.

## Self-Contained Scraper Bundles

Every installable scraper unit satisfies one of these bundle contracts:

```text
ScraperBundle<Input, Result>
  manifest: ParserManifest
  inputSchema: versioned schema
  outputSchema: versioned schema
  execute(input: Input, runtime: ParserRuntime) -> Result

SearchScraperBundle extends ScraperBundle
  planInitial(intent: SearchIntentView, target: ListingTarget)
    -> InitialSearchInputs
```

`planInitial` is deterministic and performs no I/O. It maps only supported
business criteria to the bundle's typed native filter schema. Network-backed
parameter resolution, such as converting a human-readable area into a
source-native id, is a bounded bootstrap action inside the first parser
invocation. The resulting continuation carries resolved state, so later calls
remain stateless.

Registering or removing a scraper bundle updates one registry entry. There is no
separate `SearchListingInputBuilder` registry that can drift from the parser.

## Manifests

All manifests contain:

| Field | Meaning |
| --- | --- |
| `parserId` | Stable implementation id. |
| `parserType` | `search_listing`, `vacancy_detail`, `company_profile`, or `company_site`. |
| `implementationVersion` | Pinned implementation version. |
| `inputSchemaId` | Versioned input contract. |
| `outputSchemaId` | Versioned result/public-fact contract. |
| `transport` | `http`, `browser`, or `hybrid`. |
| `providerIds` | Provider namespaces understood by the scraper. |
| `supportedUrlPatterns` | Deterministic target-routing patterns. |
| `outputFacts` | Fact paths this scraper can explicitly produce. |
| `invocationScope` | `stateless_unit` or `session_batch`. Non-listing scrapers use `stateless_unit`. |

A search-listing manifest additionally declares:

| Field | Meaning |
| --- | --- |
| `sourceKinds` | Catalog/discovered source kinds supported by the bundle. |
| `queryMode` | `per_query`, `query_group`, or `downstream_only`. |
| `collectionUnit` | `page` or `cursor_batch`. |
| `nativeCriteria` | Criteria that alter source requests. |
| `defaultUnitBudget` | Maximum emitted collection units. |
| `defaultItemBudget` | Maximum accepted listing items. |
| `defaultInvocationBudget` | Includes bounded zero-unit bootstrap calls. |
| `maxUnitsPerInvocation` | `1` for `stateless_unit`; bounded value greater than one for `session_batch`. |

Query modes are exact:

- `per_query` creates one initial input per query variant, as HH does;
- `query_group` lets the source group variants into one or more native requests;
- `downstream_only` fetches a shared collection and carries all queries for
  source-local text matching without pretending the source applied them.

URL routing must either resolve exactly one parser or return
`unsupported_target` / `ambiguous_target`. Registration order is never a
tie-breaker.

`session_batch` is reserved for a browser source whose pagination requires one
live session. Before leasing such an invocation, the coordinator atomically
reserves at most `maxUnitsPerInvocation` from the source-plan budget. The
reserved unit count is runtime context, not public parser input. The parser
returns only after the session batch finishes, so downstream work starts at the
batch boundary rather than after every page.

## Runtime Ports

```text
ManagedParserExecutor
  execute(invocation: ParserInvocation) -> ParserExecutionResult

DirectScraperExecutor
  execute(parserRef, input) -> ParserExecutionResult

ParserRuntime
  prepareHttp(action: HttpAction) -> admitted | retryAfter
  http(action: HttpAction) -> HttpResponse
  reservedCollectionUnits -> positive integer
  attemptMetrics -> ParserAttemptMetrics

ResourceGate
  admit(action: NetworkAction, context: OperationContext) -> ActionPermit

OperationContext
  operationId
  executionId: string | null
  invocationId: string | null

ResourceGateBackend
  acquire(resourceKey, policy, operationId) -> ResourceSlotPermit
  release(resourceSlotPermit)
```

Every HTTP request and followed redirect is admitted under a logical resource
policy. A future browser runtime must place main-document navigation, browser
API calls, and intercepted subresources behind the same port; no current v2
bundle or `ParserRuntime` implementation exposes browser actions.

Managed calls populate all operation-context ids. Direct calls use a generated
`operationId` and null execution/invocation ids. Both therefore participate in
the same deployment resource limits.

The runtime rejects non-HTTP(S) targets, loopback/private/link-local/metadata
addresses, unsafe redirects, oversized bodies, and disallowed downloads.
Scrapers cannot instantiate their own network client.

## Internal Invocation Envelope

`ParserInvocation` is coordinator state, not a public scraper input:

| Field | Type | Meaning |
| --- | --- | --- |
| `invocationId` | string | Durable task id. |
| `executionId` | string | One normal or append execution. |
| `sourcePlanId` | string or null | Listing source branch when applicable. |
| `parentInvocationId` | string or null | Diagnostic lineage only. |
| `causeEventId` | string or null | Event that created the task. |
| `parserId` | string | Pinned implementation id. |
| `parserVersion` | string | Pinned implementation version. |
| `parserType` | enum | Contract discriminator. |
| `inputSchemaId` | string | Schema used to validate `input`. |
| `input` | object | Complete standalone parser input. |
| `taskClass` | enum | Fair-scheduling class. |
| `taskKey` | string | Deterministic execution-scoped idempotency key. |
| `availableAt` | datetime | Earliest lease time. |
| `leaseToken` | string or null | Changes on every lease. |

Task keys use normalized target identities:

```text
search_listing:{parserId}:{sourcePlanId}:{inputFingerprint}
vacancy_detail:{parserId}:{providerId}:{vacancyIdentity}
company_profile:{parserId}:{providerId}:{profileIdentity}
company_site:{parserId}:{registrableDomain}:{entryPath}
```

`UNIQUE(execution_id, task_key)` prevents duplicate execution work.
`listing_enrichment_requests` records every listing consuming a shared task.

The lease scheduler maps `listing` tasks to one branch and
`detail/profile/site` tasks to an enrichment branch. One bounded SQL query
loads up to the requested lease limit from each branch together with current
active counts. Each free worker slot goes to the less represented active
branch. When counts are equal, the oldest ready head wins; an exact timestamp
tie goes to listing. Therefore a page continuation cannot be buried under a
new detail backlog, while listing work also cannot indefinitely suppress older
enrichment when concurrency is one. The decision adds no scheduler-state row
or per-task write beyond the normal lease update.

## Exact Parser Inputs

### SearchListingInput

```text
{
  sourceId: string,
  targetProviderId: string,
  queries: NonEmptyArray<string>,
  target:
    | { kind: "catalog" }
    | { kind: "discovered_url", url: URL },
  cursor: object,
  nativeFilters: object,
  resolvedState: object | null
}
```

`nativeFilters` is validated by the bundle's source-specific schema.
`resolvedState` is JSON-safe, non-secret continuation state such as a resolved
area id. It is never copied to public output. Cookies, credentials, and bearer
tokens are not serialized into invocation input. Session-bound prerequisite
work must finish inside one bounded invocation. Continuations may change only
`cursor` and `resolvedState`; source, target, queries, and requested filters
remain invariant.

### VacancyDetailInput

```text
{
  targetProviderId: string,
  vacancyUrl: URL,
  sourceListingId: string | null
}
```

`targetProviderId` identifies the provider serving the target page. The listing
source that discovered the URL belongs to graph lineage and is not overloaded
into this field.

### CompanyProfileInput

```text
{
  targetProviderId: string,
  companyProfileUrl: URL,
  sourceCompanyId: string | null
}
```

### CompanySiteInput

```text
{
  siteUrl: URL
}
```

No parser input contains request delay, timeout, retry, concurrency, queue
priority, requested sections, downstream criteria, or listing snapshots.

## Exact Parser Results

There is no generic result shape that permits illegal cross-type combinations.

### SearchListingResult

```text
{
  kind: "search_listing",
  outcome: "success" | "no_results" | "partial_success",
  items: SearchListingOutput[],
  continuations: SearchListingInput[],
  collectionUnitsConsumed: nonNegativeInteger,
  publicNotice: string | null
}
```

Invariants:

- `no_results` requires no items and no continuations;
- `success` requires at least one item or one continuation;
- `partial_success` requires at least one valid item, commits those items, marks
  the source plan partial, and is not retried as a failed invocation;
- a continuation from a partially parsed response is accepted only when its
  cursor was independently validated;
- zero consumed units are allowed only for a bounded bootstrap result with no
  items and at least one continuation;
- `stateless_unit` results consume at most one unit;
- `session_batch` results consume no more than the units reserved by the
  coordinator and may emit items from several pages in one result;
- every continuation uses the same parser id/version, schema, source plan,
  provider, target, query set, and native filters;
- continuation fan-out is bounded by manifest and source-plan budgets;
- a `session_batch` continuation is allowed only when a fresh session can resume
  from its non-secret cursor; live browser state is never continued.

### Singleton Results

```text
VacancyDetailResult = {
  kind: "vacancy_detail",
  outcome: "success" | "not_found",
  item: VacancyDetailOutput | null,
  publicNotice: string | null
}

CompanyProfileResult = {
  kind: "company_profile",
  outcome: "success" | "not_found",
  item: CompanyProfileOutput | null,
  publicNotice: string | null
}

CompanySiteResult = {
  kind: "company_site",
  outcome: "success" | "not_found",
  item: CompanySiteOutput | null,
  publicNotice: string | null
}
```

For singleton results, `success` requires exactly one item and `not_found`
requires `item = null`. They cannot return continuations. Missing optional
source facts are represented by null or an empty collection, not by
`partial_success`.

Execution failures are separate:

```text
ParserFailure = {
  kind:
    | "blocked"
    | "rate_limited"
    | "source_timeout"
    | "http_client_error"
    | "http_server_error"
    | "network_error"
    | "parse_error"
    | "invalid_input"
    | "invalid_source_output"
    | "implementation_unavailable"
    | "resource_failure"
    | "unsupported_target",
  publicNotice: string | null
}
```

Operational diagnostics, raw HTML/JSON, screenshots, stack traces, request
history, and dropped-row details belong to the internal execution envelope and
artifact storage, not public parser facts.

## Exact Public Fact Types

Every key below is present. Optional source facts use explicit null or an empty
array.

```text
CompanyRef = {
  name: string | null,
  targetProviderId: string | null,
  sourceCompanyId: string | null,
  profileUrl: URL | null,
  officialSiteUrl: URL | null,
  sourceVacanciesUrl: URL | null
}

SourceLocation = {
  text: string
}

SalaryRange = {
  from: number | null,
  to: number | null,
  currency: ISO4217 | null,
  gross: boolean | null,
  period: "hour" | "day" | "month" | "year" | null
}

RemoteScope = {
  kind: "country" | "region" | "worldwide",
  code: string | null
}

ApplicationChannel = {
  kind: "apply_url" | "email" | "phone" | "telegram" | "other",
  value: string,
  label: string | null
}

PublicContact = {
  kind: "email" | "phone" | "telegram" | "other",
  value: string,
  label: string | null
}

SocialLink = {
  network: string,
  url: URL
}

EmploymentType =
  | "full_time"
  | "part_time"
  | "contract"
  | "temporary"
  | "internship"
  | "other"

DiscoveredEndpoint = {
  kind: "career_listing" | "career_page" | "ats_board",
  url: URL,
  providerHint: string | null,
  confidence: "confirmed" | "probable" | "candidate",
  discoveryMethod:
    | "explicit_link"
    | "redirect"
    | "structured_data"
    | "platform_signature"
}
```

`currency` is emitted only when the source exposes a recognized ISO 4217
currency. A symbol or unrecognized source code is not guessed.
`workFormats` uses only `onsite`, `hybrid`, and `remote` mapped from explicit
source signals. `remoteScopes` is emitted only from explicit eligibility
evidence.

### SearchListingOutput

```text
{
  sourceId: string,
  targetProviderId: string,
  sourceListingId: string | null,
  title: string,
  company: CompanyRef | null,
  location: SourceLocation | null,
  salary: SalaryRange | null,
  workFormats: ("onsite" | "hybrid" | "remote")[],
  remoteScopes: RemoteScope[],
  nativeGrade: string | null,
  postedAt: date | datetime | null,
  vacancyUrl: URL,
  applyUrl: URL | null,
  summary: string | null
}
```

### VacancyDetailOutput

```text
{
  targetProviderId: string,
  sourceListingId: string | null,
  canonicalVacancyUrl: URL,
  title: string | null,
  company: CompanyRef | null,
  description: string | null,
  requirements: string[],
  responsibilities: string[],
  conditions: string[],
  skills: string[],
  employmentTypes: EmploymentType[],
  salary: SalaryRange | null,
  workFormats: ("onsite" | "hybrid" | "remote")[],
  remoteScopes: RemoteScope[],
  applicationChannels: ApplicationChannel[]
}
```

### CompanyProfileOutput

```text
{
  targetProviderId: string,
  profileUrl: URL,
  sourceCompanyId: string | null,
  companyName: string | null,
  description: string | null,
  industry: string | null,
  sizeText: string | null,
  locations: SourceLocation[],
  officialSiteUrl: URL | null,
  careerEndpoints: DiscoveredEndpoint[],
  contacts: PublicContact[],
  socialLinks: SocialLink[]
}
```

### CompanySiteOutput

```text
{
  canonicalSiteUrl: URL,
  companyName: string | null,
  contacts: PublicContact[],
  socialLinks: SocialLink[],
  careerEndpoints: DiscoveredEndpoint[]
}
```

Not present in public outputs: input queries, page/rank, source-page URL, request
settings, raw format arrays, inferred geography, queue state, debug fields, or
facts copied from another scraper.

## Fact Requirements and Provider Graph

Criteria planning creates explicit requirements and possible providers:

```text
CriterionRequirement
  criterion
  requiredFactPath
  comparison
  skipWhenFinalKeep

FactProvider
  requirementId
  providerStage:
    native_request | listing_output | detail_output |
    profile_output | site_output | derived_fact | unavailable
  parserId: string | null
  deriverId: string | null
  factPath
  dependsOnFactPaths
  requiredForFinal
  costClass
```

The planner derives providers from manifests. A criterion can have several
ordered providers. For example, salary may be available from listing output and
vacancy detail; application channels may come from vacancy detail or company
site.

`FactDeriver` is a pure, versioned workflow component:

```text
FactDeriver
  derivationId
  implementationVersion
  inputFactPaths
  outputFactPaths
  derive(exact observation payloads) -> DerivedFacts
```

It performs no network access. Grade estimation from title/description, salary
extraction from text, work-format normalization, remote-scope derivation, and
geography normalization belong here when they are not explicit parser facts.
Each `fact_derivation` stores the deriver version, exact input observation ids,
input fingerprint, output schema, and derived payload.

Relocation support and visa sponsorship are separate derived facts. Explicit
relocation assistance/package evidence can satisfy `relocation = true`; visa
sponsorship alone sets `visa_sponsorship = true` and cannot satisfy a relocation
criterion. This prevents a role restricted to another country from passing a
relocation branch merely because immigration sponsorship is mentioned.

Provider dependencies are explicit. For example:

```text
grade criterion
  -> listing.nativeGrade
  -> derive.gradeFromText(title, description)
       -> vacancy_detail.description when description is missing
```

After each relevant immutable observation is stored:

1. `FactMaterializer` selects exact execution-scoped observation ids.
2. Applicable `FactDeriver` components run or reuse an identical derivation.
3. `FactMaterializer` creates a fact set containing observation and derivation
   ids.
4. `PreliminarySelector` evaluates definitive facts.
5. `FactRequirementPlanner` schedules the minimum unresolved provider chain.
6. Optional contact/profile/site enrichment starts only after a preliminary or
   final keep unless those facts are required for the decision.
7. Every detail/profile/site observation settles all provider edges sharing
   its listing and invocation in one materialization pass, then re-runs the
   requirement planner so newly available URLs can unlock the next provider
   without waiting for other sources.
8. Every relevant new fact set invalidates the previous final evaluation and
   triggers deterministic re-evaluation.

The shipped company-enrichment policy currently declares an HH company-profile
provider and a generic company-site provider. A trusted
`company.official_site_url` from listing output is canonicalized to
`official_site_url` and runs the site provider directly. When that URL is
missing, a trusted HH profile URL can run the fallback `profile -> site` chain.
It never searches by bare company name. Detail bundles are registered only for
sources whose catalog contract requires a real reviewed detail fixture.
Implementing a shared source class is not itself a declared detail capability.

A preliminary `keep` becomes final immediately only when no required provider is
unresolved. Optional facts that are excluded from selection cannot later change
that decision. For OR scenarios, `skipWhenFinalKeep` prevents an unresolved
alternative branch from scheduling network work after another branch already
proves the final keep.

## Identity and Standalone Persistence

Parser output is evidence, not entity resolution.

### Vacancy identity

Strong claims, in order:

1. `targetProviderId + sourceListingId`;
2. source-specific normalized canonical vacancy URL.

Cross-provider title/company similarity never automatically merges vacancy
resources. It is final-assembly dedupe evidence only.

`vacancy_resources` is the standalone target entity. A detail result upserts a
vacancy resource and stores a detail observation even when no listing exists.
`vacancy_listings` later links a list-page occurrence to that resource.

### Company identity

Strong claims are provider company id/profile URL and verified official domain.
Normalized company name is weak matching evidence and remains in immutable
parser observations; it does not create a company or identity-claim row.
`company_identity_claims` stores only strong claim type/value, one originating
observation FK, and merge status. Repeated sightings reuse that row. Explicit
alias/merge records preserve history; rows are not destructively rewritten.

URL normalization is provider-specific and versioned. Redirect aliases are
stored. Task keys use normalized identities, while raw request URLs remain
operational evidence.

### Cross-source duplicate policy

The first implementation uses two non-destructive levels:

| Level | Evidence | Behaviour |
| --- | --- | --- |
| `exact` | Same provider listing id, same normalized canonical/direct vacancy URL, or an explicit source-to-employer vacancy link | Link to one `vacancy_resource`, share detail work, collapse to one final item while preserving source variants. |
| `probable` | Strongly resolved same company plus normalized title and compatible location/date evidence | Store a duplicate group for review/presentation, but keep separate resources, tasks, fact sets, and final items. |

Title/company similarity alone is never `exact`. Probable groups do not suppress
results and cannot cause one source's detail facts to populate another source's
vacancy.

`DuplicateResolver` runs after new identity claims and again before final
assembly. `vacancy_duplicate_groups` and `vacancy_duplicate_members` preserve
the evidence, confidence, policy version, and every source variant. Detail tasks
are shared only by exact `vacancy_resource` identity; profile/site tasks are
shared only by strong company identity.

## Immutable Facts and Transaction Contract

Canonical resource tables contain identity and relationships, not mutable parser
payloads. Parser facts live in immutable observation tables.

`fact_sets` are compact immutable snapshots, not normalized member graphs:

```text
FactSet
  factSetId
  executionId
  listingId
  evidenceRefsJson:
    listingObservationId
    detailObservationId | null
    profileObservationIds[]
    siteObservationIds[]
    derivationIds[]
  materializedFactsJson
  fingerprint
```

The coordinator validates every referenced id before insert. The single-row
snapshot trades redundant foreign-key join tables for one auditable reference
document and one selector read. An identical fingerprint reuses the existing
fact set. A presentation-only observation that cannot affect selection does not
create another fact set or evaluation.

For a successful invocation, `GraphCoordinator` performs one transaction:

1. validate result kind, schema, parser/source invariants, and current lease;
2. upsert identity resources and append immutable observations;
3. atomically reserve and insert every accepted continuation;
4. append a versioned outbox event containing observation ids;
5. update source-plan counters and lifecycle;
6. mark the invocation succeeded and invalidate the lease;
7. commit.

Outbox events:

| Event | Immutable payload | Coordinator action |
| --- | --- | --- |
| `listing_observations_stored` | source plan, invocation, observation ids | Build fact sets, preliminary evaluations, required enrichment dependencies. |
| `vacancy_detail_observation_stored` | vacancy id, detail observation id | Rebuild fact sets for every consumer listing and re-evaluate. |
| `company_profile_observation_stored` | company/profile observation id | Re-evaluate consumers and plan verified site/career endpoints. |
| `company_site_observation_stored` | company/site observation id | Re-evaluate consumers and resolve discovered endpoints. |
| `invocation_terminal` | invocation id, terminal reason | Mark every dependent consumer terminal and settle affected fact sets. |

Event payloads have `eventSchemaVersion`. The sole consumer holds one
execution-scoped coordinator lease/token on `search_executions`. It reads a
bounded unprocessed event batch without writing claim state to every event,
groups events by affected listing, loads all referenced observations with
batched `IN` queries, and writes at most one newest fact set/evaluation per
listing in the handler transaction. The same transaction validates the
coordinator token and marks the whole batch processed. An expired coordinator
cannot commit derived work.

Source-plan terminal state is already committed in `source_plans` and is read by
the completion monitor. It does not create a second outbox event unless a future
external subscriber requires one.

## Shared Enrichment Dependencies

`listing_enrichment_requests` is the many-to-many edge:

```text
(execution_id, listing_id, invocation_id nullable, provider_id, required,
 resolution_outcome, status, terminal_reason)
```

A deterministic task may already exist when another listing requests the same
profile/site/detail target. The coordinator inserts another consumer edge
instead of another task. Result or terminal events fan out to all consumers.
When employer target resolution ends without a trusted URL, the same row records
`unresolved_no_trusted_url` with no invocation; no separate resolution row is
written.

`parentInvocationId` remains diagnostic lineage and is never used to infer all
consumers.

## Persistence I/O Rules

The managed graph minimizes SQLite write amplification:

1. One parser-result transaction writes all items from the invocation, all
   accepted continuations, one batch event, source counters, and task terminal
   state. It never emits one event per item.
2. One execution-scoped coordinator lease replaces per-event claim writes.
   Bounded event batches coalesce multiple events affecting one listing into at
   most one new fact set/evaluation for the newest fingerprint.
3. `fact_sets` store one compact materialized snapshot plus evidence-id JSON;
   there are no per-type fact-set member tables.
4. `fact_derivations` use a unique input fingerprint and are reused rather than
   recalculated or rewritten.
5. Main graph storage keeps one aggregate `parser_attempt` per parser-call
   attempt, including network-action count, network elapsed time, last HTTP
   status, and terminal error class. Those fields are written in the existing
   terminal attempt update, so diagnostics add no separate hot-path write.
6. `artifact_index` reserves a relation for a future failure/debug artifact
   writer. The current runtime does not persist raw responses or per-action
   telemetry.
7. A `companies` row and identity claim are created only from a strong provider
   id/profile URL/verified domain. A name-only company remains in the immutable
   parser observation with `listing.company_id = null`.
8. Re-observing an existing identity does not update a `last_seen` column.
   First/last seen values are derived from indexed immutable observations when
   producing reports.
9. Exact/probable duplicate groups are batch-written once during final
   assembly, not rewritten after every observation.
10. Ready-task leasing uses bounded batch claims, while completion uses indexed
    `EXISTS` checks. No write-maintained global counter row is added solely to
    avoid those reads.
11. Parser-result handling never performs identity or task lookups in an item
    loop. It normalizes all result keys first, bulk-loads matching identities in
    one query, then uses set-based inserts/upserts with `RETURNING` for missing
    resources, listings, claims, tasks, and consumer edges.
12. Requirements and providers are loaded once per coordinator event batch for
    the affected source plans, then reused for every listing in that batch.
    Parser manifests are resolved from the immutable in-process registry.
13. Fact materialization bulk-loads current snapshots and enrichment-edge state
    for the whole affected-listing batch. The normal observation path does not
    issue snapshot or dependency-state queries per listing. Final assembly also
    uses bounded bulk queries.
14. The resource gate acquires a slot and advances pacing in one short
    transaction, then releases the fixed slot in one update. It never holds a
    SQLite transaction open during a network request.

These rules keep correctness-critical writes transactional while moving verbose
diagnostics and recomputable projections out of the hot path.

| Hot operation | Earlier normalized shape | Target shape |
| --- | --- | --- |
| One materialized fact set | `fact_sets` plus 1-5 member-table inserts and later joins | One `fact_sets` insert and one-row selector read |
| One network action | `resource_requests` plus `request_attempts` inserts in graph DB | No graph-DB row; invocation-local counters are committed with the parser attempt |
| Event dispatch | Claim update and handler transaction per event | One execution coordinator lease plus one transaction per bounded/coalesced batch |
| Repeated strong company claim | Claim row plus one evidence row per observation | One claim row with first strong observation FK; repeated sightings write nothing |
| Employer URL resolution | Separate resolution row plus task/dependency | Existing enrichment-request row records resolved/unresolved outcome |
| Source-plan terminal | State update plus outbox event and processed update | One idempotent source-plan state update |
| Deployment limiter action | Growing lease-history rows | Updates to fixed `resource_state` and `resource_slots` rows |

The intended database access shape is bounded by batches, not result count:

| Managed operation | Graph DB access target |
| --- | --- |
| Persist one parser result | One transaction; a fixed number of bulk reads/writes plus payload-sized row inserts |
| Consume ready events | One coordinator acquire/renewal and one transaction per bounded batch, not per event |
| Schedule enrichment | One set-based existing-task lookup/upsert for all affected listings |
| Assemble final output | Bounded bulk reads and one bulk snapshot write, not N listing queries |
| Perform one network action | One short limiter acquire transaction and one slot-release update in the separate limiter DB |

SQLite parameter limits set the maximum chunk size for bulk `IN` and insert
statements. Chunking is deterministic and occurs at the repository boundary;
it does not reintroduce item-by-item queries.

The result transaction still writes immutable parser observations because they
are the non-recomputable source evidence. That write volume is intentional and
is not replaced with a mutable latest-value cache.

## Employer Target Resolution

`EmployerTargetResolver` determines whether profile/site work has a trustworthy
target. It performs no network request and accepts URL candidates only from:

1. listing/detail/profile/site parser output;
2. a versioned trusted source-catalog mapping;
3. a verified redirect alias from a previous managed or direct call.

Its result is:

```text
resolved_profile(providerId, profileUrl)
resolved_site(siteUrl)
resolved_career(endpointId)
ambiguous(candidateIds)
unresolved_no_trusted_url
rejected_unsafe_target
```

A bare company name or weak name-only company claim never triggers web search
or a site scraper in the first implementation. `unresolved_no_trusted_url` is a
normal terminal resolution outcome recorded on affected enrichment requests.
This avoids silently attaching a vacancy to the wrong employer.

When a profile scraper later exposes an official site, or a site scraper exposes
a career endpoint, that parser observation becomes new trusted input and the
resolver runs again.

## Dynamic Source Discovery

A `DiscoveredEndpoint` is stored before routing. The
`TargetParserResolver` returns exactly one of:

```text
resolved(parserId, parserVersion, providerId)
unsupported
ambiguous(candidateParserIds)
rejected_unsafe_target
```

For a resolved career listing/ATS endpoint, the coordinator creates a normal
`source_plan` with `originEventId`, `originCompanyId`,
`originEndpointId`, and its own budgets. The selected listing bundle creates
initial inputs through `planInitial`.

The graph normalizes every discovered URL, deduplicates endpoint observations
and task keys, and enforces a persisted per-execution discovered-source-plan
budget. Every discovered plan also has its own unit, item, and invocation
budgets. Once the execution budget is exhausted, later endpoints terminate as
`budget_exhausted` without creating tasks.

## Source Plan Lifecycle

`source_plans.status` is:

```text
planned -> running ->
  succeeded | no_results | partial | limit_reached | failed | cancelled
```

Rules:

- the plan becomes `running` when its first invocation is leased;
- continuation budget is reserved atomically before continuation tasks are
  inserted, including fan-out;
- `succeeded` means at least one item was accepted and all branches exhausted;
- `no_results` means all branches exhausted with no item and no terminal branch
  failure;
- `partial` means usable items exist but a result/branch ended partially or
  terminally;
- `failed` means no usable items and at least one terminal branch failure;
- `limit_reached` is normal bounded completion, not parser failure;
- `cancelled` records deadline/user cancellation.

`collection_units_used` counts actual listing pages/cursor batches declared by
`collectionUnitsConsumed`. `invocations_used` also counts zero-unit bootstrap
calls, preventing an infinite continuation chain. Network actions are counted
separately by `ResourceGate`.

Every terminal transition is committed idempotently on `source_plans`.
Completion reads that state directly; no redundant terminal outbox event is
written.

### Session-bound listing sources

`stateless_unit` is the default and preserves page-level graph interleaving.
`session_batch` is allowed only when captured source behaviour proves that
pagination requires one live browser session.

For a session batch:

1. the coordinator reserves a bounded number of collection units before lease;
2. `ParserRuntime.reservedCollectionUnits()` exposes that internal allowance;
3. the scraper keeps cookies/tokens only in its invocation-scoped browser
   session;
4. the result may consume several units but never more than reserved;
5. observations and downstream events commit when the batch returns;
6. a continuation is emitted only if a new session can resume from a
   non-secret cursor.

The reduced interleaving is explicit source capability, not an invisible parser
exception. Sources without captured evidence for session continuity remain
`stateless_unit`.

## Selection Lifecycle

Preliminary outcomes:

| Outcome | Meaning |
| --- | --- |
| `reject` | A definitive available fact failed. No selection-required enrichment is needed. |
| `enrich` | Required facts are unresolved and at least one provider can obtain them. |
| `keep` | Available facts match; may be final only when required dependencies are terminal. |

Final outcomes are `keep` or `reject` with reason codes, missing-evidence
diagnostics, and an exact `factSetId`.

Profile/site observations trigger final re-evaluation when they supply facts in
the execution's provider plan. They are presentation-only when no selected
criterion depends on them.

Global cross-source deduplication, ranking, and top-N remain final-assembly
operations.

## Concrete Interleaving

```text
HH page 1 invocation succeeds
-> store page-1 listing observations
-> reserve and enqueue HH page 2 continuation
-> emit listing_observations_stored(page-1 observation ids)

Coordinator consumes the event
-> build one fact set per listing occurrence
-> preliminary selection
-> enqueue only required detail/profile/site tasks
-> attach listing_enrichment_requests consumer edges

TaskRunner may now lease:
  HH page 2
  Habr page 1
  HH detail A
  a shared profile task needed by several listings

The fair lease balances the listing and enrichment branches by current active
count and age. No source-level or listing-stage barrier exists.
```

## Resource Scheduling

`ResourceGate` applies service-owned policies by logical resource:

| Policy | Meaning |
| --- | --- |
| `maxConcurrency` | In-flight actions for the resource. |
| `minDelayMs` | Delay between admitted action starts. |
| `fetchTimeoutSeconds` | Service-owned timeout applied by `ParserRuntime`. |
| `sourceAttemptTimeoutSeconds` | Maximum duration of one managed parser call. |
| `runTimeoutSeconds` | Global execution deadline. |
| `RequestRetryPolicy` | Safe request statuses/errors, maximum attempts, per-attempt timeout, elapsed request budget, exponential backoff, full jitter, and `Retry-After`. |
| Source-plan unit/item/invocation budgets | Bounds listing collection and bootstrap work. |

Only `RequestRetryPolicy` decides whether a concrete request is retried. The
managed runner persists that decision on the same `ParserInvocation`; direct
execution and `HttpArtifactFetcher` use the same policy through
`InMemoryRequestRetrier`. There is no source-wide or run-wide retry override.
`SourceFetchRequest` is a read-only contract: its POST form is reserved for
idempotent search-query APIs such as Workday, never for mutations. Therefore
these fetches are safe to retry after transient transport failures; arbitrary
`HttpAction` values must declare their own `RetrySafety`.
Safe retries cover timeouts, network errors, and explicit transient statuses
`408`, `425`, `429`, `500`, `502`, `503`, and `504`. Other failures are
terminal.

Before a managed parser attempt starts, the bundle builds its pure `HttpAction`
and `ResourceGate.try_admit` performs non-blocking preflight. If pacing delays
the action, the invocation becomes `waiting` with
`waiting_reason = resource_pacing`; no `parser_attempts` row, active slot, or
lease is held. A retryable request failure becomes `waiting` with
`waiting_reason = retry_backoff`, and only that invocation is eligible again at
`available_at`.

`ParserRuntime` owns network-target validation. DNS resolution and limiter I/O
run outside the event loop. The runtime rejects credentials, unsupported URL
schemes, and every non-global resolved address, then passes the exact validated
address set to the HTTP transport. The transport connects only to those pinned
addresses while preserving the original HTTP `Host` and TLS SNI hostname; it
does not resolve the hostname again. Redirect targets repeat the same validation
and pinning independently, and cross-origin redirects discard authorization,
cookie, and proxy-authorization headers. This prevents a DNS change between
validation and connection from turning a public URL into a private-network
request.

Managed tasks use a 30-second lease and one batched 10-second heartbeat for all
currently running invocations owned by the worker. Long request backoff is not
an active lease: it is durable `waiting` state. If heartbeats stop, lease expiry
closes the active attempt as `worker_lost` and requeues the same invocation. A
stale worker cannot commit after reassignment because its lease token no longer
matches.

The first implementation uses a deployment-scoped SQLite limiter at:

```text
{runsRoot}/_runtime/resource-gate.sqlite
```

This file is separate from every run/execution database and is shared by all CLI
processes using the same `runsRoot`. It contains:

- `resource_state(resource_key, max_concurrency, min_interval_seconds,
  lease_seconds, next_start_at, updated_at)`;
- `resource_slots(resource_key, slot_number, operation_id, owner_id,
  lease_until)` with a fixed bounded slot set per policy.

Acquisition uses one `BEGIN IMMEDIATE` transaction: find an empty or expired
slot, verify `next_start_at <= now`, update that slot with a crash-expiring
owner, and advance `next_start_at` by the configured interval. Release clears
the same fixed row. This avoids an insert/update history row for every network
action and prevents unbounded limiter-table growth. Direct and managed calls use
the same backend, so concurrent CLI searches obey one resource limit.

Cancellation does not abandon an acquire already running in a worker thread.
Cleanup waits for that acquire and releases any resulting permit before
propagating cancellation, including when cancellation is requested repeatedly
during cleanup.

Weighted fairness between task classes remains coordinator-local; resource
concurrency/pacing is deployment-global. A multi-host deployment replaces only
`ResourceGateBackend` with Postgres/Redis or another linearizable lease store.
It must not fall back to independent in-memory limiters.

## Completion and Deadline

An execution is drained only when, in one coordinator transaction:

1. no invocation is queued, leased, or waiting;
2. no outbox event is unprocessed;
3. no dependency is waiting on a non-terminal invocation;
4. every source plan is terminal;
5. no active lease can commit;
6. the transaction inserts no continuation, derived task, dependency, or source
   plan.

At deadline, the execution enters `stopping`, rejects late commits, invalidates
remaining leases, marks invocations/dependencies/source plans cancelled, and
then performs the normal drain check. Final assembly keeps already committed
facts and preserves `completion_reason = deadline`.

Final artifacts expose `execution_quality` as `complete`, `degraded`, or
`failed`, plus `source_coverage` with planned, complete, degraded, failed, and
per-status source counts. Degraded sources do not erase usable observations,
and a failed source never silently appears as a complete run.

## Run and Append Isolation

`run_id` identifies the reusable identity corpus and report root.
`execution_id` identifies one normal or append search.

Identity resources may be shared across executions. Parser observations,
criteria/provider plans, fact sets, evaluations, tasks, events, and final
snapshots are execution-scoped.

An event and evaluation read only the immutable observation ids visible to that
execution. A shared convenience projection may point to the latest observation
for browsing, but selectors and final assembly never use it. Concurrent append
executions therefore cannot change each other's facts.

## CLI Mapping

| CLI field | Owner |
| --- | --- |
| query, grades, salary, date, work format, remote scope, geography | `SearchIntent` and fact-requirement planning |
| source and source type | Initial `SourceSelector` |
| excluded companies/text | Selection policy |
| run id, append id, runs directory | Application/storage |
| timeout, retry, delay, concurrency, budgets | Service runtime configuration |

The CLI does not know source-native ids. Human values are mapped by
`planInitial` when static or resolved by the parser as a bounded bootstrap call
when network access is required.

## Final Projection

`FinalAssembler` reads the execution's latest terminal final evaluation and its
exact fact set. It applies field-specific precedence, then execution-scoped
dedupe/ranking and writes an immutable `final_vacancies` snapshot.

Detail facts may override listing facts only through an explicit projection
rule. Profile/site facts join through resolved company identities and consumer
dependencies. Operational data never enters the public projection.

Exact duplicate-group members produce one final item with all source variants
and provenance preserved. Probable duplicate groups remain separate final items
and expose only an internal/report grouping marker; they are not silently
collapsed. Ranking operates on exact groups plus ungrouped/probable members.

## Required Verification Before Implementation Is Complete

- direct bundle execution creates no graph or persistence rows and still obeys
  the deployment resource gate;
- a standalone detail URL can be parsed and persisted before any listing exists;
- registering a listing bundle requires no external source-specific builder;
- one listing result can atomically fan out several continuations without
  exceeding budgets;
- a zero-unit bootstrap continuation is bounded and restart-safe;
- page 1 can create detail work while page 2 and another source remain runnable;
- one profile task can satisfy several listing consumers;
- profile/site facts trigger re-evaluation only when declared providers;
- derived grade/salary/work-format/geography facts retain exact input
  observation ids and deriver version;
- missing profile/site URLs terminate as `unresolved_no_trusted_url` and never
  trigger bare-name network search;
- concurrent CLI processes sharing a runs root cannot exceed one resource
  concurrency/delay policy, and expired limiter slots recover after crash;
- `session_batch` cannot consume more units than atomically reserved and never
  serializes live browser credentials;
- exact duplicate evidence shares one vacancy resource while probable matches
  remain separate and do not share scraper facts;
- event replay creates no duplicate observations, tasks, dependencies, fact
  sets, or source terminal transitions;
- a selected event batch creates at most one newest fact set/evaluation per
  affected listing fingerprint;
- successful managed requests do not write per-network-action telemetry rows to
  the graph database;
- repeated limiter activity reuses fixed resource-slot rows instead of growing
  the deployment database without bound;
- an event always evaluates its immutable observation/fact-set version;
- concurrent append executions cannot observe each other's parser facts;
- every source plan reaches exactly one terminal outcome;
- parser-resolution ambiguity fails explicitly and never depends on registry
  order;
- normalized endpoint ids, task-key dedupe, and the persisted execution budget
  terminate recursive career discovery;
- process restart recovers task leases and the execution coordinator lease;
- a committed successful page is never fetched again, while an uncommitted
  `worker_lost` invocation is safely reassigned;
- retry backoff and resource pacing create no active parser attempt and hold no
  task lease or resource slot;
- all active invocation leases are renewed by one batched 10-second heartbeat;
- full-catalog healthy execution stays within 120 seconds and widespread
  degraded failure settles with explicit quality within 180 seconds;
- parser versions referenced by active invocations remain installed; otherwise
  the invocation terminates explicitly as `implementation_unavailable`;
- browser and HTTP scrapers can only reach the network through `ParserRuntime`;
- deadline settlement cannot race a late parser commit;
- final assembly starts only after graph drain.
