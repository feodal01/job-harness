# Page Retry And Fast Leases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry each page independently with durable backoff and jitter, recover dead workers quickly, remove obsolete retry layers, and meet the full-catalog latency budget.

**Architecture:** One HTTP parser invocation owns one logical page. A single request policy decides retries; managed execution persists `waiting` decisions while direct execution waits in memory. Short renewable leases cover only active work, and resource pacing is moved out of worker slots.

**Tech Stack:** Python 3.14, asyncio, httpx, SQLite, unittest, DOT/Graphviz.

## Global Constraints

- Healthy 149-source full catalog completes in at most 120 seconds.
- Network-degraded execution completes in at most 180 seconds and reports `degraded`.
- Successfully committed pages are never fetched again.
- Backoff and resource pacing hold neither worker slots nor leases.
- Only one request-level retry policy exists.
- Use TDD for every behavior change.
- Do not add compatibility shims.

---

### Task 1: Request Retry Contract

**Files:**
- Create: `plugins/job-harness/src/job_harness/v2/runtime/request_retry.py`
- Modify: `plugins/job-harness/src/job_harness/v2/ports.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_request_retry.py`

**Interfaces:**
- Produces: `RequestRetryPolicy`, `RequestRetryDecision`, `RetrySafety`, and injectable `jitter`.
- Consumes: canonical HTTP status and transport failure kinds.

- [ ] Write failing tests for full-jitter bounds, maximum attempts, request budget, `Retry-After`, retryable statuses, and unsafe methods.
- [ ] Run `uv --directory plugins/job-harness run python -m unittest -v tests.v2.test_runtime_request_retry` and verify RED.
- [ ] Implement the pure decision policy with no sleeping, persistence, or source lookup.
- [ ] Re-run the focused test and verify GREEN.

### Task 2: One Page Per Invocation

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/independent.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/source_bundles.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/executors.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_independent_source_bundles.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_managed_task_runner.py`

**Interfaces:**
- Produces: pure `build_action(input) -> HttpAction` and `parse_response(input, response) -> Result` for HTTP bundles.
- Consumes: `HttpAction.retry_safety` from Task 1.

- [ ] Write failing tests proving a bundle exposes exactly one logical action and a parser failure cannot trigger network retry.
- [ ] Verify RED with the two focused test modules.
- [ ] Split HTTP bundle execution into action planning and response parsing while preserving typed results.
- [ ] Assert one logical page per managed HTTP invocation.
- [ ] Verify GREEN.

### Task 3: Durable Retry Waiting

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_schema.sql`
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/executors.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_scheduler.py`
- Test: `plugins/job-harness/tests/v2/test_persistence_graph_repository.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_managed_task_runner.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`

**Interfaces:**
- Produces: `defer_invocation(..., reason, available_at, retry_decision)` and `next_wakeup_at(execution_id)`.
- Consumes: `RequestRetryDecision` from Task 1.

- [ ] Write failing tests proving a retry records one failed request attempt, clears lease ownership, and waits until `available_at`.
- [ ] Write a failing scheduler test proving future waiting work prevents drain and wakes at its due time.
- [ ] Verify RED.
- [ ] Implement `waiting` state, `waiting_reason`, durable retry metadata, and scheduler wakeup.
- [ ] Remove the old runner-owned `commit_retry()` decision path.
- [ ] Verify GREEN.

### Task 4: Short Renewable Leases

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_scheduler.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_persistence_graph_repository.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_managed_task_runner.py`

**Interfaces:**
- Produces: `renew_invocation_leases(owner_id, leases, lease_until, now) -> int`.
- Consumes: active scheduler task lease ids and tokens.

- [ ] Write failing tests for batch renewal, stale-token rejection, expiry recovery, and no renewal while waiting.
- [ ] Verify RED.
- [ ] Implement a 30-second lease and one scheduler batch heartbeat every 10 seconds.
- [ ] Keep worker-loss attempts separate from HTTP retry-budget attempts.
- [ ] Verify GREEN.

### Task 5: Non-Blocking Resource Admission

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/resource_gate.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_scheduler.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/executors.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_resource_gate.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`

**Interfaces:**
- Produces: `ResourceGate.try_admit(...) -> ResourceSlotPermit | ResourceAdmissionDelay`.
- Consumes: pure page action from Task 2.

- [ ] Write a failing test where one saturated resource does not consume active parser slots or block another host.
- [ ] Verify RED.
- [ ] Add non-blocking admission and persist `waiting_reason=resource_pacing` without creating a request attempt.
- [ ] Preserve the blocking in-memory adapter for direct execution.
- [ ] Verify GREEN.

### Task 6: Remove Obsolete Retry Layers

**Files:**
- Delete: `plugins/job-harness/src/job_harness/v2/runtime/retry.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/config.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/__init__.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/enums.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/records.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/search_service_config.json`
- Modify: `plugins/job-harness/src/job_harness/v2/application.py`
- Test: `plugins/job-harness/tests/v2/test_architecture_boundaries.py`

**Interfaces:**
- Produces: exactly one `request_retry` configuration object.
- Consumes: request policy from Task 1.

- [ ] Write a failing architecture test rejecting `RetryPolicy`, `RetryInfo`, `RetryNextAction`, graph `max_attempts`, and runner retry configuration.
- [ ] Verify RED.
- [ ] Delete the obsolete contracts, imports, configuration, and tests.
- [ ] Migrate ATS probe fetching to the shared request execution policy.
- [ ] Verify GREEN.

### Task 7: Coverage Quality And Reporting

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/execution_artifacts.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/artifacts.py`
- Modify: `plugins/job-harness/src/job_harness/v2/presentation/report_template.html`
- Test: `plugins/job-harness/tests/v2/test_formatters.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`

**Interfaces:**
- Produces: execution quality `complete | degraded | failed` and source coverage counts.
- Consumes: terminal source-plan outcomes.

- [ ] Write failing tests for all-success, partial-source failure, and zero-usable-source executions.
- [ ] Verify RED.
- [ ] Add quality and coverage to receipt, JSON artifact, and report summary.
- [ ] Verify GREEN.

### Task 8: Architecture And SLA Verification

**Files:**
- Modify: `docs/v2-to-be-scraper-contract-architecture.md`
- Modify: `docs/v2-to-be-scraper-contract-flow.md`
- Modify: `docs/v2-to-be-scraper-contract-event-graph.dot`
- Modify: `docs/v2-to-be-scraper-contract-db-schema.dot`
- Modify: generated SVG counterparts
- Modify: `scripts/benchmark_v2_search.py`
- Modify: `scripts/verify_v2.py`

**Interfaces:**
- Produces: deterministic latency/fault benchmark and updated executable architecture.
- Consumes: all previous task contracts.

- [ ] Add a deterministic benchmark asserting healthy completion <=120 seconds and degraded completion <=180 seconds under widespread temporary timeout injection.
- [ ] Run the focused benchmark and verify RED before the scheduling changes, then GREEN afterward.
- [ ] Update Markdown/DOT contracts and regenerate SVG diagrams.
- [ ] Run `python3 scripts/verify_v2.py --skip-live`.
- [ ] Run the five-query Manual QA remote-RU full catalog search and audit source coverage, retries, final criteria, and elapsed time.
- [ ] Run `git diff --check` and review the complete diff against the design invariants.
