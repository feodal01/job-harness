# Agent Search Contract Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the shipped job-search skill and v2 graph produce an efficient, auditable search result for current business criteria.

**Architecture:** Keep independent scraper bundles and the durable event graph. Make query mode source-specific, translate requested criteria into fact requirements backed by detail/profile providers, and project both kept and title-matching rejected rows from graph state into the report.

**Tech Stack:** Python 3.12, asyncio, SQLite, unittest, self-contained HTML reports.

## Global Constraints

- Business criteria stay in `SearchRequest`; runtime safety settings stay service-owned.
- Scrapers remain independently callable and source-specific.
- Unsupported source criteria remain diagnostics and never fabricate facts.
- No compatibility shims or legacy graph paths.
- Every behavior change follows RED-GREEN-REFACTOR.

---

### Task 1: Graph report projection

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/execution_artifacts.py`
- Modify: `plugins/job-harness/src/job_harness/v2/presentation/report_template.html`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_formatters.py`

- [ ] Add failing integration tests for latest kept and title-matching rejected graph rows.
- [ ] Add failing rendering test for OR scenarios.
- [ ] Implement one graph projection used by live and reopened runs.
- [ ] Render scenarios explicitly and verify the focused tests pass.

### Task 2: Business criteria fact planning

**Files:**
- Create: `plugins/job-harness/src/job_harness/v2/runtime/fact_requirements.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_pipeline.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/fact_derivers.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/source_bundles.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_fact_derivers.py`

- [ ] Add failing tests proving missing relocation/workplace/salary facts schedule detail.
- [ ] Add failing test proving employer geography schedules a trusted profile provider.
- [ ] Implement request-to-requirement planning with explicit providers.
- [ ] Derive allowed text-enriched facts from detail text and verify final selection.

### Task 3: Query modes, limits, and duplicate storage

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/source_bundles.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_pipeline.py`
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_independent_source_bundles.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_persistence_graph_repository.py`

- [ ] Add failing tests for native `per_query` and non-native `downstream_only` manifests.
- [ ] Prove source limits do not grow for downstream-only query variants.
- [ ] Prove duplicate observations do not create duplicate fact/evaluation snapshots.
- [ ] Implement exact modes, budgets, and semantic snapshot deduplication.

### Task 4: Agent workflow contract

**Files:**
- Modify: `plugins/job-harness/skills/job-search-workflow/SKILL.md`
- Modify: `plugins/job-harness/skills/user-briefing/SKILL.md`
- Modify: `.job-harness/briefs/2026-07-12_ai-llm-evaluation-lead/brief.md`
- Test: `plugins/job-harness/tests/v2/test_architecture_contract_docs.py`

- [ ] Add failing documentation-contract assertions for current scenarios and brief revalidation.
- [ ] Update skill instructions and remove obsolete CLI limitations from the saved brief.
- [ ] Synchronize the installable plugin version files.

### Task 5: Verification and live QA search

**Files:**
- Verify: `plugins/job-harness/tests/v2/`
- Verify: `.job-harness/v2/runs/qa-manual-remote-ru-20260713/`

- [ ] Run focused tests after every task.
- [ ] Run `python3 scripts/verify_v2.py --skip-live` and `git diff --check`.
- [ ] Run a timed full-catalog QA Manual remote Russia search.
- [ ] Inspect DB budgets, task mix, duplicate amplification, diagnostics, and report payload.
- [ ] Open the report with Playwright and audit at least 10 visible cards against source pages.
