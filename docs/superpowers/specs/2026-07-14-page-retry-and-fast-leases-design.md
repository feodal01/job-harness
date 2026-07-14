# Page Retry And Fast Leases Design

## Goal

Make every HTTP page request independently retryable without ever repeating a
successfully committed page, while keeping a full healthy 149-source search
under 120 seconds and a network-degraded search under 180 seconds.

## Invariants

1. One managed HTTP `ParserInvocation` represents exactly one logical page
   request plus parsing of that response.
2. A committed `succeeded` invocation is terminal and is never requested again.
3. Pagination, vacancy detail, company profile, and company site pages are
   separate invocations.
4. Network retry applies only to the current logical page request. Parse,
   schema, unsafe-target, and unsupported-target failures are never retried.
5. A worker-loss recovery may repeat the current page only when no terminal
   commit exists.
6. Retry policy has one owner. Graph, runner, source, and resource-limit layers
   cannot replace or silently disable it.
7. Backoff and resource pacing never occupy an active worker slot or lease.
8. A global execution deadline may stop outstanding work, but the execution is
   then explicitly `degraded`; it is never presented as complete coverage.

## Page Contract

HTTP bundles expose two phases:

```text
buildAction(input) -> HttpAction
parseResponse(input, HttpResponse) -> typed result
```

`buildAction` is pure. It makes the target URL, method, resource key, and retry
safety visible before a worker performs network I/O. `parseResponse` is also
pure with respect to network access. Redirects remain part of the same logical
page request.

`HttpAction.retry_safety` is:

- `safe`: GET/HEAD and explicitly marked read-only search POST requests;
- `never`: non-idempotent or unknown POST/PUT/PATCH/DELETE requests.

Sources may declare retry safety, but they cannot declare attempt counts,
backoff, jitter, or timeouts.

## Durable State Machine

```text
queued
  -> waiting(resource_pacing)
  -> leased
       -> waiting(retry_backoff)
       -> succeeded
       -> failed
       -> queued(worker_lost)
  -> cancelled
```

`waiting` has no owner and no lease. It stores `waiting_reason` and
`available_at`. The scheduler treats future waiting work as non-terminal and
sleeps until the earliest due task, progress heartbeat, or execution deadline.

Each active request attempt creates one `parser_attempts` row. A retryable
network failure records `retry_decision=scheduled` and `retry_delay_ms`; an
exhausted failure records `retry_decision=exhausted`. `retryable` is removed
because it confuses error classification with an actual scheduling decision.

## Retry Policy

The single service-owned request policy is:

```text
max_attempts = 3
attempt_timeout_seconds = 15
base_delay_seconds = 1
max_delay_seconds = 8
jitter = full
request_budget_seconds = 55
```

For retry number `n`, full jitter chooses a delay in
`[0, min(max_delay, base_delay * 2^(n-1))]`. The random source is injectable so
tests remain deterministic.

Retryable outcomes are transport errors, network timeouts, HTTP 408, 425, 429,
500, 502, 503, and 504. A valid `Retry-After` value is respected when it fits
the remaining request budget; otherwise the request terminates as exhausted.

The policy produces a `RequestRetryDecision`. Managed execution persists that
decision. Direct execution waits in memory. Neither execution surface has a
second attempt-count or backoff policy.

## Lease And Worker Liveness

Active lease duration is 30 seconds. The scheduler renews all active leases it
owns every 10 seconds in one batch transaction. A worker waiting for network or
parsing remains leased and heartbeating. A task waiting for backoff or resource
pacing is not leased.

If heartbeat stops, lease expiry marks the active attempt `worker_lost` and
returns the page invocation to `queued`. Worker-loss recovery does not consume
the HTTP retry budget because no reliable network outcome was committed. A
stale worker cannot commit because owner and lease token validation remains
mandatory.

## Resource Scheduling And Speed

`ResourceGate` becomes non-blocking for graph execution:

```text
tryAdmit(resource, action) -> permit | available_at
```

If no permit is available, the invocation moves to
`waiting(resource_pacing)` without starting a parser attempt and without
occupying one of the active worker slots. Direct execution may await admission
in memory because it has no graph worker pool.

The scheduler continues to use deployment-scoped per-resource concurrency and
start intervals. It does not use one global source retry or circuit-breaker
policy. Independent hosts continue while another resource is rate-limited.

## Completion And Coverage

Execution quality is derived after drain:

- `complete`: all selected source plans reached a successful terminal state;
- `degraded`: at least one selected source plan failed, timed out, was blocked,
  or was cancelled at deadline;
- `failed`: no source plan produced usable listing observations.

The receipt and HTML report show succeeded/selected source counts and failure
counts by canonical kind. `drained` remains a scheduler condition, not a claim
of complete source coverage.

## Removed Surfaces

The refactor removes:

- `runtime/retry.py` and `RetryPolicy`;
- `RetryInfo` and `RetryNextAction`;
- `RetryServiceConfig` and `to_retry_policy()`;
- graph/runner `max_attempts` and `retry_delay_seconds`;
- runner-owned retry classification;
- repository `commit_retry()` and the old `retry_wait` semantics;
- `parser_attempts.retryable`;
- documentation and diagrams that assign network retry to `TaskRunner`.

`HttpArtifactFetcher` either delegates to the same request executor or is
removed when its remaining ATS-probe caller is migrated. No separate retry
implementation remains.

## Verification

Deterministic tests must prove:

- only the failing page is retried;
- a successful continuation page is not repeated;
- full-jitter bounds and deterministic injection;
- unsafe methods and parser errors do not retry;
- backoff releases worker and lease;
- scheduler waits for future work instead of draining;
- heartbeat renews active leases in one transaction;
- expired leases requeue only uncommitted pages;
- resource pacing does not consume worker slots;
- degraded coverage is visible in receipt and report;
- legacy retry symbols and configuration are absent.

Live verification uses the five-query Manual QA remote-RU full catalog search.
The healthy run must finish under 120 seconds. A deterministic fault transport
covering widespread temporary timeouts must finish under 180 seconds and report
`degraded` when retries are exhausted.
