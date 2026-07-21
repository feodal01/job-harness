# Local Search Workspace Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a distributable local macOS web application over the existing v2 search engine so a human and an agent can create, run, inspect, and revisit searches through one portable canonical Workspace.

**Architecture:** Add a `job_harness.workspace` application layer above `V2SearchApplication`, then expose it through a loopback-only FastAPI service and a compiled React frontend. Canonical Workspace JSON plus each Run's `run.sqlite` remain the source of truth; the local index, HTTP jobs, browser state, and macOS runtime files are projections or controller state. Package the backend, frontend assets, and Python runtime into a signed/notarized macOS folder started by `Start Job Harness.command`; the UI opens in the user's installed default browser.

**Tech Stack:** Python 3.12, frozen dataclasses, SQLite/WAL, existing `V2SearchApplication`, FastAPI/Pydantic v2/Uvicorn/watchfiles, build-only Node.js 22, React/TypeScript/Vite/TanStack Query, Vitest/Testing Library/jsdom/Playwright, PyInstaller one-folder distributions, macOS `codesign` and `notarytool`.

## Global Constraints

- The normative design is `docs/superpowers/specs/2026-07-21-local-search-workspace-service-design.md`.
- This is an intentional breaking contract: do not read, import, migrate, or add fallbacks for `.job-harness/briefs/` or `.job-harness/v2/runs/`.
- The canonical hierarchy is `Workspace -> SearchTrack -> BriefRevision -> Run -> Execution`; there is no product `Session` and no SearchTrack fork feature.
- A SearchTrack has at most one mutable draft; every confirmed BriefRevision and every previous Run is immutable and remains addressable.
- Runs are physically stored under the exact BriefRevision that created them.
- Every entrypoint accepts 1-20 normalized unique query formulations and rejects more than 20 without truncation.
- The agent does not know whether the service is running and never starts it; agent CLI and HTTP service call the same `WorkspaceApplication`.
- A normal new Run is allocated under one selected immutable BriefRevision and receives the caller's exact translated `SearchRequest`; `run.json` and `run.sqlite` pin that request without rewriting the business brief.
- The shared `brief_preferences_to_search_request()` mapping is authoritative; a launch request that differs from the selected revision's normalized translation is rejected before Run allocation.
- HTTP derives `actor=frontend` server-side and the agent CLI derives `actor=agent`; neither transport accepts a caller-supplied actor.
- The service calls `V2SearchApplication` directly, never a CLI subprocess and never individual scrapers.
- Engine append and resume remain available through the shared application/API, but MVP frontend exposes resume only and has no append or Cancel action.
- One service process opens one Workspace; different Runs may write concurrently, while a Run has at most one writer protected by the shared workspace lease database.
- Canonical artifacts live under the selected Workspace. Indexes, PID, port, logs, session secrets, and browser-launch state live in macOS Application Support/Cache directories.
- Active Workspaces must be local and writable; network shares, removable volumes, and known cloud-sync roots are rejected before mutation.
- The service binds to loopback only, validates Host and Origin, exchanges a one-time launch token for an `HttpOnly; SameSite=Strict` cookie, and requires CSRF tokens on mutations.
- `report.html` is download-only with sandboxing headers and is never rendered on the privileged service origin.
- The Run page opens on Results. It uses `Подходит / Второй шанс / Все`, a left source rail, grouped sections for all sources, and a single-source filter encoded in the URL.
- `Подходит` is exactly `results`; `Второй шанс` is exactly `filtered_out_results`; `Все` is their disjoint union. The UI never shows or names a relevance score.
- The runtime distribution requires no separately installed Python, `uv`, Node.js, Docker, or Homebrew and performs no first-launch dependency download.
- Frontend development/release uses Node.js 22.x (`engines >=22.12 <23` and a checked-in `.nvmrc`); Node is never part of the end-user runtime.
- Release artifacts are signed/notarized and tested separately for Apple Silicon and Intel unless a verified universal build replaces both.
- The first beta supports macOS 15+; build on the oldest supported macOS runner and revisit this declared floor before widening support.
- Keep changes focused: no unrelated v1 refactor, no compatibility comments, and no cloud/multi-user behavior.

## Implementation sequence and checkpoints

1. **Workspace foundation (Tasks 1-7):** the agent CLI can create the new layout and run the engine with nested, leased, reconcilable Runs. This is independently usable without the web service.
2. **Local backend (Tasks 8-11):** a secured loopback API can operate and observe the same Workspace, including agent-written changes. This is independently testable with API clients.
3. **Human frontend (Tasks 12-16):** the approved SearchTrack/Run UX becomes usable in a browser while preserving the exact backend contract.
4. **Distribution and release (Tasks 17-20):** integration tests, launcher, bundled runtimes, signing, and clean-account smoke tests turn the development app into a shareable macOS download.

## Specification coverage

| Approved design sections | Implementation tasks |
|---|---|
| 1-4: summary, problem, goals, non-goals | Global constraints; Tasks 1-20 |
| 5-6: concepts and hierarchy | Tasks 2-7, 10, 13-16 |
| 7: UX architecture | Tasks 12-17 |
| 8: canonical storage and atomicity | Tasks 2-6, 10-11 |
| 9: shared application and agent/frontend workflows | Tasks 6-8, 11, 14 |
| 10: local service, API, append | Tasks 8-11 |
| 11: rebuildable index and agent reflection | Task 10 |
| 12: concurrency and leases | Tasks 5-6, 9, 11 |
| 13: failure and recovery | Tasks 6, 9, 16, 18 |
| 14: launch and distribution | Tasks 18-20 |
| 15: local security | Tasks 8, 11, 17-20 |
| 16: required contract changes | Tasks 1-7, 18 |
| 17: verification strategy | Every task's RED/GREEN checks; Tasks 17 and 20 full gates |
| 18-19: UX references and decision record | Tasks 12-17 preserve the approved focused workbench model |
| 20: compatibility policy | Global no-legacy constraint; Tasks 2, 7, 18, 20 |

## File structure map

### Shared engine and Workspace package

| Path | Responsibility |
|---|---|
| `plugins/job-harness/src/job_harness/v2/contracts/search.py` | Enforce the shared 20-formulation invariant. |
| `plugins/job-harness/src/job_harness/v2/application.py` | Accept an injected workspace resource-gate path and report workflow identity as soon as executions are allocated. |
| `plugins/job-harness/src/job_harness/v2/runtime/graph_pipeline.py` | Support a provenance-only preallocated Run directory and emit the execution-start callback before network work. |
| `plugins/job-harness/src/job_harness/v2/runtime/run_layout.py` | Validate that a preallocated Run contains only `run.json` before engine initialization. |
| `plugins/job-harness/src/job_harness/workspace/models.py` | Frozen canonical Workspace, SearchTrack, draft, BriefRevision, Run, lease, and read-model types. |
| `plugins/job-harness/src/job_harness/workspace/errors.py` | Stable validation, conflict, busy, unsupported-layout, and corruption errors. |
| `plugins/job-harness/src/job_harness/workspace/layout.py` | Safe ID-to-relative-path mapping, new-layout validation, and old-layout rejection. |
| `plugins/job-harness/src/job_harness/workspace/filesystem_policy.py` | Shared writable/local-filesystem assessment enforced by both agent and service mutations. |
| `plugins/job-harness/src/job_harness/workspace/bootstrap_lock.py` | Root-scoped cross-process flock used before a Workspace ID/resource database exists. |
| `plugins/job-harness/src/job_harness/workspace/atomic_json.py` | Temp-file, fsync, atomic-file/atomic-directory writes. |
| `plugins/job-harness/src/job_harness/workspace/serialization.py` | Strict versioned JSON codecs and exact `SearchRequest` serialization. |
| `plugins/job-harness/src/job_harness/workspace/brief_markdown.py` | Deterministic human projection of a confirmed brief. |
| `plugins/job-harness/src/job_harness/workspace/repository.py` | Canonical mutations and scans; no network or HTTP behavior. |
| `plugins/job-harness/src/job_harness/workspace/leases.py` | Shared SQLite track/run leases with token, heartbeat, expiry, and loss detection. |
| `plugins/job-harness/src/job_harness/workspace/operations.py` | Shared prepared/applied/completed mutation journal recovered before any later writer. |
| `plugins/job-harness/src/job_harness/workspace/run_reader.py` | Read finalized snapshots, source provenance, diagnostics, histories, and portable artifact verification from `run.sqlite`. |
| `plugins/job-harness/src/job_harness/workspace/reconciliation.py` | Derive Run lifecycle, quality, integrity, and recoverability after crashes or external writes. |
| `plugins/job-harness/src/job_harness/workspace/application.py` | `WorkspaceApplication` commands and read models shared by CLI and service. |
| `plugins/job-harness/src/job_harness/workspace/cli.py` | JSON agent adapter for workspace, track, brief, run, append, and resume commands. |

### Local backend

| Path | Responsibility |
|---|---|
| `apps/job-harness-local/backend/pyproject.toml` | Backend/runtime dependencies and `job-harness-local` entrypoint. |
| `apps/job-harness-local/backend/src/job_harness_local/config.py` | Injected workspace/runtime/cache paths and loopback bind settings. |
| `apps/job-harness-local/backend/src/job_harness_local/api_models.py` | Pydantic request/response/error contracts for `/api/v1`. |
| `apps/job-harness-local/backend/src/job_harness_local/auth.py` | Host validation, one-time launch exchange, session cookie, CSRF, and control-token verification. |
| `apps/job-harness-local/backend/src/job_harness_local/idempotency.py` | HTTP key/command adapter over the shared crash-recoverable mutation journal. |
| `apps/job-harness-local/backend/src/job_harness_local/jobs.py` | Durable job manifests, worker queue, progress broker, startup recovery, and retention. |
| `apps/job-harness-local/backend/src/job_harness_local/index.py` | Disposable SQLite projection schema and queries. |
| `apps/job-harness-local/backend/src/job_harness_local/scanner.py` | Full canonical scan and verified projection into the index. |
| `apps/job-harness-local/backend/src/job_harness_local/watcher.py` | Debounced watch hints plus periodic correctness rescan. |
| `apps/job-harness-local/backend/src/job_harness_local/artifacts.py` | Opaque artifact IDs, containment checks, SQLite snapshot download, and safe attachment headers. |
| `apps/job-harness-local/backend/src/job_harness_local/routes/*.py` | Thin session, workspace, SearchTrack, Run, artifact, and Job HTTP adapters. |
| `apps/job-harness-local/backend/src/job_harness_local/app.py` | FastAPI factory, lifespan, middleware, routes, static assets, and SPA fallback. |

### Frontend

| Path | Responsibility |
|---|---|
| `apps/job-harness-local/frontend/src/api/` | Generated OpenAPI types, typed fetch client, SSE/polling job hook. |
| `apps/job-harness-local/frontend/src/app/` | Router, error boundary, app shell, breadcrumbs, and shared query state. |
| `apps/job-harness-local/frontend/src/pages/` | Workspace, SearchTrack, Run Results, Diagnostics, and Artifacts pages. |
| `apps/job-harness-local/frontend/src/brief/` | Complete typed BriefPreferences editor and launch-time SearchRequest translation. |
| `apps/job-harness-local/frontend/src/results/` | Result tabs, source rail/sections, request disclosure, cards, details, and per-source pagination. |
| `apps/job-harness-local/frontend/src/styles/` | Report-derived card tokens and the approved focused workbench layout. |

### Launcher, packaging, and verification

| Path | Responsibility |
|---|---|
| `apps/job-harness-local/launcher/Start Job Harness.command` | Finder/Terminal entrypoint resolved relative to its own location. |
| `apps/job-harness-local/backend/src/job_harness_local/launcher.py` | Workspace prompt/reopen, filesystem policy, single-instance control, readiness, browser open, and foreground shutdown. |
| `apps/job-harness-local/packaging/job-harness-local.spec` | PyInstaller one-folder graph, package data, compiled frontend, CA bundle, and dynamic v2 scraper modules. |
| `apps/job-harness-local/packaging/build_macos.sh` | Reproducible architecture-specific build of the frozen Python service and compiled frontend. |
| `apps/job-harness-local/packaging/sign_macos.py`, `notarize_macos.sh` | Nested Mach-O signing, archive notarization, stapling, and verification. |
| `scripts/verify_local_app.py` | Deterministic Python/frontend/integration packaging gate. |
| `.github/workflows/local-app-candidates.yml` | Intel/Apple Silicon matrix build, signing/notarization, verification, and candidate artifact upload. |
| `.github/workflows/local-app-release.yml` | Manual acceptance-input validation, candidate download, manifest/CMS signing, and final GitHub Release publication. |

---

### Task 1: Harden the v2 boundary for Workspace operation

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/search.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`
- Modify: `plugins/job-harness/src/job_harness/v2/application.py`
- Modify: `plugins/job-harness/src/job_harness/v2/cli.py`
- Create: `plugins/job-harness/src/job_harness/v2/workflow_projection.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_pipeline_models.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_pipeline.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_execution.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/graph_artifacts.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/execution_artifacts.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/run_layout.py`
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_schema.sql`
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py`
- Modify: `scripts/v2_live_e2e.py`
- Test: `plugins/job-harness/tests/v2/test_contracts_search.py`
- Test: `plugins/job-harness/tests/v2/test_application_cli.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_run_layout.py`
- Test: `plugins/job-harness/tests/v2/test_persistence_graph_repository.py`

**Interfaces:**
- Consumes: current `SearchRequest`, `V2SearchApplication`, `GraphSearchPipeline`, and `RunLayout` contracts.
- Produces: `MAX_QUERY_VARIANTS = 20`; required `V2SearchConfig.runs_dir`; `V2SearchConfig.resource_gate_path: Path | None`; `V2SearchConfig.execution_started_callback: Callable[[V2ExecutionStarted], None] | None`; required caller `operation_correlation_id` on search/append/resume; `V2SearchApplication.search(..., preallocated_run: bool = False)`; `V2ExecutionStarted` containing correlation, all workflow IDs, and `append_sequence`; shared fit-precedence workflow projection helpers; and durable `source_plans.plan_order`.

- [ ] **Step 1: Write failing SearchRequest boundary tests**

```python
def test_search_request_rejects_more_than_twenty_normalized_queries(self) -> None:
    with self.assertRaisesRegex(ValueError, "query_variants must contain at most 20 unique values"):
        SearchRequest(query_variants=tuple(f"query-{index}" for index in range(21)))

def test_search_request_applies_limit_after_normalization(self) -> None:
    request = SearchRequest(
        query_variants=tuple(f"query-{index}" for index in range(20)) + (" QUERY-0 ",),
    )
    self.assertEqual(20, len(request.query_variants))
```

- [ ] **Step 2: Verify the query tests fail**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.v2.test_contracts_search.SearchRequestTest.test_search_request_rejects_more_than_twenty_normalized_queries \
  tests.v2.test_contracts_search.SearchRequestTest.test_search_request_applies_limit_after_normalization -v
```

Expected: the first case does not raise yet.

- [ ] **Step 3: Enforce and export the normalized limit**

Use the cleaned tuple, not the raw input:

```python
MAX_QUERY_VARIANTS = 20

queries = clean_string_tuple(self.query_variants, "query_variants", allow_empty=False)
if len(queries) > MAX_QUERY_VARIANTS:
    raise ValueError(
        f"query_variants must contain at most {MAX_QUERY_VARIANTS} unique values"
    )
object.__setattr__(self, "query_variants", queries)
```

Export `MAX_QUERY_VARIANTS` from `job_harness.v2.contracts`; do not add a separate frontend/backend constant with different semantics.

- [ ] **Step 4: Write failing injected-gate, preallocation, and identity-callback tests**

Cover these exact cases:

```python
def test_config_resolves_explicit_workspace_gate(self) -> None:
    config = V2SearchConfig(
        runs_dir=Path("/workspace/.job-harness/searches/t/briefs/0001/runs"),
        resource_gate_path=Path("/workspace/.job-harness/_runtime/resource-gate.sqlite"),
    )
    self.assertEqual(
        Path("/workspace/.job-harness/_runtime/resource-gate.sqlite"),
        config.resolved_resource_gate_path,
    )

def test_preallocated_run_allows_only_workspace_provenance(self) -> None:
    paths = RunLayout(self.runs_dir).paths_for("run-1")
    paths.run_dir.mkdir(parents=True)
    (paths.run_dir / "run.json").write_text("{}\n", encoding="utf-8")
    self.assertEqual(paths, RunLayout(self.runs_dir).claim_preallocated_run("run-1"))

def test_preallocated_run_rejects_existing_engine_state(self) -> None:
    paths = RunLayout(self.runs_dir).paths_for("run-1")
    paths.run_dir.mkdir(parents=True)
    paths.database_path.touch()
    with self.assertRaisesRegex(FileExistsError, "preallocated run contains engine artifacts"):
        RunLayout(self.runs_dir).claim_preallocated_run("run-1")
```

The pipeline integration test must capture one `V2ExecutionStarted` callback and assert it fires after `run.sqlite` execution allocation but before the fixture transport receives its first request. It must prove the exact caller `operation_correlation_id` and normalized SearchRequest hash are persisted with the new workflow; resume persists the same correlation on its newly allocated resume-session/attempt record before its callback. A second case makes the callback raise and proves that the durable correlated Execution/session row remains available for reconciliation while planning and the fixture transport are never entered.

Add a three-execution finalization fixture where a vacancy filtered by the root search is later kept by discovery/enrichment. Assert `finalize_workflow()`'s returned processed payload, the rendered `report.html`, and `read_graph_processed_payload()` each contain it once in `results`, not in `filtered_out_results`, and satisfy `all identities = disjoint(results, filtered_out_results)`. Include filtered-only rows from every child execution and deterministic duplicate collapse; do not invent a new processed-results artifact file.

- [ ] **Step 5: Implement explicit Workspace hooks**

Add the following stable shapes:

```python
@dataclass(frozen=True)
class V2ExecutionStarted:
    operation_correlation_id: str
    run_id: str
    execution_id: str
    enrichment_execution_id: str
    discovered_search_execution_id: str
    append_sequence: int

@dataclass(frozen=True)
class V2SearchConfig:
    runs_dir: Path
    resource_gate_path: Path | None = None
    execution_started_callback: Callable[[V2ExecutionStarted], None] | None = None
    # existing fields remain

    @property
    def resolved_resource_gate_path(self) -> Path:
        return self.resource_gate_path or self.runs_dir / "_runtime" / "resource-gate.sqlite"
```

Require a validated opaque `operation_correlation_id` on every `search()` and `resume_execution()` call; the developer CLI generates one, while WorkspaceApplication supplies its own journal/internal operation ID. Persist it with the workflow's search intent/execution rows and with each resume session/attempt before network work. Pass `preallocated_run` from `V2SearchApplication.search()` to `GraphSearchPipeline.run()`. For a new normal run use `RunLayout.create_new_run()`; for a Workspace run use `RunLayout.claim_preallocated_run()`. `claim_preallocated_run()` accepts exactly one existing child, `run.json`, and refuses `run.sqlite`, projections, symlinks, or any unknown child. Append continues to use `existing_run()`.

Immediately after `_create_executions()` succeeds, call the configured callback with all IDs. Do not catch callback exceptions: failing to persist provenance must stop work before `_engine.plan_initial()` or any network call.

Remove the `.job-harness/v2/runs` default from both `V2SearchConfig` and the developer CLI. `V2SearchApplication` requires an explicit config, and developer-only `job-harness-v2 search`/`resume` require an explicit `--runs-dir`. Update all direct callers and tests in the same task. The Workspace application always supplies its nested brief-local directory; nothing in the product creates the obsolete top-level layout.

Update every direct call in `scripts/v2_live_e2e.py` to pass a deterministic per-scenario `operation_correlation_id`; both `--live-profile light` and the full verifier must continue through the new required engine contract.

Add required nonnegative execution-local `plan_order` to `source_plans`, pass the enumerated source-plan order through every repository insertion path, and order one execution's diagnostics by `(plan_order, source_plan_id)`. Dynamically inserted plans allocate `MAX(plan_order)+1` inside their execution; no two insertion paths guess from rowid/time. This is a direct schema change: update fixtures and all callers without a migration/fallback for old Run databases. Test that catalog/source selection order survives planning, persistence, dynamic discovery, diagnostics, and reopen.

Move the existing graph identity-claim logic into dependency-neutral `job_harness.v2.workflow_projection`, which imports no repository/runtime module and exposes `workflow_item_identity_claims(item)` plus `finalize_workflow_projection(final_items, filtered_candidates)`. `graph_execution`, `graph_artifacts`, and `persistence.graph_repository` may all import downward into that module, avoiding a persistence↔runtime cycle. `finalize_workflow()` collects filtered candidates from the search, enrichment, and discovered-search executions, merges final kept items, subtracts every identity claimed by a final kept item, and deterministically deduplicates the remaining candidates before passing them to `processed_payload()`. Update `read_graph_processed_payload()` to resolve the same three-execution workflow and call the same fit-precedence helper. Thus the engine's canonical processed payload has disjoint `results`/`filtered_out_results`; report rendering, database reconstruction, and later UI all preserve the approved `Второй шанс = filtered_out_results` invariant rather than repairing it only in the service.

- [ ] **Step 6: Verify the focused engine contract**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.v2.test_contracts_search \
  tests.v2.test_runtime_run_layout \
  tests.v2.test_runtime_graph_pipeline \
  tests.v2.test_application_cli \
  tests.v2.test_persistence_graph_repository -v
```

Expected: all selected modules pass; 21 unique formulations fail and direct developer search/resume without `--runs-dir` fails argument parsing instead of creating the obsolete layout.

- [ ] **Step 7: Commit the engine boundary**

```bash
git add plugins/job-harness/src/job_harness/v2 \
  plugins/job-harness/tests/v2/test_contracts_search.py \
  plugins/job-harness/tests/v2/test_application_cli.py \
  plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py \
  plugins/job-harness/tests/v2/test_runtime_run_layout.py \
  plugins/job-harness/tests/v2/test_persistence_graph_repository.py \
  scripts/v2_live_e2e.py
git commit -m "feat: prepare v2 engine for workspace runs"
```

### Task 2: Define canonical Workspace models, layout, and codecs

**Files:**
- Create: `plugins/job-harness/src/job_harness/workspace/__init__.py`
- Create: `plugins/job-harness/src/job_harness/workspace/models.py`
- Create: `plugins/job-harness/src/job_harness/workspace/errors.py`
- Create: `plugins/job-harness/src/job_harness/workspace/layout.py`
- Create: `plugins/job-harness/src/job_harness/workspace/filesystem_policy.py`
- Create: `plugins/job-harness/src/job_harness/workspace/bootstrap_lock.py`
- Create: `plugins/job-harness/src/job_harness/workspace/atomic_json.py`
- Create: `plugins/job-harness/src/job_harness/workspace/serialization.py`
- Create: `plugins/job-harness/tests/workspace/__init__.py`
- Create: `plugins/job-harness/tests/workspace/test_models.py`
- Create: `plugins/job-harness/tests/workspace/test_layout.py`
- Create: `plugins/job-harness/tests/workspace/test_filesystem_policy.py`
- Create: `plugins/job-harness/tests/workspace/test_bootstrap_lock.py`
- Create: `plugins/job-harness/tests/workspace/test_serialization.py`
- Modify: `scripts/verify_v2.py`

**Interfaces:**
- Consumes: `SearchRequest`, `search_request_from_json()`, `to_jsonable()`, and the approved Workspace schema version `1`.
- Produces: frozen versioned dataclasses, the authoritative pure `brief_preferences_to_search_request()`, typed errors including `BriefRequestMismatchError`, `WorkspaceLayout`, shared `WorkspaceFilesystemPolicy`, root-scoped `WorkspaceBootstrapLock`, strict `read_*`/`write_*` codecs, and atomic JSON primitives used by every later task.

- [ ] **Step 1: Write failing model and path tests**

Define tests for:

```python
def test_run_path_is_nested_under_exact_brief(self) -> None:
    layout = WorkspaceLayout(self.root)
    self.assertEqual(
        self.root / ".job-harness/searches/track-a/briefs/0002/runs/run-a",
        layout.run_dir("track-a", 2, "run-a"),
    )

def test_only_old_layout_is_rejected(self) -> None:
    (self.root / ".job-harness/v2/runs/old-run").mkdir(parents=True)
    with self.assertRaisesRegex(UnsupportedWorkspaceLayoutError, "unsupported workspace layout"):
        WorkspaceLayout.open(self.root)

def test_ids_cannot_escape_workspace(self) -> None:
    with self.assertRaisesRegex(ValueError, "invalid search_track_id"):
        WorkspaceLayout(self.root).search_dir("../outside")
```

Also round-trip all canonical records, reject unknown `schema_version`, reject unknown fields, require UTC timestamps with `Z`, and prove `SearchRequest` preserves query order. Test that `BriefPreferences.query_formulations` uses the same case-insensitive normalization/order and 1-20 limit, that `append_to_run_id` cannot appear in a brief, and that pending-confirmation plus quality/integrity fields survive strict serialization. Prove `brief_preferences_to_search_request()` maps every engine criterion exactly, preserves formulation order as `query_variants`, drops only `notes`, and always sets `append_to_run_id=None`; round-trip a pending confirmation whose confirmer and timestamp differ from the draft updater and timestamp.

Test the shared filesystem policy with injected Darwin platform/stat probes: an ordinary writable APFS path passes; a symlink root, read-only directory, `~/Library/CloudStorage`, `~/Library/Mobile Documents`, any `/Volumes` path, and `smbfs`/`nfs` filesystem fail with stable `unsupported_workspace_filesystem` before canonical mutation. Read-only assessment returns the warning without blocking inspection. On non-Darwin agent hosts only existence/directory/symlink/writability checks apply because the distributed local app is macOS-only.

Test two processes contending on `WorkspaceBootstrapLock`: only one enters the critical section, the second enters after release and rereads the winner's bytes. A killed holder releases the kernel flock; a leftover lock file is harmless.

- [ ] **Step 2: Verify the new tests fail because the package is absent**

```bash
uv --directory plugins/job-harness run python -m unittest discover \
  -s tests/workspace -p 'test_*.py' -v
```

Expected: import failure for `job_harness.workspace`.

- [ ] **Step 3: Implement focused canonical dataclasses**

Use `StrEnum` and frozen dataclasses. Required public types are:

```python
class Actor(StrEnum):
    AGENT = "agent"
    FRONTEND = "frontend"

class RunStatus(StrEnum):
    ALLOCATED = "allocated"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

class RunQuality(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"

class RunIntegrity(StrEnum):
    OK = "ok"
    CORRUPT = "corrupt"

@dataclass(frozen=True)
class BriefPreferences:
    query_formulations: tuple[str, ...]
    grades: tuple[Grade, ...] = ()
    compensation: CompensationCriterion | None = None
    published_since: date | None = None
    exclude_companies: tuple[str, ...] = ()
    exclude_text: tuple[TextExclusion, ...] = ()
    relocation: bool | None = None
    work_formats: tuple[WorkFormat, ...] = ()
    remote_scopes: tuple[str, ...] = ()
    vacancy_geographies: tuple[str, ...] = ()
    employer_geographies: tuple[str, ...] = ()
    scenarios: tuple[SearchScenario, ...] = ()
    sources: tuple[str, ...] = ()
    source_types: tuple[SourceType, ...] = ()
    notes: str = ""

@dataclass(frozen=True)
class BriefContent:
    summary: str
    preferences: BriefPreferences

@dataclass(frozen=True)
class PendingBriefConfirmation:
    confirmation_id: str
    reserved_revision_id: str
    reserved_revision_number: int
    expected_current_revision_id: str | None
    content_sha256: str
    confirmed_at: datetime
    confirmed_by: Actor

@dataclass(frozen=True)
class WorkspaceMetadata:
    workspace_id: str
    created_at: datetime
    schema_version: Literal[1] = 1

@dataclass(frozen=True)
class SearchTrack:
    search_track_id: str
    title: str
    current_brief_revision_id: str | None
    created_at: datetime
    updated_at: datetime
    last_mutation_id: str
    lifecycle: Literal["active", "archived"] = "active"
    schema_version: Literal[1] = 1

@dataclass(frozen=True)
class BriefDraft:
    search_track_id: str
    draft_version: int
    based_on_revision_id: str | None
    content: BriefContent
    updated_at: datetime
    updated_by: Actor
    last_mutation_id: str
    pending_confirmation: PendingBriefConfirmation | None = None
    schema_version: Literal[1] = 1

@dataclass(frozen=True)
class BriefRevision:
    brief_revision_id: str
    revision_number: int
    search_track_id: str
    previous_revision_id: str | None
    content: BriefContent
    confirmed_at: datetime
    confirmed_by: Actor
    confirmation_id: str
    schema_version: Literal[1] = 1

@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    search_track_id: str
    brief_revision_id: str
    brief_revision_number: int
    initiated_by: Actor
    request: SearchRequest
    status: RunStatus
    quality: RunQuality | None
    integrity: RunIntegrity
    allocated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    active_execution_id: str | None = None
    active_append_sequence: int | None = None
    finalized_execution_id: str | None = None
    finalized_append_sequence: int | None = None
    failure_code: str | None = None
    integrity_code: str | None = None
    schema_version: Literal[1] = 1
```

`BriefPreferences` is the versioned, typed human-facing criteria contract. It deliberately omits internal `append_to_run_id`; `notes` preserves business nuance that is not an engine filter. Its query formulations use the same `clean_string_tuple` and 20-item boundary as SearchRequest. The shared pure `brief_preferences_to_search_request()` copies every engine criterion, maps `query_formulations -> query_variants`, drops only notes, and always sets `append_to_run_id=None`. A frontend/agent submits that normalized translation at Run launch; the application rejects any mismatch before allocation. The exact request used by each Run is pinned only in `run.json` and `run.sqlite`, so a future translation change does not rewrite the immutable business brief. `status`, `quality`, and `integrity` are separate axes: degraded coverage is not a failure, and corrupt provenance is not encoded as a lifecycle state.

- [ ] **Step 4: Implement safe layout and atomic primitives**

`WorkspaceLayout` resolves the user root once, rejects symlink escapes, validates IDs with `^[a-z0-9][a-z0-9_-]{0,63}$`, formats revision directories with four decimal digits, and exposes methods for every canonical path and `_runtime/jobs`.

`WorkspaceLayout.open()` accepts only `.job-harness/workspace.json` schema `1`. If it is absent while `.job-harness/briefs` or `.job-harness/v2/runs` exists, raise `UnsupportedWorkspaceLayoutError` without modifying anything. A valid new layout ignores untouched old directories.

`WorkspaceFilesystemPolicy.assess(root) -> WorkspaceFilesystemAssessment` resolves the root, classifies it without writing, and returns `supported`, stable `code`, and human message. `require_supported_for_mutation(root) -> Path` applies the same classification, then performs and cleans up one exclusive temporary write probe. On Darwin, the production probe invokes only absolute `/usr/bin/stat -f %T <resolved-root>` and allows local `apfs`/`hfs`; it also rejects the known cloud/removable path roots above before probing. This module lives in `job_harness.workspace`, has no backend import, and is the only active-Workspace policy used by either transport.

`WorkspaceBootstrapLock(root)` creates only `.job-harness/_runtime/bootstrap.lock` with private modes, opens it without following symlinks, and holds `fcntl.flock(LOCK_EX)` across reread plus first publication of `workspace.json`. Its identity is the resolved root path, not a not-yet-created Workspace ID. It is used only for bootstrap; normal writers use Task 5 leases.

Implement these exact callable contracts:

```text
atomic_write_json(path: Path, payload: JsonObject) -> None
atomic_publish_directory(target: Path, populate: Callable[[Path], None]) -> None
```

Both write into the same parent filesystem, fsync files and the parent directory, use `os.replace`, clean their own hidden temp path on failure, and never follow a symlink target.

- [ ] **Step 5: Implement strict JSON codecs**

Provide one `*_to_json()` and `*_from_json()` pair per canonical dataclass. Encode timestamps as UTC ISO-8601 ending in `Z`, enums as values, and `SearchRequest` through the existing strict serializer/deserializer. Every reader checks the exact field set and `record_type`; do not preserve unknown keys.

- [ ] **Step 6: Extend deterministic verification to the sibling package**

Add `src/job_harness/workspace` and `tests/workspace` to v2 Ruff and mypy commands, and add a `workspace contract tests` unittest-discovery check before application tests in `scripts/verify_v2.py`.

- [ ] **Step 7: Verify and commit the canonical contract**

```bash
uv --directory plugins/job-harness run python -m unittest discover \
  -s tests/workspace -p 'test_*.py' -v
uv --directory plugins/job-harness run ruff check src/job_harness/workspace tests/workspace
uv --directory plugins/job-harness run mypy src/job_harness/workspace tests/workspace \
  --disallow-any-generics --disallow-untyped-defs --no-implicit-optional --strict-equality
git add plugins/job-harness/src/job_harness/workspace \
  plugins/job-harness/tests/workspace scripts/verify_v2.py
git commit -m "feat: define canonical search workspace contract"
```

Expected: all commands exit `0`.

### Task 3: Implement SearchTrack, draft, and immutable BriefRevision storage

**Files:**
- Create: `plugins/job-harness/src/job_harness/workspace/brief_markdown.py`
- Create: `plugins/job-harness/src/job_harness/workspace/repository.py`
- Create: `plugins/job-harness/tests/workspace/test_repository_briefs.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/__init__.py`

**Interfaces:**
- Consumes: Task 2 models/layout/codecs.
- Produces: `WorkspaceRepository.initialize()`, `create_search_track()`, `put_draft()`, `confirm_draft()`, `list_search_tracks()`, `read_search_track()`, and `list_brief_revisions()`.

- [ ] **Step 1: Write failing lifecycle tests**

Cover one complete evolution:

```python
workspace = repository.initialize()
track = repository.create_search_track(
    "Senior QA",
    search_track_id="search_track_one",
    mutation_id="mutation_track_one",
    now=NOW,
)
draft_1 = repository.put_draft(
    track.search_track_id,
    BriefContent(
        summary="Worldwide remote",
        preferences=BriefPreferences(
            query_formulations=("QA", "SDET"),
            work_formats=(WorkFormat.REMOTE,),
            remote_scopes=("global",),
        ),
    ),
    actor=Actor.AGENT,
    mutation_id="mutation_draft_one",
    expected_draft_version=None,
    now=NOW,
)
brief_1 = repository.confirm_draft(
    track.search_track_id,
    actor=Actor.AGENT,
    mutation_id="mutation_confirm_one",
    expected_draft_version=draft_1.draft_version,
    now=LATER,
)
draft_2 = repository.put_draft(
    track.search_track_id,
    BriefContent(
        summary="Worldwide remote, senior",
        preferences=BriefPreferences(
            query_formulations=("Senior QA",),
            grades=(Grade.SENIOR,),
            work_formats=(WorkFormat.REMOTE,),
            remote_scopes=("global",),
        ),
    ),
    actor=Actor.FRONTEND,
    mutation_id="mutation_draft_two",
    expected_draft_version=None,
    now=LATEST,
)
brief_2 = repository.confirm_draft(
    track.search_track_id,
    actor=Actor.FRONTEND,
    mutation_id="mutation_confirm_two",
    expected_draft_version=draft_2.draft_version,
    now=LATEST,
)

self.assertEqual(1, brief_1.revision_number)
self.assertEqual(2, brief_2.revision_number)
self.assertEqual(brief_1.brief_revision_id, brief_2.previous_revision_id)
self.assertEqual(brief_2.brief_revision_id, repository.read_search_track(track.search_track_id).current_brief_revision_id)
self.assertEqual(brief_1, repository.read_brief_revision(track.search_track_id, brief_1.brief_revision_id))
```

Add tests that a stale `expected_draft_version` raises `DraftConflictError`, a second confirmation of the same draft fails visibly, and no operation rewrites `brief.json` or `brief.md` for revision 1. Start two processes against one empty root behind a barrier and assert both `initialize()` calls return the same Workspace ID, one byte-identical `workspace.json` exists, and no loser overwrites it.

- [ ] **Step 2: Verify RED**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.workspace.test_repository_briefs -v
```

Expected: import failure for `WorkspaceRepository`.

- [ ] **Step 3: Implement initialization and SearchTrack storage**

`initialize()` acquires Task 2's root-scoped `WorkspaceBootstrapLock`, rereads layout state while holding it, creates `.job-harness/workspace.json` once with a generated `workspace_<token>` ID only if still absent, and returns the winner's existing record on repeat. ID/time generation happens after the locked reread. It refuses the old-only layout from Task 2; a crash that left only the private bootstrap lock/runtime directory is safely retryable.

`create_search_track()` accepts an optional preallocated `search_track_id` and required `mutation_id`, otherwise generates both, creates the SearchTrack directory atomically, writes `search.json`, and starts without a draft or current revision. Titles are stripped, non-empty, and at most 200 characters. Repeating the same IDs plus content returns the same record; an ID/content mismatch is corruption.

- [ ] **Step 4: Implement optimistic draft replacement**

`put_draft()` reads the current `draft.json` if present and accepts a required `mutation_id`. `expected_draft_version=None` means "create a draft only when none exists"; otherwise it must equal the current version. The saved version increments by one and is written atomically with `last_mutation_id`. Repeating the same mutation ID/content returns that exact version; reusing it for different content is corruption. Editing a confirmed brief sets `based_on_revision_id` to the current revision unless the caller explicitly bases the draft on an older valid revision.

- [ ] **Step 5: Publish a confirmed revision atomically**

`confirm_draft()` accepts a required `mutation_id`, derives stable reserved confirmation/revision IDs from it, uses `draft.json.pending_confirmation` as a durable intent journal, and performs this order while leaving the prior revision untouched:

1. verify the draft version under the repository primitive's explicit caller-serialized precondition; Task 6 satisfies that precondition by acquiring the Task 5 SearchTrack writer lease before calling it;
2. reserve a confirmation ID, BriefRevision ID, and next monotonic revision number;
3. atomically persist those values, the expected current pointer, content hash, `confirmed_at`, and `confirmed_by` in `draft.json.pending_confirmation`;
4. populate a hidden sibling directory with matching `brief.json` and deterministic `brief.md`;
5. fsync and atomically rename it to `briefs/NNNN`;
6. atomically update `search.json.current_brief_revision_id`, `last_mutation_id`, and `updated_at`;
7. remove `draft.json` only after the current pointer is durable.

On open/reconciliation, an exact pending confirmation is deterministically completed from its journal: verify the expected prior pointer, IDs, revision number, content hash, confirmer, and timestamp; publish any missing directory using those journaled confirmation facts; advance the pointer; then remove the draft. Recovery must never substitute `draft.updated_by`, `draft.updated_at`, the recovery actor, or the recovery clock. A mismatch is surfaced as `CorruptWorkspaceError`; no ordinary brief listing or Run launch exposes the incomplete revision. Add crash-injection tests after steps 3-7, including a crash immediately after step 3 before a brief directory exists, and prove every retry yields exactly one visible BriefRevision with the originally confirmed actor/time and no guessed link.

- [ ] **Step 6: Verify and commit brief evolution**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.workspace.test_repository_briefs -v
git add plugins/job-harness/src/job_harness/workspace \
  plugins/job-harness/tests/workspace/test_repository_briefs.py
git commit -m "feat: persist evolving search briefs"
```

Expected: tests pass and the revision-1 byte hashes remain unchanged after revision 2 is confirmed.

### Task 4: Allocate nested Runs and project durable v2 snapshots

**Files:**
- Create: `plugins/job-harness/src/job_harness/workspace/run_reader.py`
- Create: `plugins/job-harness/tests/workspace/test_repository_runs.py`
- Create: `plugins/job-harness/tests/workspace/test_run_reader.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/models.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/repository.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/serialization.py`

**Interfaces:**
- Consumes: a confirmed BriefRevision and existing v2 `run.sqlite` schema.
- Produces: `allocate_run()`, `update_run()`, `find_run()`, `find_execution()`, `RunSnapshotReader.read_results()`, `read_sources()`, `read_diagnostics()`, `read_execution_history()`, and verified opaque artifact descriptors.

- [ ] **Step 1: Write failing Run provenance tests**

```python
run_1 = repository.allocate_run(
    track_id=track.search_track_id,
    brief_revision_id=brief_1.brief_revision_id,
    request=SearchRequest(query_variants=("QA", "SDET")),
    actor=Actor.AGENT,
    now=NOW,
)
run_2 = repository.allocate_run(
    track_id=track.search_track_id,
    brief_revision_id=brief_1.brief_revision_id,
    request=SearchRequest(query_variants=("QA", "SDET")),
    actor=Actor.FRONTEND,
    now=LATER,
)

self.assertNotEqual(run_1.run_id, run_2.run_id)
self.assertTrue(layout.run_metadata_path(track.search_track_id, 1, run_1.run_id).is_file())
self.assertEqual(brief_2.brief_revision_id, repository.read_search_track(track.search_track_id).current_brief_revision_id)
```

Also assert allocation rejects an unknown/mismatched revision, rejects a request that differs from the old revision's `BriefPreferences`, rejects a request with `append_to_run_id`, creates no engine file, and never advances the SearchTrack's current brief when rerunning an old revision.

- [ ] **Step 2: Implement Run allocation and exact lookup**

`allocate_run()` resolves the immutable revision and requires the caller request to equal `brief_preferences_to_search_request(revision.content.preferences)` after strict domain parsing. A mismatch raises `BriefRequestMismatchError` before a Run directory is allocated. It then accepts an optional preallocated `run_id`, creates its Run directory, and atomically writes `run.json` with `status=allocated`, `quality=None`, and `integrity=ok` before returning. It does not invoke the engine. Repeating the same preallocated ID plus provenance/request returns the same Run; a mismatch against an already allocated Run is corruption.

`find_run(run_id)` and `find_execution(execution_id)` scan only canonical new-layout metadata and exact brief-local databases. Cache acceleration belongs to the backend index; correctness cannot depend on it. Duplicate IDs or metadata/path disagreement raise `CorruptWorkspaceError`.

- [ ] **Step 3: Write failing finalized-snapshot tests from a fixture database**

Build a small graph fixture using `SqliteGraphRepository`, or reuse the deterministic application fixture. Sequence 0 must include one vacancy that exists only in the discovered-search child execution; then append sequence 1 and assert sequence 0 remains reconstructable. Assert:

```python
snapshot = RunSnapshotReader(database_path).read_results(
    category=ResultCategory.ALL,
    source_id=None,
    append_sequence=0,
)
self.assertEqual(
    snapshot.counts.all,
    snapshot.counts.fit + snapshot.counts.second_chance,
)
self.assertNotIn("relevanceScore", snapshot.items[0].display_fields)
self.assertEqual(0, snapshot.append_sequence)
```

Source tests must preserve persisted `source_plans.plan_order`, assign each canonical item to one `display_source_id`, expose additional `sourceVariants` as `also_found_in`, expose source-plan query formulations as `origin_queries`, retain zero/error plans only in diagnostics, include the discovered-only vacancy, and return historical append sequence 0 even after sequence 1 exists. Build one deterministic workflow total order using `(execution_kind_rank, plan_order, source_plan_id)` with ranks `search=0`, `enrichment=1`, `discovered_search=2`; the first occurrence of each `source_id` wins and is remapped to dense `display_order=0..N-1`, while later plans for that source aggregate into it. Add a fixture where root and discovered executions both have `plan_order=0` and prove stable order after reopen. `read_diagnostics()` returns every planned source plus its ordered per-query parser attempts, attempt numbers, outcomes, row counts, limits, timestamps, and sanitized failures; these facts never leak into the normal Results projection.

Add a three-execution fixture where a root `filtered_out_results` identity later appears as kept in enrichment/discovered child results. The finalized projection gives `fit` precedence, subtracts every final-fit identity from second-chance candidates, and returns that vacancy once as `fit`; its one category-independent `result_id` remains unambiguous for the detail route. Also deduplicate repeated filtered identities deterministically and prove `all = fit + second_chance` after subtraction.

- [ ] **Step 4: Implement read-only snapshot projection**

Use read-only SQLite connections with `PRAGMA query_only=ON`; never copy a live WAL database for ordinary reads. `read_results()` resolves the search, enrichment, and discovered-search execution IDs for one exact workflow identity and `append_sequence`, then reconstructs the finalized combined projection from canonical database rows rather than fixed-name JSON files. It calls Task 1's same workflow fit-precedence helper: build `fit` from the verified final combined kept projection, collect filtered candidates from all three executions, subtract all identities present in `fit`, and deterministically deduplicate the remainder before mapping engine `filtered_out_results` to `second_chance`. This exactly reproduces the canonical processed payload/report contract, handles a child execution rescuing a root-filtered vacancy, and guarantees disjoint categories. It includes enrichment/discovery-only items according to the engine's verified merge rules and strips internal `relevanceScore` from the API display object without changing stored artifacts.

Define:

```python
class ResultCategory(StrEnum):
    FIT = "fit"
    SECOND_CHANCE = "second_chance"
    ALL = "all"

@dataclass(frozen=True)
class ResultCounts:
    fit: int
    second_chance: int
    all: int

@dataclass(frozen=True)
class ResultItem:
    result_id: str
    category: Literal["fit", "second_chance"]
    display_source_id: str
    also_found_in: tuple[str, ...]
    origin_queries: tuple[str, ...]
    decision_reasons: tuple[str, ...]
    external_url: str | None
    display_fields: JsonObject
```

Derive `result_id` from `(run_id, append_sequence, canonical vacancy URL or stable listing identity)`. Project `external_url` only when the parsed absolute URL uses `http` or `https`, has a hostname, and has no username/password; otherwise set it to `None` and remove the raw URL field from `display_fields`. Use the canonical representative's `sourceId` for grouping; never duplicate a card into each `sourceVariant`.

- [ ] **Step 5: Verify portable artifacts**

Read engine artifact names, hashes, sizes, execution IDs, append sequences, and status from `execution_artifacts`. Resolve each supported artifact to its fixed relative Run path and verify containment plus hash/size. Do not use the receipt's absolute path as identity. Fixed projection files may be overwritten by a later append: an older execution descriptor remains historical provenance but is downloadable only while the current relative file still matches that execution's recorded hash/size. Missing, expected-only, overwritten, hash-mismatched, or symlinked artifacts are diagnostics and are not marked downloadable; none of these conditions prevents reconstructing the historical result snapshot from `run.sqlite`.

`run.sqlite` is not an `execution_artifacts` row. Expose it later as one synthetic Run-scoped descriptor with an opaque ID derived from `(run_id, "run_database")`, `execution_id=None`, `append_sequence=None`, and label semantics `run_database_all_sequences`; never invent an execution receipt for it. The download adapter creates and verifies a consistent SQLite online-backup snapshot on demand.

- [ ] **Step 6: Verify and commit Run read models**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.workspace.test_repository_runs \
  tests.workspace.test_run_reader -v
git add plugins/job-harness/src/job_harness/workspace \
  plugins/job-harness/tests/workspace/test_repository_runs.py \
  plugins/job-harness/tests/workspace/test_run_reader.py
git commit -m "feat: nest runs under immutable briefs"
```

Expected: tests pass, including historical append reads and artifact tamper detection.

### Task 5: Add shared writer leases and the mutation journal

**Files:**
- Create: `plugins/job-harness/src/job_harness/workspace/leases.py`
- Create: `plugins/job-harness/src/job_harness/workspace/operations.py`
- Create: `plugins/job-harness/tests/workspace/test_leases.py`
- Create: `plugins/job-harness/tests/workspace/test_operations.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/errors.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/layout.py`

**Interfaces:**
- Consumes: `.job-harness/_runtime/resource-gate.sqlite` from `WorkspaceLayout`.
- Produces: `WorkspaceLeaseManager.workspace_writer()`, `track_writer()`, explicit transferable `RunLeaseGuard`, and `WorkspaceOperationJournal`, all shared by every process.

- [ ] **Step 1: Write failing atomic-acquire and stale-recovery tests**

Use an injected clock and cover:

```python
first = manager_a.try_acquire("run:run-a", now=100.0)
self.assertIsNotNone(first)
self.assertIsNone(manager_b.try_acquire("run:run-a", now=101.0))
renewed = manager_a.renew(first, now=105.0)
self.assertGreater(renewed.lease_until, first.lease_until)
recovered = manager_b.try_acquire("run:run-a", now=renewed.lease_until + 0.1)
self.assertIsNotNone(recovered)
with self.assertRaises(LeaseLostError):
    manager_a.renew(renewed, now=recovered.acquired_at + 0.1)
```

Add a concurrency test proving `run:run-a` and `run:run-b` can both be held, a track test proving two simultaneous confirmations cannot both enter `track:<track_id>`, and a Workspace test proving SearchTrack ID reservation is serialized by `workspace:<workspace_id>` before a track exists.

Add a fake-clock recovery-observation test: a second process sees an unexpired lease from a crashed owner, does not declare busy immediately, advances to its observed expiry, and acquires only when the token/heartbeat remained unchanged; a control case renews before expiry and must be classified as a live external owner instead of being stolen.

Run two independent SQLite connections behind a barrier against `WorkspaceOperationJournal.reserve()`: identical hashed key plus identical normalized command must return the same one operation/IDs to both callers; identical key plus different commands must commit exactly one reservation and give the loser typed `IdempotencyConflict`, never a raw SQLite uniqueness/lock error. Assert the table contains one row and no canonical target was touched.

- [ ] **Step 2: Verify RED**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.workspace.test_leases tests.workspace.test_operations -v
```

Expected: import failure for `WorkspaceLeaseManager`.

- [ ] **Step 3: Implement one generic lease table in the shared database**

Create this table in the same SQLite file as the existing source resource gate:

```sql
CREATE TABLE IF NOT EXISTS workspace_writer_leases (
    lease_key TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    heartbeat_at REAL NOT NULL,
    lease_until REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_operations (
    operation_id TEXT PRIMARY KEY,
    idempotency_key_sha256 TEXT UNIQUE,
    target_lease_key TEXT NOT NULL,
    recovery_owner TEXT NOT NULL CHECK (recovery_owner IN ('workspace', 'job_controller')),
    operation_kind TEXT NOT NULL,
    command_sha256 TEXT NOT NULL,
    command_json TEXT NOT NULL,
    reserved_ids_json TEXT NOT NULL,
    prepared_facts_json TEXT,
    response_status INTEGER,
    response_json TEXT,
    state TEXT NOT NULL CHECK (state IN ('reserved', 'prepared', 'applied', 'completed')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
```

`try_acquire()` uses `BEGIN IMMEDIATE` and `INSERT ... ON CONFLICT DO UPDATE ... WHERE lease_until <= :now`. `renew()` updates only when key, owner, token, and an unexpired lease all match; zero changed rows raises `LeaseLostError`. `release()` uses the same ownership predicate and never deletes another owner's recovered lease.

`reserve()` may atomically claim a globally unique hashed HTTP key and store the normalized command hash, deterministic object IDs/reservation timestamp, target lease key, and recovery owner without touching canonical files or guessing mutable target facts. Same key/command returns that reservation; same key/different command conflicts. `prepare(lease, prepared_facts_json)` runs only while holding `target_lease_key`, recovers any older prepared/applied operation for that target, derives facts that require the lease (for example draft version, previous revision pointer, and next revision number), durably stores them, and transitions `reserved -> prepared` in the same SQLite transaction. Recovery reuses those prepared facts instead of rereading a newer target. `mark_applied()` and `complete(response_status, response_json)` are monotonic conditional updates; completion persists the exact final response, which may be success or a stable conflict discovered during pre-response recovery. Every later retry replays those final columns byte-for-byte at the HTTP representation boundary. `pending_for_target(recovery_owner="workspace")` lets `WorkspaceApplication` recover a prior crashed synchronous writer before allowing a new writer under the same lease. Async acceptance rows use `recovery_owner="job_controller"`; only JobController may recover them, and an incomplete pre-`202` row never blocks a later external writer after its live Run lease expires. The journal keeps completed rows indefinitely in MVP and never stores a raw HTTP idempotency key.

Use exact public defaults:

```python
LEASE_SECONDS = 30.0
HEARTBEAT_SECONDS = 10.0

@dataclass(frozen=True)
class WorkspaceLease:
    lease_key: str
    owner_id: str
    token: str
    acquired_at: float
    heartbeat_at: float
    lease_until: float
```

Expose a read-only lease observation carrying key/owner/token/heartbeat/expiry. Normal product acquisition remains non-waiting. Startup Job recovery alone may defer one decision until the exact observed expiry: it rereads under the lease transaction, may acquire only if the same token has not renewed and is now expired, and treats a changed token or advanced heartbeat/expiry as a genuinely live writer. Tests inject clock/sleeper functions; no unit test sleeps in real time.

- [ ] **Step 4: Implement sync track scopes and a transferable Run guard**

```text
@contextmanager workspace_writer() -> Iterator[WorkspaceLease]
@contextmanager track_writer(search_track_id: str) -> Iterator[WorkspaceLease]

class RunLeaseGuard:
    @classmethod
    async acquire(manager: WorkspaceLeaseManager, run_id: str) -> RunLeaseGuard
    def transfer_to(task: asyncio.Task[object]) -> None
    def assert_owned() -> None
    async def close() -> None
```

All acquisition is non-waiting: a live conflict raises `WorkspaceBusyError`, `TrackBusyError`, or `RunBusyError` immediately. `RunLeaseGuard.acquire()` starts an independent heartbeat task that renews every 10 seconds through `asyncio.to_thread`; it is not an async context manager tied to the reserving HTTP task. The opaque guard may exist briefly with no cancellation target while queued, then `transfer_to()` binds it exactly once to the engine worker task. If ownership is lost before transfer, `assert_owned()`/transfer fails with `LeaseLostError`; if lost after transfer, the heartbeat cancels that worker task, never the handler that created the reservation. `close()` is idempotent, stops the heartbeat, and conditionally releases only its own token. Tests transfer a guard from one completed creator task to a distinct worker task, prove renewals continue between them, prove loss cancels only the worker, and prove every abort/normal/error path closes the guard.

- [ ] **Step 5: Verify and commit shared leases**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.workspace.test_leases tests.workspace.test_operations -v
git add plugins/job-harness/src/job_harness/workspace/leases.py \
  plugins/job-harness/src/job_harness/workspace/operations.py \
  plugins/job-harness/src/job_harness/workspace/errors.py \
  plugins/job-harness/src/job_harness/workspace/layout.py \
  plugins/job-harness/tests/workspace/test_leases.py \
  plugins/job-harness/tests/workspace/test_operations.py
git commit -m "feat: serialize workspace writers"
```

Expected: tests pass without sleeping in real time.

### Task 6: Build `WorkspaceApplication`, append/resume, and crash reconciliation

**Files:**
- Create: `plugins/job-harness/src/job_harness/workspace/reconciliation.py`
- Create: `plugins/job-harness/src/job_harness/workspace/application.py`
- Create: `plugins/job-harness/tests/workspace/_support.py`
- Create: `plugins/job-harness/tests/workspace/test_application.py`
- Create: `plugins/job-harness/tests/workspace/test_reconciliation.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/repository.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/run_reader.py`
- Modify: `plugins/job-harness/src/job_harness/workspace/__init__.py`

**Interfaces:**
- Consumes: Tasks 1-5, shared `WorkspaceFilesystemPolicy`, and `V2SearchApplication` only through an injected `EngineFactory`.
- Produces: the sole product command boundary used later by agent CLI and HTTP service.

```text
@dataclass(frozen=True)
class RunCommandResult:
    run: RunMetadata
    engine_execution: V2SearchExecution

class RunCommandReservation:
    # Opaque, process-local object owning one live Run lease and canonical command.
    run: RunMetadata
    command_kind: Literal["start", "append", "resume"]
    operation_correlation_id: str
    normalized_request_sha256: str

@dataclass(frozen=True)
class WorkspaceExecutionActive:
    operation_correlation_id: str
    run_id: str
    execution_id: str
    enrichment_execution_id: str
    discovered_search_execution_id: str
    append_sequence: int
    command_kind: Literal["start", "append", "resume"]

@dataclass(frozen=True)
class WorkspaceExecutionOutcome:
    run: RunMetadata
    engine_execution: V2SearchExecution | None
    failure_code: str | None
    interrupted: bool
    lease_owned: bool

BeforeRunLeaseRelease = Callable[[WorkspaceExecutionOutcome], Awaitable[None]]
AfterRunLeaseLost = Callable[[WorkspaceExecutionOutcome], Awaitable[None]]

class WorkspaceApplication:
    initialize() -> WorkspaceMetadata
    list_search_tracks() -> tuple[SearchTrack, ...]
    read_search_track(track_id: str) -> SearchTrack
    read_draft(track_id: str) -> BriefDraft | None
    list_brief_revisions(track_id: str) -> tuple[BriefRevision, ...]
    read_brief_revision(
        track_id: str,
        brief_revision_id: str,
    ) -> BriefRevision
    translate_brief_revision(
        track_id: str,
        brief_revision_id: str,
    ) -> SearchRequest
    read_run(run_id: str) -> RunMetadata
    create_search_track(
        title: str,
        *,
        mutation_id: str | None = None,
        search_track_id: str | None = None,
    ) -> SearchTrack
    put_draft(
        self,
        track_id: str,
        content: BriefContent,
        *,
        actor: Actor,
        expected_draft_version: int | None,
        mutation_id: str | None = None,
    ) -> BriefDraft
    confirm_draft(
        self,
        track_id: str,
        *,
        actor: Actor,
        expected_draft_version: int,
        mutation_id: str | None = None,
    ) -> BriefRevision
    async reserve_start_run(
        track_id: str,
        brief_revision_id: str,
        request: SearchRequest,
        *,
        actor: Actor,
        run_id: str | None = None,
        operation_id: str | None = None,
    ) -> RunCommandReservation
    async reserve_append_run(
        run_id: str,
        request: SearchRequest,
        *,
        expected_append_sequence: int,
        actor: Actor,
        operation_id: str | None = None,
    ) -> RunCommandReservation
    async reserve_resume_execution(
        execution_id: str,
        *,
        actor: Actor,
        operation_id: str | None = None,
    ) -> RunCommandReservation
    async execute_reserved(
        reservation: RunCommandReservation,
        *,
        progress_callback: Callable[[GraphSearchProgress], None] | None = None,
        execution_active_callback: Callable[[WorkspaceExecutionActive], None] | None = None,
        before_release_callback: BeforeRunLeaseRelease | None = None,
        lease_lost_callback: AfterRunLeaseLost | None = None,
    ) -> RunCommandResult
    async abort_reserved(
        reservation: RunCommandReservation,
        *,
        failure_code: str,
    ) -> RunMetadata
    async release_reserved_for_recovery(
        reservation: RunCommandReservation,
    ) -> RunMetadata
    async start_run(
        self,
        track_id: str,
        brief_revision_id: str,
        request: SearchRequest,
        *,
        actor: Actor,
        progress_callback: Callable[[GraphSearchProgress], None] | None = None,
    ) -> RunCommandResult
    async append_run(
        self,
        run_id: str,
        request: SearchRequest,
        *,
        expected_append_sequence: int,
        actor: Actor,
        progress_callback: Callable[[GraphSearchProgress], None] | None = None,
    ) -> RunCommandResult
    async resume_execution(
        self,
        execution_id: str,
        *,
        actor: Actor,
        progress_callback: Callable[[GraphSearchProgress], None] | None = None,
    ) -> RunCommandResult
    async reconcile(
        *,
        protected_run_ids: frozenset[str] = frozenset(),
        track_ids: frozenset[str] | None = None,
        run_ids: frozenset[str] | None = None,
    ) -> WorkspaceSnapshot
```

- [ ] **Step 1: Write failing application tests with a fake engine**

The fake engine records `runs_dir`, `resource_gate_path`, request, `run_id`, `preallocated_run`, and `operation_correlation_id`, calls the execution-start callback, and returns a deterministic `V2SearchExecution`.

Tests must prove:

- before any Workspace/SearchTrack/Run writer applies a new command, it completes an older prepared/applied journal row for that lease key and stores/replays that operation's exact final response; a later agent command can never overwrite the only `last_mutation_id` evidence first;
- two concurrent `WorkspaceApplication.confirm_draft()` calls serialize through `track_writer()` before the Task 3 repository primitive; exactly one advances the draft and the other receives a typed conflict without publishing a second revision;
- read methods expose exact tracks/drafts/revisions/Runs without transport DTOs, and `translate_brief_revision()` returns exactly `brief_preferences_to_search_request(read_brief_revision(...).content.preferences)` without mutation;
- `run.json` exists before the fake engine begins;
- the engine receives the selected BriefRevision's `runs` directory and the single workspace gate path;
- all 20 formulations, scenarios, source IDs, and source types in the caller's translated request reach the engine unchanged and are pinned to the selected revision in `run.json`;
- the Workspace journal/internal operation ID and normalized request hash reach engine persistence unchanged and return in the activation event for start, append, and resume;
- a caller request that differs from `brief_preferences_to_search_request()` in any field raises `BriefRequestMismatchError` before `run.json` or an engine file exists;
- `reserve_start_run()` owns the Run lease before publishing `run.json`, may use a preallocated Run ID, and returns that same ID before engine work;
- handing a reservation from a completed creator task to `execute_reserved()` in a distinct worker task transfers the existing guard and does not reacquire the lease;
- a barrier inside `before_release_callback` proves the same Run remains busy to both another service reservation and an agent command until the first controller has durably terminalized its Job, while a different Run can proceed;
- `abort_reserved()` before engine start releases the guard on every path and leaves a newly allocated Run visibly interrupted/non-resumable without changing an existing append/resume target;
- `release_reserved_for_recovery()` consumes/closes a pre-execution guard without changing canonical Run/Execution status, so only a caller that has already durably persisted a queued Job manifest can hand recovery to a future reservation;
- start, append, and resume atomically set `status=running` plus `active_execution_id`/`active_append_sequence` before network work, preserve the prior `finalized_append_sequence`, then emit `WorkspaceExecutionActive`; callback failure stops network work and changes the Run to interrupted/recoverable while preserving the durable Execution identity;
- a successful result pins execution ID, append sequence, quality, and completed timestamps;
- an engine exception leaves the same Run as failed; cancellation while the guard is still owned writes interrupted, but detected lease loss performs no further canonical/index write and leaves Run repair to the current owner or lease-aware reconciliation;
- running an old revision creates a new Run without changing the current brief;
- no code shells out to `job-harness-v2`.

- [ ] **Step 2: Implement an explicit engine factory**

```python
EngineFactory = Callable[
    [Path, Path, Callable[[V2ExecutionStarted], None], Callable[[GraphSearchProgress], None] | None],
    V2SearchApplication,
]

def default_engine_factory(
    runs_dir: Path,
    resource_gate_path: Path,
    started: Callable[[V2ExecutionStarted], None],
    progress: Callable[[GraphSearchProgress], None] | None,
) -> V2SearchApplication:
    return V2SearchApplication(
        config=V2SearchConfig(
            runs_dir=runs_dir,
            resource_gate_path=resource_gate_path,
            execution_started_callback=started,
            progress_callback=progress,
        )
    )
```

The dependency direction test must reject imports of `job_harness.workspace`, backend modules, or frontend concepts from `job_harness.v2`.

- [ ] **Step 3: Implement new Run execution**

`reserve_start_run()` resolves the exact immutable revision and compares the strictly parsed caller request to `brief_preferences_to_search_request(revision.content.preferences)`. Any field mismatch raises `BriefRequestMismatchError` before Run ID allocation, lease acquisition, or filesystem mutation. On equality it acquires a transferable `RunLeaseGuard`, writes that request into `run.json`, and returns an opaque live reservation containing the guard. `execute_reserved()` transfers the guard to its current worker task, verifies ownership, constructs the engine with the brief-local root, and calls:

```python
await engine.search(
    reservation.run.request,
    run_id=reservation.run.run_id,
    preallocated_run=True,
    operation_correlation_id=reservation.operation_correlation_id,
)
```

For start/append, the internal `V2ExecutionStarted` callback first atomically changes the Run to running, sets `active_execution_id`/`active_append_sequence`, preserves `finalized_append_sequence`, then invokes the optional external `execution_active_callback` with all workflow IDs. If that external callback raises, the application atomically clears the active fields, marks the Run interrupted/recoverable with the just-created Execution identity, and rethrows a typed pre-network activation interruption; planning and network work remain untouched. For resume, resolve the existing workflow IDs and perform/emit the same active transition immediately before calling the engine. This makes agent operations watcher-visible even without a Job and lets the controller durably pin Job execution identity while running. A hard crash between Execution creation, the Run active write, and the external callback is handled by the same durable-evidence rule during startup reconciliation rather than by pretending that the Execution did not exist.

On verified completion, take `execution_quality` from the receipt, set persistent `finalized_execution_id` and `finalized_append_sequence` to the completed workflow, clear both active fields, and atomically update status/quality/timestamps. A later append preserves the previous finalized pair while active and replaces both only after verified completion. On failure/interruption clear active fields but retain the last finalized pair; recoverability and any newer interrupted execution come from durable execution history, never a field falsely labeled active.

After persisting either canonical success or failure, `execute_reserved()` constructs `WorkspaceExecutionOutcome` and, when supplied, awaits `before_release_callback` **while still renewing/owning the same Run guard**. The service callback advances its Job to finalizing, refreshes the disposable projection when appropriate, and fsyncs one terminal Job manifest before returning; agent calls omit it and release immediately after canonical state. An index refresh error is converted inside the callback to terminal `projection_refresh_failed`, so it still returns normally after the failed Job manifest is durable. If terminal `JobStore` persistence itself is unavailable, the controller enters fatal/not-ready mode, stops accepting commands, and keeps retrying the total callback under the live guard until persistence succeeds or process shutdown drops ownership; it never releases into a second same-Run nonterminal Job. Only then does `execute_reserved()` close the guard in `finally` and return/raise the canonical outcome.

Lease loss is a separate fail-closed branch. `RunLeaseGuard` records whether heartbeat-triggered cancellation came from token loss. Once loss is observed, `execute_reserved()` does not update `run.json`, finalize artifacts, refresh the index, or call `before_release_callback`; it conditionally closes only its stale token and invokes optional `lease_lost_callback` with `lease_owned=False`. The service callback may write only its own Job manifest as `interrupted/lease_lost` and stop readiness if that controller write fails. The new lease owner or a later lease-aware reconciliation is solely responsible for canonical Run state. A barrier test lets process B acquire the expired token and write new Run state, then delivers A's heartbeat cancellation; A must terminalize only Job A and byte-for-byte preserve B's canonical/index state.

`abort_reserved()` is valid only before execution start: for start it marks the allocated Run `interrupted` with the supplied stable failure code and non-resumable metadata; for append/resume it leaves the existing finalized Run/Execution unchanged; in all cases it closes the same guard. `release_reserved_for_recovery()` is also pre-execution-only, consumes and closes the guard, but preserves allocated/finalized metadata because a durable queued Job manifest is now the recovery owner; an already released reservation cannot execute. `start_run()` is only a convenience that reserves then executes; it never reacquires. Never create a replacement Run after allocation failure.

For create-track/draft/confirm and prepared Run allocation, `WorkspaceApplication` accepts an optional operation ID that must already exist as a reserved row in `WorkspaceOperationJournal`. While holding the matching lease, it first completes any older workspace-owned pending row, derives and stores lease-sensitive `prepared_facts_json`, transitions the current row to prepared, applies its command with the reserved IDs/timestamp plus those facts, and verifies canonical `last_mutation_id`/confirmation/Run hash. For confirmation, the prepared facts include the expected current pointer and next revision number; they are never guessed before the track lease. Synchronous mutations mark the row applied/completed with the exact serialized response before returning. A `reserve_*()` acceptance uses a JobController-owned row, marks it applied, and deliberately leaves completion/recovery to `JobController` after the matching Job manifest is durable. Calls without an operation ID are agent-local commands: they still recover workspace-owned pending rows first, then generate fresh IDs and apply once.

Every mutating public method, including `initialize()`, reconciliation that would repair metadata, and all `reserve_*()` methods, calls the injected shared `WorkspaceFilesystemPolicy.require_supported_for_mutation()` before reserving an operation, ID, lease, or canonical path. Pure agent read/list/show methods may call `assess()` and return the warning, but the MVP service does not open an unsupported active Workspace in a special inspection mode. The agent CLI and HTTP adapter cannot bypass the mutation boundary; tests run the same blocked cloud/network fixture through both transports and assert no journal/canonical file was created.

- [ ] **Step 4: Write and implement append/resume tests**

Append tests cover:

```python
with self.assertRaisesRegex(AppendSequenceConflict, "expected append sequence 0, found 1"):
    await app.append_run(run_id, request, expected_append_sequence=0, actor=Actor.AGENT)

with self.assertRaisesRegex(ValueError, "combined query_variants must contain at most 20"):
    await app.append_run(
        run_id,
        SearchRequest(query_variants=("twenty-first",)),
        expected_append_sequence=0,
        actor=Actor.AGENT,
    )
```

`reserve_append_run()` acquires one Run lease, then reads all persisted search intents, calculates the case-insensitive normalized union, verifies the expected latest sequence and 20 limit, and returns a reservation containing the request with `append_to_run_id` replaced by the target Run ID. `execute_reserved()` calls the exact brief-local engine, publishes active execution/sequence fields through the shared start event before network work, and advances `finalized_append_sequence` only after the new projection is verified. The `append_run()` convenience method reserves and executes without a second acquire.

`reserve_resume_execution()` resolves `execution_id -> Run -> BriefRevision -> runs_dir` through canonical provenance, acquires the same Run lease, validates recoverability, and returns a reservation. `execute_reserved()` calls `resume_execution()` without creating a new Run or append sequence. Unknown, completed, or failed non-recoverable executions return typed errors. The HTTP controller is allowed to hold only this opaque application reservation; it never calls the lease manager.

- [ ] **Step 5: Write failing crash-reconciliation tests**

Cover these states:

- allocated `run.json` without `run.sqlite` remains allocated only while its lease is live or a linked Job is still queued; otherwise reconciliation marks it interrupted and non-resumable;
- a durable nonterminal Execution discovered under an allocated/running Run after a pre-network crash is linked back by `(run_id, append_sequence)` and becomes interrupted/recoverable even when the controller never persisted its Execution ID;
- completed execution plus verified artifacts becomes completed even if the service/agent crashed before updating `run.json`;
- nonterminal execution with a stale active heartbeat becomes interrupted/recoverable;
- verified receipt mismatch, request mismatch, duplicate identity, or corrupt canonical JSON sets `integrity=corrupt`, withholds results, and exposes a relative-path diagnostic without conflating integrity and lifecycle;
- partial source coverage is completed with `quality=degraded`, not failed;
- fixed filenames are treated only as the latest projection; sequence history remains in SQLite.
- a barrier lets reconciliation observe an apparently stale Run, then an agent acquires its Run lease and starts/writes; reconciliation must fail its nonblocking acquire, skip/report that repair, and never overwrite the agent state;
- the same barrier around `draft.json.pending_confirmation` lets an agent acquire the SearchTrack writer and complete/mutate first; reconciliation rereads under `track_writer()` and either observes the completed fact or skips, never publishing from its stale pre-lock snapshot.

- [ ] **Step 6: Implement deterministic reconciliation**

`WorkspaceReconciler` may perform an initial read-only scan to find candidates, but every repair is lease-and-reread, never check-then-write. It acquires `track_writer()` before completing an exact `draft.json.pending_confirmation`, rereads the draft/journal/current pointer while holding that lease, and applies only facts still matching. It acquires the same nonblocking transferable Run guard before changing Run lifecycle/integrity metadata, then rereads canonical JSON, live lease ownership, SQLite execution/session state, and verified artifact hashes under that guard before writing. A busy target is skipped with a stable diagnostic and retried by later startup/watcher/periodic reconciliation; reconciliation never waits behind or overwrites an active agent/service writer. An unsupported root aborts reconciliation before any writer or repair.

The optional `protected_run_ids` is a set of opaque controller-owned Run IDs supplied by the higher layer during startup; it includes both queued Job manifests and incomplete `recovery_owner="job_controller"` journal rows whose reserved IDs contain a Run, so the crash window after `run.json` publication but before Job-manifest creation is protected. Protection prevents premature cleanup of a merely allocated Run, but never hides durable engine state: after acquiring/re-reading under the Run guard, if `run.sqlite` already contains the intended Execution, the reconciler records its identity and classifies completed evidence as completed or nonterminal evidence as interrupted/recoverable. The workspace package never imports backend Job types. It may advance stale product status to a fact supported by durable evidence, including `allocated -> interrupted` when no live lease or protected controller operation remains, but it never edits immutable brief content, deletes corrupt evidence, invents provenance, auto-resumes, or imports an old-layout Run.

- [ ] **Step 7: Verify and commit the shared application**

```bash
uv --directory plugins/job-harness run python -m unittest \
  tests.workspace.test_application \
  tests.workspace.test_reconciliation -v
git add plugins/job-harness/src/job_harness/workspace \
  plugins/job-harness/tests/workspace
git commit -m "feat: orchestrate searches through workspace application"
```

Expected: all focused tests pass, including concurrent different Runs and conflicting same-Run writers.

### Task 7: Move the agent workflow onto the Workspace application

**Files:**
- Modify: `plugins/job-harness/pyproject.toml`
- Modify: `plugins/job-harness/uv.lock`
- Create: `plugins/job-harness/src/job_harness/workspace/cli.py`
- Create: `plugins/job-harness/tests/workspace/test_cli.py`
- Modify: `plugins/job-harness/skills/user-briefing/SKILL.md`
- Modify: `plugins/job-harness/skills/job-search-workflow/SKILL.md`
- Modify: `plugins/job-harness/tests/v2/test_runtime_skill_contract.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 6 `WorkspaceApplication` and the existing `job-harness-v2 list-sources`/`format` developer utilities.
- Produces: installed `job-harness-workspace` JSON CLI used for all normal agent mutations and searches.

- [ ] **Step 1: Write failing CLI contract tests**

Require these commands and JSON stdout records:

```text
job-harness-workspace init --workspace <dir>
job-harness-workspace track list --workspace <dir>
job-harness-workspace track create --workspace <dir> --title <title>
job-harness-workspace draft put --workspace <dir> --track-id <id> --input <file-or->
job-harness-workspace draft confirm --workspace <dir> --track-id <id> --expected-version <n>
job-harness-workspace brief list --workspace <dir> --track-id <id>
job-harness-workspace brief show --workspace <dir> --track-id <id> --brief-id <id>
job-harness-workspace brief translate --workspace <dir> --track-id <id> --brief-id <id>
job-harness-workspace run start --workspace <dir> --track-id <id> --brief-id <id>
job-harness-workspace run append --workspace <dir> --run-id <id> --expected-sequence <n> --request <file-or->
job-harness-workspace execution resume --workspace <dir> --execution-id <id>
job-harness-workspace run show --workspace <dir> --run-id <id>
```

`draft put` input is one strict object:

```json
{
  "summary": "Senior QA, worldwide remote",
  "preferences": {
    "query_formulations": ["Senior QA", "SDET"],
    "grades": ["senior"],
    "work_formats": ["remote"],
    "remote_scopes": ["global"],
    "notes": "Employer product teams preferred"
  }
}
```

The `preferences` object stores the human brief, including its approved query formulations, but is not a `SearchRequest`. `brief show` returns the exact immutable revision; `brief translate` returns the strict output of `brief_preferences_to_search_request()` for audit; normal `run start` rereads that same revision and invokes the shared mapping internally, so the agent never hand-builds or passes a second request. Tests assert translate output contains all 20 formulations/criteria in order, 21 unique formulations return exit `1` with `query_variant_limit`, stdin `-` works for draft/append input, stderr receives progress only, stdout contains exactly one final JSON record, and no mutation command accepts `--actor`. The created `run.json` must link the selected BriefRevision and pin the exact translated request.

- [ ] **Step 2: Implement the thin CLI and entrypoint**

Add:

```toml
[project.scripts]
job-harness-workspace = "job_harness.workspace.cli:main"
```

The CLI parses paths/IDs, derives `Actor.AGENT`, calls only `WorkspaceApplication` public methods, prints versioned JSON, and maps typed errors to stable codes. `brief translate` calls `translate_brief_revision()`; `run start` obtains that same translation from the application and passes it back to `start_run()`, whose equality check remains authoritative. `run start` does not accept `--request` or any criterion override. The CLI never imports `WorkspaceRepository`, writes canonical files directly, or starts/discovers the HTTP service.

- [ ] **Step 3: Replace the old skill layout and execution flow**

Update both runtime skills so they:

1. ask before initializing a new Workspace;
2. inspect the current source catalog before finalizing source IDs and capabilities;
3. list/select or create a SearchTrack;
4. list/show existing BriefRevisions and either select one unchanged for rerun, or collect changed business preferences, put the draft, and explicitly confirm a new immutable BriefRevision;
5. reread that exact confirmed revision and translate it with `brief_preferences_to_search_request()` into the full SearchRequest, without manually rebuilding or extending the request;
6. start a new linked Run through `job-harness-workspace` only after user approval;
7. present `report.html` and the nested Run directory;
8. label append as an internal/advanced capability and distinguish it from resume;
9. report `unsupported workspace layout` for old-only artifacts without offering import or migration.

Delete all instructions that create `.job-harness/briefs`, `.job-harness/v2/runs`, or prohibit nesting Runs under briefs. `job-harness-v2 search` becomes documented as a developer-only engine adapter; `list-sources` and `format` may remain read-only helpers.

- [ ] **Step 4: Tighten skill and architecture tests**

Assert the runtime skills contain `job-harness-workspace`, `brief list/show/translate`, `searches/`, `briefs/<revision>/runs`, the explicit confirmation-before-translation rule for changed preferences, unchanged reuse/rerun of an existing BriefRevision, `brief_preferences_to_search_request`, and the 20-query limit. Assert they do not contain `.job-harness/briefs/`, `.job-harness/v2/runs/`, `--runs-dir`, a service-start command, import/migration guidance, a hand-built Run request, or user-facing append as the default iteration path.

- [ ] **Step 5: Verify the complete independently usable Workspace milestone**

```bash
uv --directory plugins/job-harness lock --check
uv --directory plugins/job-harness run python -m unittest discover \
  -s tests/workspace -p 'test_*.py' -v
uv --directory plugins/job-harness run python -m unittest \
  tests.v2.test_runtime_skill_contract -v
python3 scripts/verify_v2.py --skip-live
git diff --check
```

Expected: all commands exit `0`; a deterministic fixture search is visible only in the new nested layout.

- [ ] **Step 6: Commit the agent milestone**

```bash
git add plugins/job-harness/pyproject.toml plugins/job-harness/uv.lock \
  plugins/job-harness/src/job_harness/workspace/cli.py \
  plugins/job-harness/tests/workspace/test_cli.py \
  plugins/job-harness/skills plugins/job-harness/tests/v2/test_runtime_skill_contract.py \
  README.md
git commit -m "feat: move agent searches into workspaces"
```

### Task 8: Scaffold the secured loopback backend and session bootstrap

**Files:**
- Create: `apps/job-harness-local/VERSION.json`
- Create: `apps/job-harness-local/backend/pyproject.toml`
- Create: `apps/job-harness-local/backend/uv.lock`
- Create: `apps/job-harness-local/backend/src/job_harness_local/__init__.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/config.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/api_models.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/errors.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/auth.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/app.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/__init__.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/session.py`
- Create: `apps/job-harness-local/backend/tests/conftest.py`
- Create: `apps/job-harness-local/backend/tests/test_auth.py`
- Create: `apps/job-harness-local/backend/tests/test_app.py`

**Interfaces:**
- Consumes: one already selected `WorkspaceApplication`; the backend never discovers multiple Workspaces inside one process.
- Produces: `create_app(settings, workspace_app) -> FastAPI`, unauthenticated readiness/static shell, one-time browser launch exchange, authenticated `/api/v1/session`, strict error envelopes, and mutation CSRF enforcement.

- [ ] **Step 1: Create the isolated backend package**

Set local-app version `0.1.0` independently from plugin version `0.5.1`. Configure Python `>=3.12`, the local `job-harness` path source, and direct runtime dependencies `fastapi`, `pydantic`, `uvicorn`, and `watchfiles`; add `httpx`, `pytest`, `pytest-asyncio`, `ruff`, `mypy`, and `pyinstaller` to the dev group. Register the repository-local source without exposing a launcher entrypoint before Task 18 creates it:

```toml
[tool.uv.sources]
job-harness = { path = "../../../plugins/job-harness", editable = true }
```

Generate and commit `backend/uv.lock`. Do not add these service dependencies to the plugin's runtime package.

- [ ] **Step 2: Write failing Host, launch-token, cookie, and CSRF tests**

Cover:

```python
def test_launch_token_is_single_use(
    client: TestClient,
    launch_token: str,
    session_cookie_name: str,
) -> None:
    first = client.post(
        "/api/v1/auth/launch",
        headers={"Origin": "http://127.0.0.1:8765"},
        json={"token": launch_token},
    )
    assert first.status_code == 200
    assert f"{session_cookie_name}=" in first.headers["set-cookie"]
    assert "HttpOnly" in first.headers["set-cookie"]
    assert "SameSite=strict" in first.headers["set-cookie"]
    second = client.post(
        "/api/v1/auth/launch",
        headers={"Origin": "http://127.0.0.1:8765"},
        json={"token": launch_token},
    )
    assert second.status_code == 401

def test_mutation_requires_matching_origin_and_csrf(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/v1/search-tracks",
        headers={"Origin": "http://evil.example"},
        json={"title": "QA"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_rejected"
```

Also reject a nonconfigured Host, reject a missing/incorrect CSRF token, assert unauthenticated API calls return JSON `401`, assert `/api/v1/health` reveals no paths or secrets, and assert no `Access-Control-Allow-Origin: *` header exists.

- [ ] **Step 3: Verify RED**

```bash
uv --directory apps/job-harness-local/backend run pytest \
  tests/test_auth.py tests/test_app.py -q
```

Expected: imports fail because the backend package does not exist yet.

- [ ] **Step 4: Define injected settings and strict wire errors**

```python
@dataclass(frozen=True)
class AppSettings:
    workspace_root: Path
    runtime_dir: Path
    cache_dir: Path
    index_path: Path
    bind_host: Literal["127.0.0.1", "::1"]
    port: int
    control_token: str
    session_cookie_name: str
    launch_token_ttl_seconds: int = 120
    frontend_dir: Path | None = None

class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)

class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: ErrorBody
```

All response/request models use `extra="forbid"`; IDs and relative URLs are strings, never filesystem paths.

- [ ] **Step 5: Implement one-time launch and browser session state**

On process start, hash the high-entropy control token and retain no pre-aged browser token. A `LaunchTokenStore` mints a fresh random one-time token only when the ready service handles an authenticated launch-URL handoff (Task 18). It keeps a bounded map (maximum 32) of independently outstanding token hashes and issuance times; mint prunes expired/consumed entries but never invalidates another unexpired launch URL, and a still-full map rejects minting rather than evicting a valid token. The launcher opens `http://127.0.0.1:<port>/#launch=<token>` so the token is not sent in an HTTP request, access log, or Referer. The static bootstrap removes the fragment with `history.replaceState()` and posts it once to `POST /api/v1/auth/launch`. The route verifies exact Origin, compares in constant time, rejects tokens older than the configured two-minute TTL, and atomically consumes only the matching entry.

Each service process owns one process-lifetime browser session/CSRF pair, created lazily on the first successful exchange and reused by later valid focus-launch tokens. Its cookie name is `jh_session_<instance-suffix>`, where the validated non-secret suffix is derived from the resolved-path instance hash; it is never the global `jh_session`. This prevents two simultaneously running Workspace instances on different loopback ports from overwriting each other's cookie, while reusing one same-instance session prevents a second focused tab from invalidating the first tab's CSRF state. Set that configured cookie as `HttpOnly; SameSite=Strict; Path=/` with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. Unit tests inject a fake clock and mint through the store rather than putting a raw launch token in `AppSettings`; a concurrency case mints two handoffs before either exchange, proves both tokens are consumed exactly once and return the same process session, then proves either replay returns `401`. An integration test uses one browser cookie jar for Workspace A on port 8765 and Workspace B on 8766, then proves authenticated reads/mutations and old-tab CSRF continue working in A, B, and a second focused A tab. No token appears in captured output.

`GET /api/v1/session` returns only the CSRF token and service version for a valid session. Mutations require the exact configured Origin and `X-CSRF-Token`; the app installs no permissive CORS middleware. Host middleware permits only the configured numeric loopback host and port.

- [ ] **Step 6: Build the minimal app factory**

`create_app()` registers exception handlers before routers, injects `WorkspaceApplication` through app state, and exposes:

```text
GET /api/v1/health
POST /api/v1/auth/launch
GET /api/v1/session
```

Health returns `{"status":"ready","version":"0.1.0"}` only after startup reconciliation is complete; until then return `503` with `status=starting`.

- [ ] **Step 7: Verify and commit the backend security shell**

```bash
uv --directory apps/job-harness-local/backend lock --check
uv --directory apps/job-harness-local/backend run pytest -q
uv --directory apps/job-harness-local/backend run ruff check src tests
uv --directory apps/job-harness-local/backend run mypy src tests --strict
git add apps/job-harness-local/VERSION.json apps/job-harness-local/backend
git commit -m "feat(local-app): secure the loopback service"
```

Expected: all checks pass and tests cannot reuse a consumed launch token.

### Task 9: Persist idempotent background Jobs before returning `202`

**Files:**
- Create: `apps/job-harness-local/backend/src/job_harness_local/job_models.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/job_store.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/idempotency.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/jobs.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/jobs.py`
- Create: `apps/job-harness-local/backend/tests/test_job_store.py`
- Create: `apps/job-harness-local/backend/tests/test_jobs.py`
- Create: `apps/job-harness-local/backend/tests/test_idempotency.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/api_models.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/app.py`

**Interfaces:**
- Consumes: `WorkspaceApplication.reserve_start_run()`, `reserve_append_run()`, `reserve_resume_execution()`, `execute_reserved()`, `abort_reserved()`, `release_reserved_for_recovery()`, and `.job-harness/_runtime/jobs/`.
- Produces: HTTP `IdempotencyRegistry` with in-process same-key coalescing over the shared `WorkspaceOperationJournal`, durable `JobManifest`, `JobController.accept()`, a bounded concurrent Job dispatcher, polling snapshots, and Server-Sent Event notifications.

- [ ] **Step 1: Define and test the canonical controller command**

```python
class JobLifecycle(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

class JobPhase(StrEnum):
    PLANNING = "planning"
    SEARCHING = "searching"
    ENRICHING = "enriching"
    DISCOVERING = "discovering"
    FINALIZING = "finalizing"

@dataclass(frozen=True)
class JobManifest:
    job_id: str
    version: int
    operation_correlation_id: str
    operation: Literal["start_run", "append_run", "resume_execution"]
    requested_by: Actor
    lifecycle: JobLifecycle
    phase: JobPhase
    quality: Literal["complete", "degraded", "failed"] | None
    command: JsonObject
    command_sha256: str
    normalized_request_sha256: str
    idempotency_key_sha256: str
    track_id: str | None
    brief_revision_id: str | None
    run_id: str | None
    execution_id: str | None
    append_sequence: int | None
    accepted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    failure_code: str | None

@dataclass(frozen=True)
class IdempotencyOperation:
    operation_id: str
    key_sha256: str
    command_sha256: str
    operation_kind: str
    state: Literal["reserved", "prepared", "applied", "completed"]
    target_id: str
    job_id: str | None
    prepared_facts: JsonObject | None
    response_status: int | None
    response_json: JsonObject | None

@dataclass(frozen=True)
class IdempotencyClaim:
    operation: IdempotencyOperation
    is_owner: bool
    async wait() -> tuple[int, JsonObject]

@dataclass(frozen=True)
class JobAcceptedDto:
    job_id: str
    run_id: str | None
    execution_id: str | None
    lifecycle: Literal["queued"]
    status_url: str
    events_url: str

@dataclass(frozen=True)
class JobSnapshotDto:
    job_id: str
    version: int
    operation: Literal["start_run", "append_run", "resume_execution"]
    requested_by: Actor
    lifecycle: JobLifecycle
    phase: JobPhase
    quality: Literal["complete", "degraded", "failed"] | None
    track_id: str | None
    brief_revision_id: str | None
    run_id: str | None
    execution_id: str | None
    append_sequence: int | None
    accepted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    failure_code: str | None
    counters: dict[str, int]

RefreshFinalizedRun = Callable[[str], Awaitable[None]]

class JobController:
    def before_run_lease_release(self, job_id: str) -> BeforeRunLeaseRelease: ...
    def after_run_lease_lost(self, job_id: str) -> AfterRunLeaseLost: ...
```

The queued manifest already pins the intended workflow slot: `append_sequence=0` for start, `expected_append_sequence + 1` for append, and the existing sequence plus `execution_id` for resume. It also pins the journal `operation_correlation_id` and normalized persisted SearchRequest hash. These values come from the application's prepared journal facts under the Run lease, not from a controller-side reread or guess. The later activation callback adds newly allocated Execution/session IDs; this lets recovery distinguish “allocated only” from “this exact command reached engine persistence” even if the callback never reaches `JobStore`.

Test that `JobStore.create()` has fsynced `<job_id>.json` before it returns, stores no raw idempotency key, rejects unknown fields on read, and increments `version` atomically on every transition. `JobStore.active_for_run(run_id)` returns the sole nonterminal manifest or `None`, and treats duplicate nonterminal manifests for one Run as controller corruption rather than choosing arbitrarily; the before-release guard makes that invariant achievable. `JobStore.latest_for_run(run_id)` may return the newest retained terminal manifest for projection-error disclosure and uses deterministic accepted-time/Job-ID ordering. `IdempotencyRegistry` normalizes HTTP route/target/body into the shared journal command, hashes the key globally rather than per route, and delegates prepared/applied/completed recovery to `WorkspaceApplication`; it stores no raw key and retains operations indefinitely in MVP.

- [ ] **Step 2: Write failing idempotency and lifecycle tests**

Assert:

- first accept persists queued command, then returns its stable Job ID;
- same `Idempotency-Key` plus byte-equivalent normalized command returns the original Job;
- two concurrent accepts with the same key/command elect one in-process owner before `reserve_*()`, while the follower awaits the owner's shielded completion and then replays the same operation/Job/Run/manifest/`202`; the follower never attempts the live target lease and cancellation of either HTTP request does not cancel shared acceptance;
- same key plus a different route, target, or normalized command raises `IdempotencyConflict`, including reuse between synchronous and asynchronous mutations;
- a start command preallocates both `job_id` and `run_id`, stores both in the prepared operation/Job manifest, and publishes the matching `run.json` under a live application reservation before `202` can be returned;
- browser/client cancellation does not cancel the worker;
- with injected dispatcher concurrency `2`, two different Runs both cross a fixture network barrier before either is released, while a second command for the same Run fails at the shared Run lease before queueing;
- after an injected lease-token takeover, the losing pool task writes only its Job as `interrupted/lease_lost`; it never refreshes or mutates canonical state written by the new owner;
- an exception before Job-manifest fsync calls `abort_reserved()`; an exception after manifest fsync but before queue handoff calls `release_reserved_for_recovery()` and preserves the queued Job/allocated Run; after queue handoff only the worker closes the guard;
- worker transitions `queued -> running -> completed|failed|interrupted` and never writes an invented percentage;
- execution-start callback pins Run/Execution IDs while the Job is running; a callback failure leaves no network calls and terminalizes the Run/Job as interrupted/recoverable from durable Execution evidence;
- the manifest is completed only after Workspace/engine durable state and verified artifacts agree;
- queued Jobs with no durable Execution for their pinned workflow slot are revalidated on service restart and requeued only after reacquiring an application reservation with the same deterministic IDs; a new external owner produces terminal `run_busy_on_recovery` rather than a race;
- a queued Job whose exact correlated `(run_id, append_sequence, operation_correlation_id, request_hash)` or resume-session attempt already exists in `run.sqlite` is never requeued: durable completion makes it completed, otherwise Workspace and Job become interrupted/recoverable and require an explicit user resume;
- durable engine state is attributable to a Job only when workflow slot, `operation_correlation_id`, and normalized persisted request hash all match its manifest/journal; a slot occupied by another agent/service command yields stable `workflow_slot_taken` and is never claimed as this Job's completion;
- an incomplete acceptance journal plus an already running or terminal Job manifest first reconstructs/completes the original `202` from that manifest, then reconciles lifecycle, without creating or queueing anything;
- an immediate restart before the crashed owner's 30-second lease expires defers one recovery decision until the observed lease expires; an unchanged, unrenewed token is then recovered, while an advancing heartbeat is a real external writer and yields `run_busy_on_recovery`;
- a restart after start-Run `run.json` publication but before Job-manifest creation protects the Run via the incomplete JobController journal row, reacquires the reservation with the same Run/Job IDs, creates exactly one manifest, and never marks that Run interrupted first;
- previously running Jobs are reconciled to completed when durable engine state proves completion, otherwise interrupted/recoverable; a linked Run that never obtained an Execution becomes interrupted/non-resumable, and work is never automatically resumed.

- [ ] **Step 3: Implement `JobController.accept()` and the worker queue**

`IdempotencyRegistry.claim()` first normalizes/hashes the command and key, then under one short-held `asyncio.Lock` either finds a matching in-flight entry or installs a provisional `(command_sha256, Future)` owner entry before releasing the lock; it performs no filesystem/SQLite await while locked. Only that owner atomically reserves/loads the journal row. A concurrent identical caller is `is_owner=False`, never calls `reserve_*()`, and awaits the Future through `asyncio.shield()` before replaying the completed journal response; a different command conflicts immediately. If journal reservation itself fails, the owner completes/removes the provisional entry with the same typed error. The owner acceptance runs in a controller-owned task, so disconnecting its initiating HTTP request cannot cancel it; terminal success/conflict/error completes both journal response columns and all followers, then removes the Future. On restart there is no in-memory Future, so the one durable recovery path claims incomplete rows before accepting new owners.

The owner `accept()` asks the registry for deterministic operation/Job/Run IDs without touching canonical files. It passes that operation into the matching `WorkspaceApplication.reserve_*()` method; the application prepares it under the Run guard, validates the command, and for start publishes the preallocated `run.json`. The controller then creates the queued Job manifest with the same IDs and hands the manifest plus opaque reservation to an unbounded process-owned queue with `put_nowait()`. A supervised dispatcher feeds a bounded pool with injected `max_concurrent_jobs` (default `4`, validation minimum `2`); each pool task calls `execute_reserved()`, which transfers the existing guard to that task. This permits genuinely overlapping network work for different Runs while the shared Run lease remains the sole same-Run arbiter. Only after durable manifest, queue handoff, completed acceptance operation, and live reservation agree may the handler send/replay `202`.

Ownership has three explicit boundaries. Before `JobStore.create()` fsyncs the manifest, `accept()` owns the reservation; an ordinary exception calls `abort_reserved(..., failure_code="acceptance_aborted")` and persists the stable error. From manifest fsync until successful `put_nowait()`, the durable queued Job is the canonical recovery owner while `accept()` is only temporary guard custodian; an ordinary exception calls `release_reserved_for_recovery()`, then invokes the same queued-manifest recovery routine to reacquire/enqueue, and never marks the Run interrupted. After `put_nowait()`, the dispatcher/pool owns the opaque reservation and exactly one pool task's `execute_reserved()` path closes it. Handler cancellation is shielded through this state machine.

A hard process crash at any boundary naturally drops the in-memory guard: before a manifest, the incomplete JobController journal row protects/reconstructs acceptance; after manifest fsync, the manifest protects/reconstructs it. Persist the journal `202` only after a live queue handoff (original or recovered). Add distinct restart and in-process fault tests immediately before manifest fsync, immediately after manifest fsync/before queue handoff, immediately after queue handoff, and before HTTP response; assert exactly one manifest, one eventual queue entry, one guard owner, no leaked heartbeat, no premature `interrupted`, and no second Run.

Crash recovery is state based rather than cross-filesystem transactional: prepared workspace-owned operations inspect their deterministic target; an absent target is safely applied with the same ID, an exact `last_mutation_id`/Run hash is marked applied, and a mismatched target is corruption. JobController-owned rows inspect deterministic Job/Run IDs, and before general Run reconciliation their Run IDs join `protected_run_ids`.

Any existing manifest, regardless of `queued|running|terminal`, proves that acceptance crossed the manifest-fsync boundary. Recovery first verifies its command/IDs against the journal row and completes the original `202` response from that manifest if the row is incomplete; lifecycle recovery is a separate second step. For a queued manifest whose pinned workflow slot has no durable **matching correlated** Execution/session, recovery reacquires the application reservation and enqueues once only when the slot is still free. A live lease is not an immediate conflict during crash recovery: recovery observes its token/heartbeat/expiry, keeps readiness at `starting`, and waits through that one bounded expiry window. If the same token does not renew and expires, it acquires/requeues; if the heartbeat/expiry advances or owner/token changes, a real external writer exists and the Job becomes interrupted with `run_busy_on_recovery`. If `run.sqlite` changes while waiting, recovery compares the durable workflow/resume-session correlation ID and normalized persisted intent to the manifest/journal. An exact match switches to this Job's durable-Execution reconciliation and never queues; a sequence/session occupied by a different correlation or request terminates this Job as interrupted with `workflow_slot_taken`, without attributing foreign results or appending over them. The same exact-match rule applies when the slot exists initially.

If no manifest exists and deterministic Run/append/resume preconditions still match, recovery applies the same lease-observation/correlation rule, reacquires one application reservation, creates the one reserved manifest, and completes the original `202`; if an external writer has invalidated those preconditions, it persists and replays that stable conflict rather than claiming the unaccepted Job existed. The completed journal row's `response_status` and `response_json` are the only replay response. Add fault-injection/restart tests after registry prepare, `run.json` publication, Job write, operation apply, enqueue, after a pool task writes `running`, after a pool task writes each terminal state but before journal completion, immediately after `_create_executions()`, immediately after the Run active-fields write, on a `JobStore` activation-callback write error, and before response. Add a barrier race where crashed queued append A expires, agent append B with a different request occupies the same next sequence, and recovery A records `workflow_slot_taken` without claiming B. Each activation-window test asserts zero network calls, one durable correlated Execution, no second queue entry/Run/Execution, and after restart either exact matched completion evidence or one interrupted/recoverable Job and Run requiring explicit resume.

Pool-task phases are facts, not percentages: planning before Workspace mutation, searching while `V2SearchApplication` is active, and finalizing inside one outcome-aware controller callback while durable Workspace state is verified. Task 9 injects a no-op `RefreshFinalizedRun` so this backend milestone does not depend on the later index; Task 10 injects `scanner.refresh_run`. Each pool task calls `execute_reserved()` with its already-owned opaque reservation and passes both `JobController.before_run_lease_release(job_id)` and `JobController.after_run_lease_lost(job_id)` as the exact Task 6 callbacks. The before-release callable accepts an owned `WorkspaceExecutionOutcome`, atomically moves the Job to finalizing, invokes `RefreshFinalizedRun` for a successfully finalized Run (or projects failure metadata when applicable), then fsyncs `completed|failed|interrupted` before returning. The lease-lost callable accepts only `lease_owned=False`, skips Workspace/index work, and fsyncs Job interruption. Thus the application releases a live Run guard only after terminal Job persistence, never writes through a stolen guard, and the pool task never acquires a second lease. Preserve engine counters as named counters only. Enriching/discovering may be emitted only when the engine supplies that phase; do not infer a fake proportion.

Each pool task also passes `execution_active_callback` to `execute_reserved()`. After WorkspaceApplication has durably written the Run's active fields but still before network work, that callback atomically increments the Job manifest version, sets lifecycle/phase to running/searching, and pins correlation plus Run/Execution IDs and append sequence. If `JobStore` persistence fails, the callback raises, engine work does not start, WorkspaceApplication performs the interrupted/recoverable transition described in Task 6, and the same before-release finalizer persists the matching terminal Job; if terminal persistence itself is unavailable, the fail-closed retry/fatal behavior from Task 6 applies. Resume emits the same callback from its newly correlated durable resume attempt. Polling/SSE therefore observes exact identities while all three command kinds are active.

- [ ] **Step 4: Implement polling and SSE as notification over snapshots**

Expose:

```text
GET /api/v1/jobs/{job_id}
GET /api/v1/jobs/{job_id}/events
```

Mutation acceptance returns `JobAcceptedDto`; polling returns `JobSnapshotDto`; each SSE `data` payload is the same `JobSnapshotDto`. The event stream immediately emits the current snapshot with `id: <manifest.version>`, emits subsequent snapshots, sends a comment heartbeat every 15 seconds, and honors `Last-Event-ID`. After reconnect the client always re-fetches the polling snapshot; in-memory events are never the source of truth.

- [ ] **Step 5: Keep terminal manifests as the MVP audit trail**

Retain terminal Job manifests and idempotency operations indefinitely in the MVP. This avoids a controller garbage collector before real usage provides a retention requirement and preserves unconditional replay. Tests prove startup leaves terminal manifests intact and that product history still comes from Run/Execution provenance rather than Job files. `requested_by` audits the HTTP controller command only; the UI does not claim an engine-level append/resume actor that `run.sqlite` does not store.

- [ ] **Step 6: Verify and commit durable Jobs**

```bash
uv --directory apps/job-harness-local/backend run pytest \
  tests/test_idempotency.py tests/test_job_store.py tests/test_jobs.py -q
git add apps/job-harness-local/backend/src/job_harness_local \
  apps/job-harness-local/backend/tests
git commit -m "feat(local-app): persist background search jobs"
```

Expected: tests pass, including restart and idempotency replay.

### Task 10: Build the disposable index, canonical scanner, watcher, and read API

**Files:**
- Create: `apps/job-harness-local/backend/src/job_harness_local/index.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/scanner.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/watcher.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/workspace.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/search_tracks.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/runs.py`
- Create: `apps/job-harness-local/backend/tests/test_index.py`
- Create: `apps/job-harness-local/backend/tests/test_scanner.py`
- Create: `apps/job-harness-local/backend/tests/test_watcher.py`
- Create: `apps/job-harness-local/backend/tests/test_read_api.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/jobs.py`
- Modify: `apps/job-harness-local/backend/tests/test_jobs.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/api_models.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/app.py`

**Interfaces:**
- Consumes: canonical `WorkspaceRepository`, `WorkspaceApplication.read_run()`, `RunSnapshotReader`, reconciliation from Task 6, and durable active/latest Job lookup from Task 9.
- Produces: rebuildable per-Workspace SQLite projection, settled rescans, and the complete read-only product API.

- [ ] **Step 1: Write failing rebuild tests**

Create a canonical fixture with two SearchTracks, three BriefRevisions, completed/interrupted Runs, and one completed Run whose append sequence 0 is followed by sequence 1. Assert deleting the index and scanning again recreates identical API DTOs, both historical sequence projections, counts, source order, request audit, and diagnostics. Corrupt canonical files must remain untouched and appear as relative-path diagnostics. Copy the portable Workspace to a second resolved root while preserving its `workspace_id`; each root must receive a distinct index keyed by its resolved-path hash and neither scanner may see the other's projection.

- [ ] **Step 2: Implement one transactional projection schema**

Store only reconstructable fields in tables for Workspace scan generations, SearchTracks, BriefRevisions, Runs, result items, source summaries, execution steps, and artifact descriptors. Every Run result row is keyed by `(run_id, append_sequence, category, result_id)` and stores display source, deterministic engine order, low-cardinality search text, and projection JSON.

Place the database at:

```text
~/Library/Application Support/Job Harness/workspaces/by-path/<sha256-of-resolved-root>/index.sqlite
```

The path is injectable in tests. Index metadata stores both the resolved-root hash and canonical `workspace_id`; a mismatch quarantines/rebuilds the disposable index instead of mixing copied Workspace projections. A corrupt index is renamed to `index.sqlite.corrupt-<timestamp>` and rebuilt; no canonical Workspace file is moved.

- [ ] **Step 3: Implement a correctness-first scanner**

Startup first calls `job_controller.protected_run_ids()`, defined exactly as Run IDs from queued/recovering acceptance manifests plus incomplete `recovery_owner="job_controller"` journal rows. Running Jobs already have durable correlated Execution evidence/live leases; retained terminal manifests never protect a Run. It passes that set to `WorkspaceApplication.reconcile(protected_run_ids=...)`, then recovers controller operations/reservations, performs the full scan, and only then reports readiness. A test retains a terminal manifest beside a stale allocated Run and proves it does not prevent the correct interrupted repair. For each `integrity=ok` Run, index every completed `(run_id, append_sequence)` result/source/request projection reconstructable from durable `run.sqlite` workflow evidence, including older sequences after fixed projection files have been overwritten. Artifact hash verification controls only descriptor downloadability and the current fixed-file projection; it is not a prerequisite for historical database-backed result cards. Corrupt Runs retain metadata/diagnostics but expose no result cards. Replace all sequence projections for one Run in one transaction and record the canonical database fingerprint used. A running internal append leaves the prior finalized projection visible and sets a banner state.

Implement `scanner.refresh_run(run_id)` and inject it as Task 9's `RefreshFinalizedRun` dependency; JobController invokes it inside the outcome-aware `before_release_callback`, not after `execute_reserved()` returns. All full-scan, watcher, and focused Run work passes through one keyed single-flight/per-Run lock held across canonical read, projection build, and index commit. A full scan calls the same locked Run-refresh primitive rather than building stale rows outside the lock. Therefore, if an older full scan enters first, finalization waits and the focused fresh commit occurs last; if focused refresh enters first, the later full scan rereads finalized state. A service-owned Job remains in `finalizing` under the Run guard until that one transactional refresh succeeds and its terminal manifest is fsynced, so its terminal snapshot and Run API agree; an index failure leaves the Job failed with `projection_refresh_failed` while canonical Run results remain recoverable by the next full rebuild. External agent writes still use watcher/periodic/startup scanning and never import `jobs.py`.

Add a deterministic overlap test that pauses a full scan after acquiring one Run's scanner lock, finalizes that Run, starts `refresh_run()`, then releases the old scan. Assert the focused refresh commits last and the terminal Job snapshot, Run DTO, counts, and finalized sequence agree; no older projection can overwrite them.

- [ ] **Step 4: Implement watch hints and periodic rescan**

Use `watchfiles.awatch()` only as a latency hint. Coalesce events for 250 ms, ignore `.tmp` as a canonical target, and map `-wal`/`-shm` activity to the containing Run rather than indexing those files. Before projecting watcher targets, call lease-aware `WorkspaceApplication.reconcile(track_ids=..., run_ids=..., protected_run_ids=job_controller.protected_run_ids())`; before each 5-second/overflow full scan, call the unfiltered form with the current protected set. Thus a repair skipped because an agent held the lease is retried in the live service after that writer finishes or its lease expires, not only after process restart. The workspace layer still receives only opaque ID sets and imports no backend Job type. Stop cleanly through FastAPI lifespan.

Test that an agent-created draft, confirmed brief, allocated Run, and finalized Run appear without service-mediated writes; a service-off change appears after the next startup scan. Add a fake-clock/barrier case where startup reconciliation skips a Run held by an agent, that writer dies without cleanup, and a later periodic reconcile after lease expiry changes it to interrupted/recoverable and projects the update without restarting the service.

- [ ] **Step 5: Define exact read DTOs and routes**

Expose:

```text
GET /api/v1/workspace
GET /api/v1/source-catalog
GET /api/v1/search-tracks
GET /api/v1/search-tracks/{track_id}
GET /api/v1/search-tracks/{track_id}/briefs
GET /api/v1/search-tracks/{track_id}/runs
GET /api/v1/runs/{run_id}
GET /api/v1/runs/{run_id}/results?append_sequence=&category=&source=&cursor=&limit=&q=
GET /api/v1/runs/{run_id}/results/{result_id}?append_sequence=
GET /api/v1/runs/{run_id}/sources?append_sequence=
GET /api/v1/runs/{run_id}/artifacts?append_sequence=
```

Define these exact response shapes in `api_models.py` with `extra="forbid"`:

```text
CountryCatalogItemDto { country_code, display_name, search_enabled }
CriterionCapabilityDto { criterion, capability }
SourceCatalogItemDto {
  source_id, source_type, transport, countries, source_limit, implemented,
  criteria: list[CriterionCapabilityDto]
}
SourceCatalogResponse {
  schema_version, countries: list[CountryCatalogItemDto],
  sources: list[SourceCatalogItemDto]
}
WorkspaceDto { workspace_id, created_at, schema_version, diagnostics }
SearchTrackSummaryDto {
  track_id, title, lifecycle, current_revision_id, draft_version,
  run_count, created_at, updated_at
}
BriefDraftDto {
  draft_version, based_on_revision_id, summary, preferences,
  updated_at, updated_by
}
BriefRevisionSummaryDto {
  revision_id, revision_number, previous_revision_id, summary,
  preferences, confirmed_at, confirmed_by, run_ids
}
SearchTrackDetailDto {
  summary: SearchTrackSummaryDto,
  current_brief: BriefRevisionSummaryDto | null,
  draft: BriefDraftDto | null
}
ResultCountsDto { fit: int, second_chance: int, all: int }
SourceCoverageDto {
  planned: int, returned_data: int, no_results: int, degraded: int, failed: int
}
RunSummaryDto {
  run_id, track_id, revision_id, revision_number, initiated_by,
  status, quality, integrity, allocated_at, started_at, finished_at,
  counts: ResultCountsDto, source_coverage: SourceCoverageDto
}
ExecutionStepDto {
  execution_id, append_sequence, started_at, finished_at,
  request: SearchRequestDto, quality, resumable
}
RunDetailDto {
  summary: RunSummaryDto,
  projection_state: "pending" | "ready" | "unavailable",
  projection_reason: null | "projection_refresh_failed" |
    "terminal_without_snapshot" | "corrupt_provenance",
  active_job_id, latest_job_id, active_execution_id, active_append_sequence,
  finalized_execution_id, finalized_append_sequence,
  resumable, execution_history: list[ExecutionStepDto]
}
ResultItemDto {
  result_id, category, display_source_id, also_found_in,
  origin_queries, decision_reasons, external_url, display_fields
}
ResultDetailDto {
  run_id, append_sequence, item: ResultItemDto
}
SourceRailItemDto { source_id, display_name, display_order, count }
SourceGroupPageDto {
  source: SourceRailItemDto, items: list[ResultItemDto], next_cursor: str | null
}
GroupedResultsResponse {
  run_id, append_sequence, category, selected_source_id,
  counts: ResultCountsDto, sources: list[SourceRailItemDto],
  groups: list[SourceGroupPageDto]
}
SourceAttemptDto {
  execution_id, append_sequence, query_formulation, attempt_number,
  outcome, rows_written, limit_reached, started_at, finished_at,
  failure_code, failure_message
}
SourceDiagnosticDto {
  source_id, display_name, display_order, aggregate_outcome,
  raw_observation_count, canonical_vacancy_count,
  fit_count, second_chance_count, attempts: list[SourceAttemptDto]
}
RunSourcesResponse {
  run_id, append_sequence, sources: list[SourceDiagnosticDto]
}
ArtifactDescriptorDto {
  artifact_id: str, kind: str, filename: str, media_type: str,
  scope: "run" | "execution", execution_id: str | null,
  append_sequence: int | null,
  projection_role: "run_database_all_sequences" | "latest_projection" | "historical_execution",
  size_bytes: int | null, verified: bool, download_url: str | null
}
```

`SearchRequestDto` and the Brief preference DTO are generated from the existing strict domain serializers and contain every public field. `GET /source-catalog` returns `SourceCatalogResponse`; `GET /search-tracks/{track_id}` returns `SearchTrackDetailDto`; Job acceptance and polling/SSE use the Task 9 `JobAcceptedDto` and `JobSnapshotDto`. `display_fields` is the existing report-shaped safe JSON projection, not an arbitrary filesystem/domain object. `GET /results/{result_id}` returns exactly `ResultDetailDto`; it introduces no undocumented detail bag outside the safe `ResultItemDto.display_fields`. `GET /artifacts` returns `list[ArtifactDescriptorDto]`; scope, nullable execution/sequence, and `projection_role` distinguish the synthetic all-sequence database from latest and historical engine artifacts.

Run summaries contain exact revision link, actor, `fit`/`second_chance`/`all` counts, and qualified source coverage. Runs sort by start/allocation time descending; briefs sort by revision descending.

`GET /runs/{run_id}` always overlays canonical Run fields plus `JobStore.active_for_run(run_id)`/`latest_for_run(run_id)` on the disposable projection. On **any** index miss it calls `WorkspaceApplication.read_run()` before returning 404; a valid canonical Run returns a minimal detail with zero result/source counts and schedules a focused scan, regardless of Job lifecycle. An indexed active Run still receives the live Job/canonical overlay.

`projection_state="ready"` means the index contains the canonical `finalized_append_sequence`; merely having a Run row is insufficient. A brand-new active Run or completed Run awaiting a missing/failed index refresh is `pending`; if the latest terminal Job says `projection_refresh_failed`, expose that reason and `latest_job_id` while continuing periodic rebuild attempts. An active internal append may remain ready for its previous finalized sequence. A terminal/interrupted Run with no finalized snapshot is `unavailable/terminal_without_snapshot`, and an integrity-corrupt Run is `unavailable/corrupt_provenance`; both still expose metadata, diagnostics, resumability, and the latest relevant Job without pretending results are loading forever. A normal service success fsyncs a ready projection before its terminal Job under Task 6's retained guard. For an agent-created active Run there is no Job: expose canonical status plus active Execution/sequence, leave both Job IDs null, and let the client poll Run detail without inventing phase or counters. Result/source endpoints return typed `409 run_projection_pending` for pending and `409 run_results_unavailable` with the stable reason for unavailable.

For result/source/artifact reads, omitted `append_sequence` means the latest verified finalized sequence; an explicit value selects that immutable historical snapshot or returns `404 historical_snapshot_not_found`. When `source` is omitted, Results returns one `SourceGroupPageDto` with its own `next_cursor` for every nonempty contributing source group in the deterministic projected workflow `display_order`. When `source` is present, return only that group's page. The top counts are always Run totals for the selected sequence; source counts are per category. Failed/no-result sources never enter the Results rail and remain in `/sources` diagnostics.

Cursor payload pins `run_id`, finalized `append_sequence`, category, source, normalized `q`, and offset. Validate every field after base64url decoding; changing any pinned filter returns typed `409 stale_result_cursor`, and a default-latest snapshot change returns the same conflict rather than mixing sequences. Clamp `limit` to `1..100`, default `20`.

- [ ] **Step 6: Verify and commit live reflection**

```bash
uv --directory apps/job-harness-local/backend run pytest \
  tests/test_index.py tests/test_scanner.py tests/test_watcher.py tests/test_read_api.py \
  tests/test_jobs.py -q
git add apps/job-harness-local/backend/src/job_harness_local \
  apps/job-harness-local/backend/tests
git commit -m "feat(local-app): project workspace changes into the API"
```

Expected: deleting the index changes no observable product state.

### Task 11: Add mutation routes, opaque artifact downloads, and API contract export

**Files:**
- Modify: `apps/job-harness-local/backend/src/job_harness_local/idempotency.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/artifacts.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/artifacts.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/export_openapi.py`
- Create: `apps/job-harness-local/backend/tests/test_mutation_api.py`
- Create: `apps/job-harness-local/backend/tests/test_artifacts.py`
- Create: `apps/job-harness-local/backend/tests/test_openapi.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/routes/search_tracks.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/routes/runs.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/api_models.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/app.py`

**Interfaces:**
- Consumes: Tasks 8-10.
- Produces: every versioned mutation from the design, stable typed conflicts, safe artifact attachments, and checked-in OpenAPI input for the frontend.

- [ ] **Step 1: Write failing synchronous and asynchronous mutation tests**

Cover:

- `POST /search-tracks`, `PUT /draft`, and `POST /draft/confirm` require Origin, CSRF, and `Idempotency-Key` and replay the exact original response;
- a reused key with different content returns `409 idempotency_conflict`;
- draft version conflict returns `409 draft_conflict`;
- `POST /briefs/{revision}/runs` accepts one strict SearchRequest, preallocates a linked Run, and returns `202` only after the queued Job manifest, `run.json`, idempotency operation, and live application reservation agree;
- append/resume ask `WorkspaceApplication` for an opaque live reservation before returning `202`; busy or stale calls return synchronous typed `409`;
- append accepts the full SearchRequest plus mandatory `expected_append_sequence`, returns `409 stale_append_sequence` for stale callers, and is absent from all frontend navigation data;
- 21 normalized formulations return `422 query_variant_limit` before Run allocation;
- a new-Run request that differs from the selected revision's authoritative translation returns `422 brief_request_mismatch` before Run allocation;
- client payloads containing `actor`, `runs_dir`, absolute paths, unknown fields, or a **non-null** `append_to_run_id` return `422`; the canonical happy-path new-Run JSON includes the required field as `"append_to_run_id": null` and matches the frontend fixture;
- every HTTP-created draft, BriefRevision, and Run records `Actor.FRONTEND`; append/resume Job manifests record `requested_by=frontend` without claiming durable engine-level actor provenance;
- fault injection after operation prepare, canonical mutation, response construction, and before response proves a retry returns exactly the original object/Job and never duplicates it;
- all object lookups use IDs and return `404`, never filesystem disclosure.

- [ ] **Step 2: Complete the crash-safe global idempotency state machine**

Use the Task 9 adapter over `WorkspaceOperationJournal`, keyed only by `idempotency_key_sha256`, so reuse on another route conflicts. It first reserves a deterministic `operation_id`, object IDs/reservation timestamp, and command hash without canonical writes. While holding the target lease, the application recovers any older prepared/applied operation for that target, derives the exact mutable target facts, stores them in `prepared_facts_json`, and transitions this reservation to prepared in one journal transaction before mutation. SearchTrack creation uses `workspace_writer()` because no track exists yet; draft/confirmation use `track_writer()`. In particular, confirmation's previous pointer and revision number are chosen only under `track_writer()`, never during the initial reservation.

Every synchronous mutation also uses `IdempotencyRegistry.claim()`: only the elected owner enters `WorkspaceApplication`; an identical concurrent follower awaits/replays the completed response and never competes for the workspace/track lease. Add concurrent same-key tests for create-track and confirm as well as Task 9's asynchronous acceptance case.

Apply repository mutations with the operation ID as `mutation_id`, then advance `reserved -> prepared -> applied -> completed`. Recovery/retry inspects deterministic target ID plus `last_mutation_id` or the pending-confirmation journal: exact applied state reconstructs the original response, absent state reapplies with the same IDs, and mismatch returns `corrupt_provenance`. Retain completed operations indefinitely in MVP. SQLite and canonical JSON are not called one transaction; correctness comes from the durable state machine, deterministic IDs, and idempotent repository writes.

- [ ] **Step 3: Wire exact mutation routes**

```text
POST /api/v1/search-tracks
PUT  /api/v1/search-tracks/{track_id}/draft
POST /api/v1/search-tracks/{track_id}/draft/confirm
POST /api/v1/search-tracks/{track_id}/briefs/{revision_id}/runs
POST /api/v1/runs/{run_id}/append
POST /api/v1/executions/{execution_id}/resume
```

All adapters use Pydantic models with `extra="forbid"`, assign `Actor.FRONTEND` server-side, call `WorkspaceApplication`/`JobController`, and translate typed domain errors to stable codes: `workspace_unavailable`, `unsupported_workspace_layout`, `unsupported_workspace_filesystem`, `workspace_busy`, `track_busy`, `run_busy`, `stale_append_sequence`, `execution_not_recoverable`, `corrupt_provenance`, `query_variant_limit`, and `brief_request_mismatch`. API/OpenAPI tests exercise concurrent create/confirm/Run conflicts and a blocked filesystem so each domain error returns its documented non-500 envelope. `BriefPreferencesInput` mirrors every Task 2 field and converts to the strict domain type, including the shared normalized 1-20 formulation rule. New-Run and append DTOs contain strict SearchRequest JSON and convert it through `search_request_from_json()`; their canonical serializer includes required nullable `append_to_run_id`, transport input must set it to `null`, and the append target is derived only from the URL by `WorkspaceApplication`. Resume has an empty body. API/OpenAPI tests include one accepted full JSON request with `null` and one rejected request with a non-null client value.

For `POST .../briefs/{revision_id}/runs`, resolve the exact immutable business brief and require the parsed request to equal `brief_preferences_to_search_request(revision.content.preferences)`. Reject a mismatch before preallocating Job/Run IDs or reserving an operation. On equality, preallocate Job and Run IDs in the idempotency operation, then call `WorkspaceApplication.reserve_start_run(track_id, revision_id, request, actor=Actor.FRONTEND, run_id=reserved_run_id, operation_id=reserved_operation_id)`. Persist the exact request hash and IDs before returning `202`; hold the opaque reservation only in the process-owned queue entry, never JSON. `run.json` and later sequence-0 intent must match the durable command.

For append/resume, `JobController.accept_run_mutation()` calls `reserve_append_run()` or `reserve_resume_execution()`; those application methods acquire the lease and perform sequence/recoverability validation. The controller treats `RunCommandReservation` as opaque and hands the same object to `execute_reserved()` without touching or reacquiring its lease. On process crash the guard expires; startup recovery uses the queued manifest to request a new application reservation. If an external writer now owns the Run, recovery marks the Job interrupted with `run_busy_on_recovery` instead of racing or silently rebasing it.

- [ ] **Step 4: Implement safe opaque artifact downloads**

Add:

```text
GET /api/v1/runs/{run_id}/artifacts/{artifact_id}/download
```

Resolve `artifact_id` only through the verified descriptor from `RunSnapshotReader`; then re-resolve the fixed relative path and reject symlinks/traversal. Never verify a pathname and later hand that pathname to `FileResponse`. For every fixed JSON/receipt/report, open the source with no-follow semantics, copy from that open descriptor into a mode-`0600` private cache temp while hashing/counting, require the copied bytes to match the selected descriptor, then stream only that immutable temp and delete it in response cleanup. An atomic replacement before/during capture yields typed `409 artifact_changed` unless the captured bytes still exactly match; a replacement after verification cannot change the response bytes. Return attachments with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`; `report.html` always has `Content-Disposition: attachment`. A barrier test replaces the fixed file after capture/verification and proves the client receives the originally verified bytes (or the typed conflict), never unverified replacement bytes.

For the synthetic Run-scoped `run.sqlite` descriptor, derive the opaque ID from `(run_id, "run_database")` rather than `execution_artifacts`. On download, create a consistent temporary snapshot in the service cache with SQLite's online backup API, verify it opens read-only and belongs to the requested Run, compute response size/hash, stream it as `run.sqlite`, then delete it in response cleanup. Never assign it a fake execution/append sequence and never stream a lone live database file while WAL writes may exist. For historical fixed-file descriptors, return a download only when the current file still matches that historical receipt; otherwise keep the row as non-downloadable provenance.

- [ ] **Step 5: Export and freeze the API contract for frontend generation**

`python -m job_harness_local.export_openapi` serializes `create_app(...).openapi()` deterministically to `apps/job-harness-local/frontend/openapi.json`. Test that every route above exists, every mutation documents `Idempotency-Key` and CSRF responses, and no schema exposes `Path`, `runs_dir`, launch/control token, or absolute artifact path.

- [ ] **Step 6: Verify the complete backend milestone**

```bash
uv --directory apps/job-harness-local/backend run pytest -q
uv --directory apps/job-harness-local/backend run ruff check src tests
uv --directory apps/job-harness-local/backend run mypy src tests --strict
uv --directory apps/job-harness-local/backend run python -m job_harness_local.export_openapi
git diff --check
```

Expected: all commands exit `0`; API tests demonstrate `202 + job_id`, agent rescan visibility, and attachment-only reports.

- [ ] **Step 7: Commit the usable local API**

```bash
git add apps/job-harness-local/backend apps/job-harness-local/frontend/openapi.json
git commit -m "feat(local-app): expose the workspace API"
```

### Task 12: Establish the typed frontend, session bootstrap, and routes

**Files:**
- Create: `apps/job-harness-local/frontend/.nvmrc`
- Create: `apps/job-harness-local/frontend/package.json`
- Create: `apps/job-harness-local/frontend/package-lock.json`
- Create: `apps/job-harness-local/frontend/tsconfig.json`
- Create: `apps/job-harness-local/frontend/tsconfig.app.json`
- Create: `apps/job-harness-local/frontend/vite.config.ts`
- Create: `apps/job-harness-local/frontend/vitest.config.ts`
- Create: `apps/job-harness-local/frontend/playwright.config.ts`
- Create: `apps/job-harness-local/frontend/eslint.config.js`
- Create: `apps/job-harness-local/frontend/index.html`
- Create: `apps/job-harness-local/frontend/src/main.tsx`
- Create: `apps/job-harness-local/frontend/src/api/schema.d.ts`
- Create: `apps/job-harness-local/frontend/src/api/client.ts`
- Create: `apps/job-harness-local/frontend/src/api/session.ts`
- Create: `apps/job-harness-local/frontend/src/api/jobs.ts`
- Create: `apps/job-harness-local/frontend/src/app/router.tsx`
- Create: `apps/job-harness-local/frontend/src/app/AppShell.tsx`
- Create: `apps/job-harness-local/frontend/src/app/Breadcrumbs.tsx`
- Create: `apps/job-harness-local/frontend/src/app/RouteErrorPage.tsx`
- Create: `apps/job-harness-local/frontend/src/styles/tokens.css`
- Create: `apps/job-harness-local/frontend/src/styles/global.css`
- Create: `apps/job-harness-local/frontend/tests/apiClient.test.ts`
- Create: `apps/job-harness-local/frontend/tests/router.test.tsx`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: checked-in `openapi.json` from Task 11.
- Produces: generated `paths` types, one same-origin client, launch-fragment exchange, CSRF/idempotency behavior, Job snapshot hook, and the approved URL hierarchy.

- [ ] **Step 1: Initialize the build-only JavaScript toolchain**

Use Node.js 22 only for development/release builds. Write `22\n` to `.nvmrc` and set `package.json.engines.node` to `>=22.12 <23`; CI reads that file. Install React, React DOM, React Router, and TanStack Query as application dependencies. Install TypeScript, `@types/react`, `@types/react-dom`, Vite React plugin, Vitest, `jsdom`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, ESLint, `@eslint/js`, `typescript-eslint`, `globals`, `eslint-plugin-react-hooks`, Prettier, `openapi-typescript`, and `@playwright/test` as development dependencies; configure Vitest's component-test environment explicitly as `jsdom`. Commit the exact generated `package-lock.json`. Add root ignores for `node_modules/`, `playwright-report/`, `test-results/`, frontend `coverage/`, and `dist/`; packaging scripts address built output explicitly rather than staging it.

Following the current [typescript-eslint flat-config quickstart](https://typescript-eslint.io/getting-started/), `eslint.config.js` imports `@eslint/js`, `typescript-eslint`, `globals`, and `eslint-plugin-react-hooks`; ignores only generated/build directories; applies `js.configs.recommended` plus `...tseslint.configs.recommended` to `.ts/.tsx`; assigns browser globals to `src`/component tests and Node globals to config/E2E files; and applies `reactHooks.configs.flat.recommended.rules` to `.tsx`. The lint test includes a deliberately invalid typed fixture/config assertion so a missing TypeScript parser cannot silently pass by ignoring application files.

Required scripts:

```json
{
  "scripts": {
    "generate:api": "openapi-typescript openapi.json -o src/api/schema.d.ts",
    "dev": "vite --host 127.0.0.1",
    "lint": "eslint . --max-warnings=0",
    "format:check": "prettier --check .",
    "typecheck": "tsc --noEmit -p tsconfig.app.json",
    "test": "vitest run",
    "build": "npm run generate:api && npm run typecheck && vite build",
    "e2e": "npm run build && playwright test",
    "check": "npm run lint && npm run typecheck && npm run format:check && npm run test"
  }
}
```

Vite outputs `dist/`, emits no source maps, and is never launched in the production bundle. Its development-only proxy follows the current [Vite `server.proxy` contract](https://vite.dev/config/server-options.html#server-proxy) and uses this exact security adapter so backend `Host`/`Origin` checks see the same configured loopback origin as production:

```ts
const backendOrigin = 'http://127.0.0.1:8765'

server: {
  host: '127.0.0.1',
  port: 5173,
  strictPort: true,
  proxy: {
    '/api': {
      target: backendOrigin,
      changeOrigin: true,
      configure(proxy) {
        proxy.on('proxyReq', (proxyReq) => {
          proxyReq.setHeader('Origin', backendOrigin)
        })
      },
    },
  },
}
```

This header rewrite exists only in the trusted local dev proxy; the browser client still uses relative URLs, the backend still has one exact allowed origin/host, and production installs no CORS exception. Test the proxied launch/session/mutation flow and reject an unproxied `Origin: http://127.0.0.1:5173` sent directly to the backend.

- [ ] **Step 2: Write failing API-client and bootstrap tests**

Test that:

- every request uses a relative `/api/v1/...` URL and `credentials: "same-origin"`;
- `#launch=<token>` is posted once to `/api/v1/auth/launch`, then removed with `history.replaceState`;
- mutations refuse to send before `/api/v1/session` supplies a CSRF token;
- a logical mutation retry preserves one generated `Idempotency-Key`;
- JSON error codes such as `run_busy`, `draft_conflict`, and `query_variant_limit` survive in `ApiError.code`;
- SSE failure causes polling of the durable Job snapshot without losing the latest known state.

- [ ] **Step 3: Implement one generated-contract client**

`client.ts` parameterizes request/response types from `schema.d.ts`, validates `response.ok`, accepts an optional caller-supplied idempotency key, and never contains filesystem fields or a second hand-written `SearchRequest` model. `session.ts` owns the CSRF token in memory; it does not expose the HttpOnly cookie.

`jobs.ts` opens `EventSource` for notifications, fetches `/jobs/{id}` on connection/reconnection, and polls every two seconds only while SSE is unavailable and the lifecycle is nonterminal. It never issues Cancel.

- [ ] **Step 4: Write failing route tests**

Require:

```text
/                                      Workspace home
/search-tracks/:trackId?tab=runs       SearchTrack default
/search-tracks/:trackId?tab=briefs&brief=<revisionId>  Brief history/exact revision
/runs/:runId                           redirect to /runs/:runId/results?category=fit
/runs/:runId/results?sequence=<n>&category=fit|second_chance|all&source=<id>&vacancy=<id>
/runs/:runId/diagnostics?sequence=<n>
/runs/:runId/artifacts?sequence=<n>
```

Unknown routes render a recoverable route error; API errors never replace the whole browser document with raw JSON.

- [ ] **Step 5: Implement the focused shell**

Use one content column, breadcrumb row, and object-local tabs. Do not create a permanent tree showing SearchTracks, briefs, Runs, sources, and cards simultaneously. Tokens define the report-derived card colors, typography, spacing, focus ring, and responsive breakpoint without copying inline script behavior from `report.html`.

- [ ] **Step 6: Verify and commit the frontend boundary**

```bash
npm --prefix apps/job-harness-local/frontend ci
npm --prefix apps/job-harness-local/frontend run generate:api
npm --prefix apps/job-harness-local/frontend run check
npm --prefix apps/job-harness-local/frontend run build
git add .gitignore apps/job-harness-local/frontend
git commit -m "feat(local-app): establish the typed web client"
```

Expected: checks pass and the production output is static `dist/` assets only.

### Task 13: Implement Workspace home and the progressive SearchTrack page

**Files:**
- Create: `apps/job-harness-local/frontend/src/pages/WorkspaceHomePage.tsx`
- Create: `apps/job-harness-local/frontend/src/pages/SearchTrackPage.tsx`
- Create: `apps/job-harness-local/frontend/src/searchTracks/CreateSearchTrackForm.tsx`
- Create: `apps/job-harness-local/frontend/src/searchTracks/SearchTrackHeader.tsx`
- Create: `apps/job-harness-local/frontend/src/searchTracks/RunsTable.tsx`
- Create: `apps/job-harness-local/frontend/src/searchTracks/BriefHistory.tsx`
- Create: `apps/job-harness-local/frontend/tests/WorkspaceHomePage.test.tsx`
- Create: `apps/job-harness-local/frontend/tests/SearchTrackPage.test.tsx`
- Modify: `apps/job-harness-local/frontend/src/app/router.tsx`

**Interfaces:**
- Consumes: Workspace, SearchTrack, BriefRevision, and Run summary DTOs from Task 10.
- Produces: Workspace-to-SearchTrack drill-down, compact Runs table, and sibling Brief history without a crowded combined hierarchy.

- [ ] **Step 1: Write failing Workspace/SearchTrack navigation tests**

Assert:

- Workspace home lists SearchTracks and no Run-source controls;
- an empty Workspace offers `Новое направление поиска`, creates exactly one SearchTrack through an idempotent mutation, and navigates to it;
- SearchTrack opens on `Раны`/`tab=runs`;
- `tab=briefs&brief=<revision_id>` selects an exact historical BriefRevision and is refresh/back-forward safe;
- Runs sort newest first and each row links to a dedicated Run page;
- a row says `37 подходит · 48 всего`, never a bare `48 результатов`;
- coverage says `7 запланировано · 5 вернули данные · 2 degraded`, never bare `7 источников`;
- every Run links to its exact historical BriefRevision and shows initiating actor when relevant;
- the current Brief link and `Уточнить бриф` action appear in the header;
- clicking a Brief link does not trigger Run navigation.

- [ ] **Step 2: Implement Workspace home and SearchTrack header**

Workspace home shows one concise row/card per SearchTrack with title, intent summary, current revision, last Run, and updated time. Its empty state and primary action render `CreateSearchTrackForm`; one logical submission retains one idempotency key across retries, handles conflict visibly, and navigates only after the server returns the canonical track. SearchTrack header shows title, concise summary, current Brief link, draft status, `Уточнить бриф`, and `Новый запуск текущего брифа`.

- [ ] **Step 3: Implement compact Runs and Brief history tabs**

Runs use a semantic table on desktop and stacked rows on narrow screens, preserving the same fields. Brief history groups immutable revisions with date, actor, concise change summary, and linked Runs. Do not render source rail, request chips, or vacancy cards on this page.

- [ ] **Step 4: Verify and commit progressive navigation**

```bash
npm --prefix apps/job-harness-local/frontend run test -- \
  tests/WorkspaceHomePage.test.tsx tests/SearchTrackPage.test.tsx
npm --prefix apps/job-harness-local/frontend run typecheck
git add apps/job-harness-local/frontend/src apps/job-harness-local/frontend/tests
git commit -m "feat(local-app): add workspace and search history pages"
```

Expected: tests pass and all exact revision links remain deep-linkable.

### Task 14: Build the full brief editor and idempotent Run launch

**Files:**
- Create: `apps/job-harness-local/frontend/src/brief/BriefDraftForm.tsx`
- Create: `apps/job-harness-local/frontend/src/brief/QueryFormulationsEditor.tsx`
- Create: `apps/job-harness-local/frontend/src/brief/CompensationFields.tsx`
- Create: `apps/job-harness-local/frontend/src/brief/LocationScenarioFields.tsx`
- Create: `apps/job-harness-local/frontend/src/brief/SourceScopeFields.tsx`
- Create: `apps/job-harness-local/frontend/src/brief/briefState.ts`
- Create: `apps/job-harness-local/frontend/tests/BriefDraftForm.test.tsx`
- Create: `apps/job-harness-local/frontend/tests/RunLaunch.test.tsx`
- Modify: `apps/job-harness-local/frontend/src/pages/SearchTrackPage.tsx`

**Interfaces:**
- Consumes: full source catalog/capabilities, current draft/revision, and every public `SearchRequest` field except internal `append_to_run_id`.
- Produces: mutable optimistic draft, explicit confirmation, and `202 + job_id` launch of any confirmed current or historical revision.

- [ ] **Step 1: Write failing formulation-boundary tests**

Assert:

- whitespace-only formulations are rejected;
- case-insensitive duplicates collapse using the server-normalized response;
- 1 and 20 unique normalized formulations can be saved and confirmed;
- the 21st unique formulation shows `query_variant_limit` and nothing is truncated;
- formulations preserve order through edit, save, confirmation, and exact request disclosure;
- a shared JSON fixture proves `translateBriefPreferences()` is field-for-field identical to the server's `brief_preferences_to_search_request()`, and a deliberately changed field is rejected before Run allocation.

- [ ] **Step 2: Write failing full-contract round-trip tests**

Fill and read back grades, compensation/currency/period/gross, publication date, excluded companies, substring/regex exclusions with fields/case sensitivity, relocation, work formats, remote/vacancy/employer geographies, repeated OR scenarios, exact source IDs, and source types.

Scenario mode must disable and clear conflicting flat work/location fields only after explicit user confirmation; backend validation remains authoritative. `append_to_run_id`, timeout, retry, concurrency, and path fields never render.

- [ ] **Step 3: Implement normalized brief and request state**

The form creates one typed business brief draft:

```ts
{
  summary,
  preferences: buildBriefPreferences(formState)
}
```

`BriefPreferences` includes every user-facing criterion plus notes, but not `append_to_run_id`. On save, replace client state with the normalized server response. Confirm requires the latest `draft_version` and produces a new immutable revision. `translateBriefPreferences(revision.preferences)` is a mechanical mirror of the Task 2 server mapping: copy every engine criterion, rename `query_formulations` to `query_variants`, omit only `notes`, and set `append_to_run_id` to `null`. Keep the shared mapping fixture generated from the server contract in the frontend tests; backend equality validation remains authoritative and the resulting Run response replaces any client assumptions.

- [ ] **Step 4: Implement idempotent new-Run launch**

Launching a confirmed revision posts the exact translated SearchRequest to its `/runs` endpoint with one idempotency key; it never sends actor or filesystem fields. The server recomputes the authoritative translation, rejects any mismatch before allocation, preallocates the linked Run, and returns a Job whose `run_id` is already known. The UI shows named lifecycle/phase/counters and navigates to that Run. A test pauses watcher/scanner hints, navigates immediately after `202`, and requires `GET /runs/{run_id}` to return the canonical pending/active Job overlay rather than 404. Repeated clicks/retries reuse the same key and IDs. Unmounting or closing the page does not stop the Job.

The UI has `Новый запуск текущего брифа` and a historical revision action; both create a new Run. It exposes neither append nor a fork.

- [ ] **Step 5: Verify and commit complete human search input**

```bash
npm --prefix apps/job-harness-local/frontend run test -- \
  tests/BriefDraftForm.test.tsx tests/RunLaunch.test.tsx
npm --prefix apps/job-harness-local/frontend run typecheck
git add apps/job-harness-local/frontend/src apps/job-harness-local/frontend/tests
git commit -m "feat(local-app): edit briefs and launch searches"
```

Expected: all public SearchRequest fields and 20 formulations round-trip without a second normalization policy in TypeScript.

### Task 15: Implement grouped Run Results, report-style cards, and request audit

**Files:**
- Create: `apps/job-harness-local/frontend/src/pages/RunResultsPage.tsx`
- Create: `apps/job-harness-local/frontend/src/runs/RunLayout.tsx`
- Create: `apps/job-harness-local/frontend/src/results/ResultCategoryTabs.tsx`
- Create: `apps/job-harness-local/frontend/src/results/ResultSourceRail.tsx`
- Create: `apps/job-harness-local/frontend/src/results/SourceResultGroup.tsx`
- Create: `apps/job-harness-local/frontend/src/results/VacancyCard.tsx`
- Create: `apps/job-harness-local/frontend/src/results/VacancyDetails.tsx`
- Create: `apps/job-harness-local/frontend/src/results/SearchRequestDisclosure.tsx`
- Create: `apps/job-harness-local/frontend/tests/RunResultsPage.test.tsx`
- Create: `apps/job-harness-local/frontend/tests/VacancyCard.test.tsx`
- Create: `apps/job-harness-local/frontend/tests/SearchRequestDisclosure.test.tsx`
- Modify: `apps/job-harness-local/frontend/src/app/router.tsx`
- Modify: `apps/job-harness-local/frontend/src/styles/global.css`

**Interfaces:**
- Consumes: grouped Results/read-detail DTOs with fixed Run snapshot sequence.
- Produces: the approved Results-first page with category/source deep links, independent source pagination, safe vacancy details, and exact request audit.

- [ ] **Step 1: Write failing category/source invariant tests**

Assert:

- `/runs/{id}` lands on Results and `category=fit`;
- `Все = Подходит + Второй шанс`, otherwise a visible contract error replaces counts;
- selecting a source writes `source=<id>` and hides every other source group without a new search;
- clearing restores sequential groups in projected workflow `display_order`;
- changing category preserves the source, including an honest zero-result state;
- top tab counts never change with the source filter;
- source-rail counts follow the current category;
- failed/no-result sources are absent from the rail;
- a contributing source with zero in the selected category remains in the rail but creates no empty group in all-source mode;
- `Показать ещё` updates only its source group;
- one canonical vacancy appears in one group only;
- selecting an earlier search step writes `sequence=<append_sequence>` and reloads results, counts, sources, and request audit from that exact historical snapshot;
- no rendered text matches `relevance`, `релевантност`, or `score`.

- [ ] **Step 2: Implement desktop rail and responsive drawer**

The Results layout has a persistent left `Источники результатов` rail on desktop and an accessible drawer on narrow screens. Both manipulate the same URL state. `Все источники` renders one nonempty section per contributing source; selecting one source renders no section chrome for unrelated sources.

- [ ] **Step 3: Port the proven vacancy-card presentation**

Render title/external listing link, company, grade, compensation, geography, workplace/remote evidence, and relocation using the existing report's field hierarchy and refined spacing. React renders scraped strings as text. Render an anchor only when `external_url` independently parses as absolute `http:` or `https:` with a hostname and no credentials; otherwise render plain title text. The link uses `target="_blank" rel="noopener noreferrer"`; a distinct `Подробнее` button writes `vacancy=<result_id>` and opens a local drawer. API and component tests feed `javascript:`, `data:`, protocol-relative, credential-bearing, malformed, and ordinary HTTPS values and prove only the valid HTTP(S) URL becomes a link.

`Второй шанс` always explains `Вакансии, не прошедшие один или несколько критериев` and shows concrete decision reasons. `Все` displays the category badge. Details show `Также найдено в ...` and origin formulations when present.

- [ ] **Step 4: Implement compact and exact request disclosure**

Collapsed `Параметры поиска` shows only active high-signal constraints and may summarize `8 формулировок`. Expanded view lists every formulation in order, every exact criterion, `Не ограничено` for absent audit constraints, and `Весь каталог на момент запуска` plus source-plan link when sources were omitted.

For append history render `Критерии текущего снимка` and ordered, selectable `Шаги поиска 0..N`. The selected step is encoded as `sequence=<append_sequence>`; omission follows the latest finalized snapshot. Internal `append_to_run_id` appears only in provenance, never compact criteria. Results, source rail, counts, vacancy detail, diagnostics/artifacts links, and request audit all pin the same selected finalized append sequence.

- [ ] **Step 5: Verify and commit Results UX**

```bash
npm --prefix apps/job-harness-local/frontend run test -- \
  tests/RunResultsPage.test.tsx \
  tests/VacancyCard.test.tsx \
  tests/SearchRequestDisclosure.test.tsx
npm --prefix apps/job-harness-local/frontend run typecheck
git add apps/job-harness-local/frontend/src apps/job-harness-local/frontend/tests
git commit -m "feat(local-app): browse grouped search results"
```

Expected: tests pass, malicious fixture HTML is escaped, and source/category state survives reload.

### Task 16: Separate Diagnostics, Artifacts, progress, and explicit resume

**Files:**
- Create: `apps/job-harness-local/frontend/src/pages/RunDiagnosticsPage.tsx`
- Create: `apps/job-harness-local/frontend/src/pages/RunArtifactsPage.tsx`
- Create: `apps/job-harness-local/frontend/src/runs/RunNavigation.tsx`
- Create: `apps/job-harness-local/frontend/src/runs/RunProgressBanner.tsx`
- Create: `apps/job-harness-local/frontend/src/runs/SourceDiagnostics.tsx`
- Create: `apps/job-harness-local/frontend/tests/RunDiagnosticsPage.test.tsx`
- Create: `apps/job-harness-local/frontend/tests/RunArtifactsPage.test.tsx`
- Create: `apps/job-harness-local/frontend/tests/RunProgress.test.tsx`
- Modify: `apps/job-harness-local/frontend/src/app/router.tsx`
- Modify: `apps/job-harness-local/frontend/src/runs/RunLayout.tsx`

**Interfaces:**
- Consumes: Run diagnostics, opaque artifact descriptors, Job snapshots, and typed resumability.
- Produces: secondary pages that do not contaminate default Results and an explicit recovery path without Cancel/append controls.

- [ ] **Step 1: Write failing separation and diagnostic tests**

Assert default Results contains no attempt table, stack trace, retry details, raw counters, or artifact downloads. Diagnostics includes every planned success/no-results/partial/failed/blocked/skipped source, expands query-variant attempts, and labels raw observations, canonical vacancies, `Подходит`, and `Второй шанс` separately. Diagnostics and Artifacts preserve the selected `sequence` query parameter from Results; historical engine-artifact rows expose their execution/append sequence and never masquerade as latest, while the synthetic database row is labeled `Полная база Run (все шаги)` with no fake sequence.

- [ ] **Step 2: Implement honest Job progress and finalized-snapshot behavior**

`RunProgressBanner` first reads `RunDetailDto.active_job_id`; when present it fetches that durable Job snapshot, reconnects polling/SSE after reload or browser reopen, and renders lifecycle, named phase, result quality, and engine counters without a percentage. Run routing always loads detail before results. While `projection_state="pending"`, it renders a pending/progress shell and sends zero `/results` requests; `projection_refresh_failed` additionally shows the latest terminal Job failure and Diagnostics link while periodically refetching detail for the rebuild, rather than a blank infinite spinner. Once detail becomes `ready`, it invalidates and loads the Results query. `projection_state="unavailable"` sends zero Results requests and does not poll for an impossible snapshot: it renders `projection_reason`, the `latest_job_id` snapshot when present, Diagnostics, and the resume/new-Run action determined by canonical resumability. During an internal append it leaves the prior finalized results visible with a banner; it switches to the next snapshot only after the server advertises a verified sequence. Missed SSE notifications trigger polling reconciliation. When `active_job_id=null` but canonical Run status/active Execution says an agent-owned search is active, the banner shows only `Поиск выполняется` plus the known Execution/sequence and periodically refetches Run detail; it never fabricates a Job phase or counters.

Tests launch a service Job, pause watcher/index refresh, discard all in-memory mutation state, reload `/runs/{run_id}`, resolve the same durable `active_job_id`, render progress with zero Results calls while pending, and resume polling/SSE; after the verified ready transition exactly one Results load succeeds. A `projection_refresh_failed` fixture remains a canonical HTTP 200 with latest failure visible and later becomes ready after rebuild. Three unavailable fixtures—resumable interruption before any snapshot, nonrecoverable initial failure, and corrupt provenance—make zero Results calls, stop snapshot polling, and show respectively resume, new-Run, or Diagnostics-only recovery. A separate fixture writes an agent-owned active Run with no Job manifest and proves the generic canonical fallback updates through completion.

- [ ] **Step 3: Implement recovery without hidden mutations**

Show `Продолжить прерванный запуск` only when the API says the exact execution is recoverable. Resume posts one idempotent Job and never creates a new Run or append sequence. A nonrecoverable failure offers `Новый запуск этого брифа`. Do not render Cancel, user-facing append, retry-in-place, or fork actions.

- [ ] **Step 4: Implement safe Artifacts page**

List verified artifact name, kind, size, latest/historical meaning, and opaque download URL. Every action downloads an attachment. `report.html` is never put in an iframe, opened at an API-origin document URL, or given the service token/cookie in its content.

- [ ] **Step 5: Verify and commit secondary Run pages**

```bash
npm --prefix apps/job-harness-local/frontend run test -- \
  tests/RunDiagnosticsPage.test.tsx \
  tests/RunArtifactsPage.test.tsx \
  tests/RunProgress.test.tsx
npm --prefix apps/job-harness-local/frontend run check
npm --prefix apps/job-harness-local/frontend run build
git add apps/job-harness-local/frontend
git commit -m "feat(local-app): add run diagnostics and recovery"
```

Expected: all checks pass; Results remains the default clean human view.

### Task 17: Serve the compiled SPA and verify the agent-to-browser convergence flow

**Files:**
- Create: `apps/job-harness-local/backend/src/job_harness_local/static_assets.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/spa.py`
- Create: `apps/job-harness-local/backend/tests/test_static_assets.py`
- Create: `apps/job-harness-local/backend/tests/test_spa.py`
- Create: `apps/job-harness-local/backend/tests/e2e_server.py`
- Create: `apps/job-harness-local/frontend/e2e/auth.setup.ts`
- Create: `apps/job-harness-local/frontend/e2e/search-track-to-run.spec.ts`
- Create: `apps/job-harness-local/frontend/e2e/result-source-deep-link.spec.ts`
- Create: `apps/job-harness-local/frontend/e2e/agent-reflection.spec.ts`
- Create: `apps/job-harness-local/frontend/e2e/security-and-accessibility.spec.ts`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/app.py`
- Modify: `apps/job-harness-local/frontend/playwright.config.ts`

**Interfaces:**
- Consumes: `frontend/dist`, the complete backend, and deterministic canonical Workspace fixtures.
- Produces: one same-origin production application and end-to-end evidence for approved navigation, deep links, external agent writes, and report isolation.

- [ ] **Step 1: Write failing static/SPA routing tests**

Assert:

- `/`, `/search-tracks/...`, and `/runs/.../results?...` return the built `index.html` shell;
- hashed `/assets/...` files receive immutable cache headers, while `index.html` is no-cache;
- `/api/v1/unknown` returns a JSON 404 and never falls through to the SPA;
- non-GET unknown paths do not receive `index.html`;
- missing frontend assets fail readiness/`doctor --bundle` instead of serving a blank page;
- no build-time Vite server or Node process is started.

- [ ] **Step 2: Implement frozen/development static resolution**

`static_assets.py` resolves either the injected development `frontend/dist` or PyInstaller's bundled `job_harness_local/static` root. Task 19 maps the former directory into the latter path as PyInstaller data; there is no implicit copy step. Every resolved path must remain inside that root after symlink resolution. The static shell contains no Workspace data or secrets, so `/`, hashed assets, and `spa.py` history fallback are available without a session for browser GET routes outside `/api/` and `/internal/`; this is required for the first `#launch=` exchange and recoverable expired-session pages. Every data/API/internal route keeps Task 8 authentication, and non-GET history fallback is forbidden.

- [ ] **Step 3: Write the complete browser flows**

`backend/tests/e2e_server.py` accepts `--workspace`, `--port`, and `--launch-token`, creates a deterministic new-layout fixture at that explicit path, then serves the real FastAPI app. Per the current [Playwright web-server contract](https://playwright.dev/docs/test-webserver), `cwd` is resolved relative to its config directory. Playwright also creates an isolated browser context per test and runs files in parallel by default, so the suite consumes the single-use launch token exactly once in a setup project, reuses only its saved HttpOnly-cookie state, and serializes this one mutable Workspace. Commit this configuration:

```ts
webServer: {
  command: 'uv run python tests/e2e_server.py --workspace ../frontend/test-results/e2e-workspace --port 8765 --launch-token e2e-fixed-launch-token',
  cwd: '../backend',
  url: 'http://127.0.0.1:8765/api/v1/health',
  reuseExistingServer: false,
  timeout: 120_000,
},
workers: 1,
fullyParallel: false,
use: {
  baseURL: 'http://127.0.0.1:8765',
},
projects: [
  {
    name: 'setup',
    testMatch: /auth\.setup\.ts/,
  },
  {
    name: 'chromium',
    dependencies: ['setup'],
    use: {
      ...devices['Desktop Chrome'],
      storageState: 'test-results/.auth/session.json',
    },
  },
],
```

`auth.setup.ts` alone opens `/#launch=e2e-fixed-launch-token`, waits until the fragment is removed and an authenticated API call succeeds, then saves `test-results/.auth/session.json`. No functional spec reuses the launch token; every test gets a fresh isolated context seeded from that state. The test entrypoint seeds that fixed value into `LaunchTokenStore` immediately before publishing readiness; production configuration has no fixed-token path. The generated fixture lives under ignored `frontend/test-results/`, so the external CLI flow can target the same known Workspace. The server seeds a distinct deterministic SearchTrack namespace for each spec file, and each test mutates/asserts only its namespace; combined with `workers: 1`, this prevents source counts, revisions, Runs, and watcher events from leaking between flows without adding a product reset API.

Use that deterministic backend fixture and test:

1. launch fragment exchanges for a session and disappears from the URL;
2. Workspace home -> SearchTrack Runs -> dedicated Run Results;
3. `tab=briefs&brief=<revision_id>` and exact Brief link survive reload/back-forward;
4. category/source/vacancy query state survives reload and back/forward;
5. all-source grouping and single-source filtering match counts;
6. exact request disclosure lists all formulations;
7. diagnostics/artifacts remain separate;
8. an external `job-harness-workspace` fixture command creates a revision/Run that appears after watcher/rescan without service mutation;
9. a report download has attachment/sandbox headers and never navigates the privileged page;
10. keyboard/focus behavior works for tabs, source rail/drawer, disclosure, details, and modal close;
11. malicious scraped HTML and non-HTTP(S) listing URLs render inert text and cannot create executable navigation.

- [ ] **Step 4: Verify the integrated development application**

```bash
npm --prefix apps/job-harness-local/frontend run build
uv --directory apps/job-harness-local/backend run pytest \
  tests/test_static_assets.py tests/test_spa.py -q
npm --prefix apps/job-harness-local/frontend exec -- playwright install chromium
npm --prefix apps/job-harness-local/frontend run e2e
```

Expected: all commands pass with the backend serving compiled assets from a temporary loopback port.

- [ ] **Step 5: Commit end-to-end convergence**

```bash
git add apps/job-harness-local/backend apps/job-harness-local/frontend
git commit -m "feat(local-app): serve and verify the workspace UI"
```

### Task 18: Add the foreground macOS launcher and single-Workspace instance control

**Files:**
- Create: `apps/job-harness-local/backend/src/job_harness_local/__main__.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/launcher.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/runtime_state.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/instance_control.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/browser_launcher.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/routes/control.py`
- Create: `apps/job-harness-local/backend/tests/test_launcher.py`
- Create: `apps/job-harness-local/backend/tests/test_instance_control.py`
- Create: `apps/job-harness-local/launcher/Start Job Harness.command`
- Create: `apps/job-harness-local/launcher/tests/fake-runtime.zsh`
- Create: `apps/job-harness-local/launcher/tests/test_start_command.zsh`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/app.py`
- Modify: `apps/job-harness-local/backend/pyproject.toml`
- Modify: `apps/job-harness-local/backend/uv.lock`

**Interfaces:**
- Consumes: a bundled `job-harness-local` executable and one local Workspace path.
- Produces: `job-harness-local serve|doctor|--version`, auto-port foreground service, terminal workspace selection/reopen, start-or-focus semantics, readiness-before-browser, and graceful Ctrl+C.

- [ ] **Step 1: Write and test the exact `.command` launcher**

```zsh
#!/bin/zsh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
RUNTIME="$SCRIPT_DIR/runtime/job-harness-local"

if [[ ! -x "$RUNTIME" ]]; then
  print -u2 "Job Harness runtime is missing or is not executable:"
  print -u2 "$RUNTIME"
  read -r "?Press Enter to close..."
  exit 66
fi

exec "$RUNTIME" serve "$@"
```

The zsh test runs it from a path containing spaces and Unicode, proves arguments are not retokenized, verifies missing runtime exit `66`, and verifies `exec` propagates SIGINT.

- [ ] **Step 2: Write failing launcher/runtime tests**

Require:

```text
job-harness-local serve [--workspace PATH] [--port 0] [--no-open-browser]
job-harness-local doctor --bundle
job-harness-local --version
```

Tests prove first launch prompts in the visible Terminal for a path (including drag-and-drop quoting), later launch offers the remembered Workspace, explicit `--workspace` wins, port `0` chooses a free loopback port, readiness succeeds before browser open, and only then the first launcher requests a fresh launch URL through the same authenticated control path used by later launchers. Output prints only the redacted base origin plus log path/`Ctrl+C`, and browser close does nothing to the process. The full fragment-bearing launch URL is handed only to the browser opener; launch/control tokens never appear in stdout, stderr, logs, or ordinary diagnostics. A fake-clock case holds startup in reconciliation for more than two minutes, then proves the post-readiness handoff succeeds and the newly minted token expires exactly two minutes after that handoff rather than process start.

At this point add and lock the now-valid entrypoint:

```toml
[project.scripts]
job-harness-local = "job_harness_local.launcher:main"
```

- [ ] **Step 3: Enforce the shared active-Workspace filesystem policy**

The launcher calls Task 2's `WorkspaceFilesystemPolicy.assess()` as soon as the user chooses a path and shows its stable warning before app initialization. `WorkspaceApplication` remains the authoritative enforcement point for initialization and every mutation, so the launcher, HTTP routes, and agent CLI cannot drift. A rejected symlink/read-only/cloud/network/removable Workspace returns `unsupported_workspace_filesystem`, leaves canonical/journal files untouched, and is not opened by the MVP service; there is no partially disabled inspection mode to explain or secure.

- [ ] **Step 4: Implement runtime state outside the Workspace**

Use:

```text
~/Library/Application Support/Job Harness/
  recent-workspaces.json
  instances/by-path/<sha256-of-resolved-path>.json
  instances/by-path/<sha256-of-resolved-path>.lock
  workspaces/by-path/<sha256-of-resolved-path>/index.sqlite
~/Library/Caches/Job Harness/
  logs/<date>.log
  downloads/
```

`AppSettings.index_path` is always the resolved-path-hash Application Support path above and is the same value consumed by Task 10's scanner. The canonical `workspace_id` is metadata, not the cache namespace: two portable copies with the same ID but different resolved roots can run independently without sharing an index. Instance/token files are mode `0600`; directories are `0700`. Logs redact tokens, cookies, request bodies, and absolute Workspace paths. The instance file contains PID, numeric loopback host/port, Workspace ID, start time, and a control secret only in this user-private runtime area.

- [ ] **Step 5: Implement start-or-focus without a competing process**

Before opening or initializing `.job-harness`, resolve the user-selected root and acquire `fcntl.flock` keyed by SHA-256 of that exact resolved path. This path-derived key works for an empty first-launch folder where no Workspace ID exists yet and reveals no path in the filename. After initialization, store the Workspace ID inside the adjacent mode-`0600` instance record; keep the same lock for the process lifetime.

After the winning first process reaches ready it calls its own loopback `POST /internal/launch-url` with `X-Job-Harness-Control`, receives a token minted at that moment, and passes the URL directly to the browser opener. If the nonblocking path-lock acquisition reports that the lock is held, the second launcher never quarantines, replaces, initializes, or starts a competing process. It performs a bounded poll for the mode-`0600` instance record to appear and for authenticated loopback health to move from `503 starting` to ready, verifying PID/port at each step; it then calls the same control route. The existing process mints a fresh two-minute one-time launch token and returns a `#launch=` URL; the second launcher passes that URL directly to the browser opener and exits. If the bounded wait ends while the kernel lock is still held, it reports that the existing instance is still starting/unavailable and exits without mutation. Quarantine is permitted only after this process actually acquires the path lock, which proves the prior owner is gone, and then validates any leftover record as stale.

Tests race two first launches against the same empty folder before the winner fsyncs its instance record and again while its health is `503 starting`; both prove that the loser waits/focuses without quarantine and that exactly one Workspace ID/service is created. They also prove distinct resolved paths may start independently and that fragment/control tokens are absent from captured stdout, stderr, log, and diagnostic output while remaining confined to the mode-`0600` instance state or direct browser-opener argument as designed.

The internal route accepts loopback plus the exact control token, is not part of OpenAPI, and never exposes the token to browser JavaScript.

- [ ] **Step 6: Implement foreground shutdown semantics**

SIGINT first stops new acceptance/launch-token minting; it never pre-terminalizes Jobs still owned by a queue entry or pool task. The dispatcher drains queued pre-execution reservations through their explicit shutdown-abort path: under the live guard, start Runs become interrupted/non-resumable while append/resume targets preserve their prior finalized state, then the matching queued Job is fsynced interrupted and the guard closes. Active pool tasks receive cancellation and unwind through `execute_reserved()` while ownership is still valid: WorkspaceApplication writes canonical interruption, the normal `before_run_lease_release()` callback transitions `running -> finalizing -> interrupted`, refreshes failure metadata, fsyncs the terminal Job, and only then releases the guard. Direct fallback terminalization is allowed only after the controller proves no queue entry, pool task, or live guard owns that Job; it never performs terminal-to-finalizing transitions. After all owned callbacks complete, shutdown closes watcher/index, removes current instance state, and exits. It does not label anything cancelled. A hard kill is reconciled on next start.

Fake-clock/barrier Ctrl+C tests cover one queued Job and one running Job. The queued command never enters engine/network work and closes exactly one reservation; the running command writes canonical interruption before exactly one terminal Job transition, never retries forever under the guard, and leaves no nonterminal manifest or heartbeat. A second hard-kill fixture proves startup recovery handles the absence of graceful callbacks.

- [ ] **Step 7: Verify and commit the zero-install launch contract**

```bash
zsh apps/job-harness-local/launcher/tests/test_start_command.zsh
uv --directory apps/job-harness-local/backend run pytest \
  tests/test_launcher.py tests/test_instance_control.py -q
git add apps/job-harness-local/launcher apps/job-harness-local/backend
git commit -m "feat(local-app): add the macOS foreground launcher"
```

Expected: checks pass, including second-launch focus and clean Ctrl+C.

### Task 19: Build architecture-specific standalone macOS distributions

**Files:**
- Create: `apps/job-harness-local/THIRD_PARTY_NOTICES.txt`
- Create: `apps/job-harness-local/packaging/job-harness-local.spec`
- Create: `apps/job-harness-local/packaging/entitlements.plist`
- Create: `apps/job-harness-local/packaging/build_macos.sh`
- Create: `apps/job-harness-local/packaging/assemble_bundle.py`
- Create: `apps/job-harness-local/packaging/verify_bundle.py`
- Create: `apps/job-harness-local/packaging/tests/test_bundle_contract.py`
- Modify: `apps/job-harness-local/backend/pyproject.toml`
- Modify: `apps/job-harness-local/backend/uv.lock`

**Interfaces:**
- Consumes: compiled frontend, backend entrypoint, v2/plugin package resources, and launcher.
- Produces: unsigned-but-self-contained `dist/Job Harness/` for one native architecture, ready for signing.

- [ ] **Step 1: Fix the initial beta platform matrix**

Set the local-app manifest minimum to macOS 15.0 and ship separate `arm64` and `x86_64` builds. Building on a newer minimum or adding older macOS support is a release-contract change because native Python wheels and PyInstaller compatibility must be retested on the oldest supported system.

- [ ] **Step 2: Write failing bundle-contract tests**

Require:

```text
dist/Job Harness/
├── Start Job Harness.command
├── VERSION.json
├── THIRD_PARTY_NOTICES.txt
└── runtime/
    ├── job-harness-local
    └── _internal/
```

Tests assert the executable and launcher bits, version `0.1.0`, expected architecture, absence of `node_modules`, `.venv`, bundled Chromium, `uv`, npm, Docker, or Homebrew runtime calls, and no dynamic library reference to `/opt/homebrew`, `/usr/local`, checkout, or build venv paths. Current v2 uses `HttpxTransport`; Playwright remains a development-only E2E tool and legacy-v1 dependency, not an MVP runtime asset.

- [ ] **Step 3: Build all static and Python resources explicitly**

`build_macos.sh <arm64|x86_64>` performs:

1. `npm ci` and production frontend build;
2. frozen backend dependency sync;
3. PyInstaller `onedir` build with an explicit data mapping from `apps/job-harness-local/frontend/dist` to bundled `job_harness_local/static`;
4. bundle assembly with `.command`, version, and notices;
5. `doctor --bundle` plus offline fixture smoke under `PATH=/usr/bin:/bin:/usr/sbin:/sbin`;
6. architecture and dynamic-link verification.

The PyInstaller spec collects all `job_harness.v2.runtime.sources` submodules plus these package resources:

```text
job_harness/v2/source_catalog.sql
job_harness/v2/runtime/search_service_config.json
job_harness/v2/persistence/graph_schema.sql
job_harness/v2/presentation/report_template.html
apps/job-harness-local/frontend/dist -> job_harness_local/static
CA certificate bundle
```

`doctor --bundle` imports every v2 parser registry, opens all SQL/config/template/static resources, verifies the compiled SPA shell and hashed assets from the bundled static mapping, creates a temporary new-layout Workspace, and runs one deterministic fixture search without network access. A frozen smoke requests a deep SPA URL to prove the actual PyInstaller data mapping works.

- [ ] **Step 4: Verify both unsigned native bundles**

```bash
apps/job-harness-local/packaging/build_macos.sh arm64
python3 apps/job-harness-local/packaging/verify_bundle.py \
  --bundle "dist/Job Harness" --arch arm64

apps/job-harness-local/packaging/build_macos.sh x86_64
python3 apps/job-harness-local/packaging/verify_bundle.py \
  --bundle "dist/Job Harness" --arch x86_64
```

Run each command on its matching macOS runner. Expected: clean offline smoke, correct architecture, and no development-tool dependency.

- [ ] **Step 5: Commit reproducible unsigned packaging**

```bash
git add apps/job-harness-local/packaging apps/job-harness-local/VERSION.json \
  apps/job-harness-local/THIRD_PARTY_NOTICES.txt \
  apps/job-harness-local/backend/pyproject.toml apps/job-harness-local/backend/uv.lock
git commit -m "build(local-app): bundle the standalone macOS runtime"
```

### Task 20: Sign, notarize, gate, document, and smoke-test the release

**Files:**
- Create: `apps/job-harness-local/packaging/sign_macos.py`
- Create: `apps/job-harness-local/packaging/create_dmg.sh`
- Create: `apps/job-harness-local/packaging/notarize_macos.sh`
- Create: `apps/job-harness-local/packaging/verify_dmg.sh`
- Create: `apps/job-harness-local/packaging/create_acceptance_workspace.py`
- Create: `apps/job-harness-local/packaging/release_manifest.py`
- Create: `apps/job-harness-local/packaging/tests/test_release_manifest.py`
- Create: `apps/job-harness-local/backend/src/job_harness_local/acceptance_observation.py`
- Create: `apps/job-harness-local/backend/tests/test_acceptance_observation.py`
- Modify: `apps/job-harness-local/backend/src/job_harness_local/launcher.py`
- Modify: `apps/job-harness-local/packaging/verify_bundle.py`
- Modify: `apps/job-harness-local/packaging/tests/test_bundle_contract.py`
- Create: `scripts/verify_local_app.py`
- Create: `scripts/check_local_app_packaging.py`
- Create: `.github/workflows/local-app-ci.yml`
- Create: `.github/workflows/local-app-candidates.yml`
- Create: `.github/workflows/local-app-release.yml`
- Create: `docs/local-search-workspace-app.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-21-local-search-workspace-service-design.md`
- Modify: `scripts/verify_repo.py`

**Interfaces:**
- Consumes: the two native bundles from Task 19, Apple Developer ID/notary credentials exposed only through the protected `candidate-signing` environment, and manifest-signing credentials exposed only through the protected `release` environment.
- Produces: two notarized DMGs, SHA-256 manifest, deterministic repository gates, a bundled `acceptance-observe` QA command, a hash-bound acceptance Workspace, an exact app-version/tag/candidate-SHA release identity, and end-user launch/workspace documentation.

- [ ] **Step 1: Preserve the approved design and document the shipped contract**

Verify the design header remains `Status: Approved`; update only implementation-result notes without rewriting approved decisions. Document the beta support matrix, exact `Start Job Harness.command` flow, Workspace selection/reopen, where canonical artifacts/runtime logs live, Ctrl+C/restart semantics, new-only layout, unsupported filesystem message, and how agent-created Runs appear. Do not document `uv`, Python, Node, Docker, Homebrew, append, or Cancel as end-user prerequisites/actions. Update root `AGENTS.md` so “not a tool for humans directly” describes the installable plugin runtime only; explicitly document `apps/job-harness-local` as the separate human-facing deliverable over shared `job_harness.workspace`, while preserving `plugins/job-harness` as the sole plugin root.

- [ ] **Step 2: Sign every Mach-O leaf-first and create the DMGs**

Do not use `codesign --deep`. `sign_macos.py` discovers nested Mach-O objects, signs inner Python/native libraries first, then the main executable with Developer ID, hardened runtime, secure timestamp, and the reviewed entitlements file. `get-task-allow` must be absent.

Create:

```text
Job-Harness-0.1.0-macos-arm64.dmg
Job-Harness-0.1.0-macos-x86_64.dmg
```

Define and test these exact script contracts:

```text
python3 sign_macos.py --bundle <dir> --identity <Developer-ID-name> --entitlements <plist>
create_dmg.sh --bundle <dir> --output <candidate.dmg>
notarize_macos.sh --dmg <candidate.dmg> --key <AuthKey.p8> \
  --key-id <id> --issuer <issuer-uuid> --result-json <submission.json>
verify_dmg.sh --dmg <candidate.dmg> --arch <arm64|x86_64>
python3 release_manifest.py prepare-fixture --archive <acceptance-workspace.zip> \
  --candidate-metadata <candidate-metadata.json> --destination <empty-local-dir> \
  --output <prepared-fixture.json>
job-harness-local acceptance-observe --dmg <candidate.dmg> \
  --fixture-archive <acceptance-workspace.zip> \
  --candidate-metadata <candidate-metadata.json> --workspace <path> \
  --workspace-run-id <product-run-id> --result pass --output <observation.json>
python3 release_manifest.py acceptance --dmg <candidate.dmg> \
  --fixture-archive <acceptance-workspace.zip> \
  --candidate-metadata <candidate-metadata.json> \
  --observation <clean-host-observation.json> --output <acceptance.json>
python3 release_manifest.py finalize --dmg <arm64.dmg> --dmg <x86_64.dmg> \
  --candidate-metadata <arm64.metadata.json> \
  --candidate-metadata <x86_64.metadata.json> \
  --acceptance <arm64.json> --acceptance <x86_64.json> --output <manifest.json>
```

Each candidate metadata sidecar contains schema version, candidate workflow run ID, checked-out candidate commit SHA, app version from that commit's `apps/job-harness-local/VERSION.json`, architecture, DMG filename/hash, and acceptance-Workspace archive filename/hash plus seeded Run ID. `create_acceptance_workspace.py` uses the shared agent CLI/application against deterministic fixture transport to create a new-layout portable Workspace with one completed agent-authored BriefRevision/Run/report, then archives it reproducibly. On the trusted release workstation, `release_manifest.py prepare-fixture` verifies the sidecar/archive, rejects absolute paths, `..`, symlinks, duplicate entries, non-private modes, and a nonempty destination, extracts to a fresh local path, opens the Workspace read-only, and proves the sidecar's seeded Run/report exist. The already-prepared folder is transferred to the clean host, so the first execution of candidate code there remains the Finder-launched `.command`. After that Gatekeeper check, the bundled `acceptance-observe` revalidates the sidecar against the actual DMG/archive, verifies that seeded fixture identity plus the selected live product Run through read-only Workspace contracts, captures OS build/CPU automatically, and writes a non-secret observation without requiring Python, `uv`, Node, Docker, Homebrew, `gh`, or a repository checkout. On the trusted release workstation, `release_manifest.py acceptance` revalidates the sidecar, DMG, fixture archive, seeded Run identity, and clean-host observation and records its separate live `workspace_run_id`; `run_id` is never overloaded to mean a GitHub Actions run and a product Run. `finalize` requires both sidecars, both matching acceptances, one commit SHA/version, exact architecture coverage, and versioned filenames before producing the manifest.

Unit tests cover the launcher subcommand dispatch, deterministic fixture/archive identity, trusted-workstation prepare/extraction traversal/symlink/duplicate rejection, nonempty-destination refusal, sidecar/DMG/archive/architecture mismatch, nonexistent seeded/live Run IDs, fake-clock observation timestamps, and release-record verification. `verify_bundle.py` and `test_bundle_contract.py` must prove `acceptance-observe` executes from the frozen runtime under the sanitized PATH; it is a release-QA tool, not a prerequisite for ordinary end users.

The release scripts fail if required signing/notary credentials are absent; they never publish an unsigned fallback. Following [Apple's custom notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow), `notarize_macos.sh` runs `xcrun notarytool submit <dmg> --key <p8> --key-id <id> --issuer <uuid> --wait --output-format json`, saves the response, requires `status=Accepted`, then staples the exact DMG. Verification uses `stapler validate`, leaf and main-executable `codesign --verify --strict`, and Gatekeeper `spctl`. Tests use fake command adapters; real codesign/notarization runs only in protected `candidate-signing` jobs, while final CMS signing/publication runs only in the protected `release` job.

- [ ] **Step 3: Implement the deterministic local-app gate**

`scripts/verify_local_app.py` runs, in order:

```text
workspace Ruff/mypy/unittests
backend lock/Ruff/mypy/pytest
OpenAPI regeneration with clean diff
frontend npm ci/lint/typecheck/format/test/build
compiled SPA/backend integration
Playwright Chromium install plus E2E
launcher zsh tests
acceptance fixture/observation/release-manifest tests
local-app packaging contract tests
existing python3 scripts/verify_v2.py --skip-live
git diff --check
```

`scripts/check_local_app_packaging.py` enforces app version consistency across `VERSION.json`, candidate metadata, DMG filenames, final manifest, release tag/title; Python/npm lockfiles plus `.nvmrc`; required resources; executable mode; the candidate matrix/publication workflow split; the exact candidate-SHA target contract; and separation from plugin version/checker. Wire the deterministic gate into `verify_repo.py` without making signed/notarized release credentials part of ordinary local verification.

- [ ] **Step 4: Add CI and release workflows**

`local-app-ci.yml` runs deterministic Python/frontend/launcher tests on normal changes and installs Playwright Chromium only on CI/dev hosts. Candidate and publication are two separate workflows because a protected-environment approval cannot inject files into an already running workflow. `local-app-candidates.yml` is triggered by `workflow_dispatch` with one `release_ref`. An unprotected `resolve` job with only `contents: read` resolves that input once to a repository commit, validates one lowercase 40-hex SHA, and exposes `candidate_sha`; every signing matrix job declares `needs: resolve` and checks out exactly `${{ needs.resolve.outputs.candidate_sha }}` after any environment-approval delay. A branch or movable tag can therefore move without splitting the two architectures. The matrix uses the [currently published GitHub runner labels](https://github.com/actions/runner-images#available-images) (`macos-15` arm64 and `macos-15-intel` x86_64):

```yaml
matrix:
  include:
    - runner: macos-15
      arch: arm64
    - runner: macos-15-intel
      arch: x86_64
```

Every candidate matrix job declares `environment: candidate-signing`, whose required-reviewer rule gates access to environment secrets `MACOS_DEVELOPER_ID_P12_BASE64`, `MACOS_DEVELOPER_ID_P12_PASSWORD`, `MACOS_NOTARY_KEY_P8_BASE64`, `MACOS_NOTARY_KEY_ID`, `MACOS_NOTARY_ISSUER_ID`, and `MACOS_TEAM_ID`, plus environment variable `MACOS_SIGNING_IDENTITY`. The separate protected `release` environment contains only `MACOS_DEVELOPER_ID_P12_BASE64`, `MACOS_DEVELOPER_ID_P12_PASSWORD`, and `MACOS_SIGNING_IDENTITY`, because publication signs the manifest but never notarizes/rebuilds a candidate. No Apple credential exists as an unprotected repository/workflow secret. Secret material is written only to an ephemeral keychain/temp directory and removed in an always-running cleanup step.

Each matrix job builds and verifies unsigned output, imports the Developer ID certificate, signs, creates/notarizes/staples/verifies its DMG, generates/verifies the deterministic new-layout `acceptance-workspace.zip`, mounts the DMG read-only, launches under a sanitized PATH, checks health/SPA/pre-seeded fixture Run, shuts down with SIGINT, and uploads a [GitHub Actions workflow artifact](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts) named `candidate-<arch>`. It contains only the notarized **candidate DMG**, SHA-256, notarization JSON, `acceptance-workspace.zip`, and `candidate-metadata.json` with candidate workflow run ID, resolved candidate SHA, app version, architecture, DMG identity, and fixture identity. It does not create a final manifest or release.

Set workflow-level `permissions: {}`. Candidate matrix jobs grant only `contents: read`; no candidate job can create a Release. In `local-app-release.yml`, the initial validation job grants `actions: read, contents: read`, while the protected publication job grants exactly `actions: read, contents: write`; all other permissions remain `none`.

Clean-account operators download both artifacts from that candidate run, execute Step 6, and generate `acceptance-arm64.json` and `acceptance-x86_64.json`, each bound to candidate workflow run ID, commit SHA, architecture, and DMG SHA-256. They then start the separate [`workflow_dispatch`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch) `local-app-release.yml` with exactly three non-secret inputs: numeric `candidate_run_id`, `acceptance_arm64_base64`, and `acceptance_x86_64_base64`. The workflow validates the run ID as digits, uses its scoped `actions: read` token to download `candidate-arm64` and `candidate-x86_64` from that completed successful run in the same repository, decodes the two small JSON inputs without logging them, and rejects a workflow-name, commit, architecture, filename, or hash mismatch. After extracting the trusted candidate commit SHA from matching metadata, both validation and publication jobs check out that exact commit before invoking repository release scripts; neither executes tooling from an unrelated default-branch revision or a caller-supplied ref.

Only after those immutable inputs and downloaded candidates validate does the protected `release` environment request human approval. Its publication job runs in a new isolated runner and does not rely on the validation job's filesystem: it independently downloads both `candidate-<arch>` artifacts again by the same candidate run ID, decodes both acceptance inputs again, and repeats workflow name/status, commit SHA, app version, architecture, filename, and SHA-256 validation before any signing/publication action. It then creates its own ephemeral keychain, imports `MACOS_DEVELOPER_ID_P12_BASE64` with its password, grants only `/usr/bin/security` access, and verifies `MACOS_SIGNING_IDENTITY` is present before signing. It generates the final manifest and runs `security cms -S -N "$MACOS_SIGNING_IDENTITY" -i release-manifest.json -o release-manifest.json.cms`. It verifies the signature with `security cms -D` and byte-compares the decoded content before publication.

The release identity is exact: `tag = job-harness-local-v<app-version>` and title `Job Harness Local v<app-version>`, where the version and candidate SHA come only from the mutually matching candidate sidecars. Publication creates that tag/Release with `--target <validated-candidate-sha>`; it never relies on the default branch. Before upload it resolves any existing tag and Release through the GitHub API. Absence permits creation; an existing tag/Release is an idempotent replay only when the tag resolves to the same candidate SHA and every existing asset hash matches the manifest, otherwise the workflow fails without replacing assets or moving the tag. The Release contains the two unchanged DMGs, manifest, CMS signature, acceptance JSON, and notarization records. An `if: always()` cleanup deletes that release-job keychain, decoded certificate, decoded acceptance material, and downloaded candidates just as each matrix job does. Any candidate rebuild has a different run/hash and invalidates acceptance; publication cannot precede both attestations.

`scripts/check_local_app_packaging.py` parses both workflow YAML files and fails unless one unprotected resolve job pins a 40-hex candidate SHA for both matrix jobs, every signing matrix job declares `candidate-signing`, the publication job declares `release`, environment secret sets stay separated as above, the validation job and protected publication job each perform their own download/hash/fixture validation, the protected publication job alone has `contents: write`, candidate metadata drives the versioned tag and `--target` candidate SHA, mismatched existing tags/releases are rejected, and unconditional secret/file cleanup exists.

- [ ] **Step 5: Run deterministic and focused live verification**

```bash
python3 scripts/verify_local_app.py
python3 scripts/verify_v2.py --live-profile light
python3 scripts/verify_repo.py full
git diff --check
```

Expected: deterministic gate exits `0`; the bounded live search succeeds on the host network and writes only the new nested layout.

- [ ] **Step 6: Perform the mandatory clean-account acceptance**

The trusted release workstation first downloads each complete `candidate-<arch>` artifact, runs `prepare-fixture` into a fresh directory, then transfers that architecture's DMG, metadata, original hash-bound archive, verified extracted Workspace, and `prepared-fixture.json` to its clean smoke host. On each architecture, the account has no project checkout or development tools and performs:

1. mount the exact DMG and copy `Job Harness`;
2. as the **first execution of candidate code**, double-click `Start Job Harness.command` through Finder/Gatekeeper;
3. select the already verified/extracted local Workspace and open the seeded Run ID reported by `prepared-fixture.json`;
4. create/confirm a brief with multiple formulations;
5. launch one bounded live search on the host network and retain its product Run ID;
6. close/reopen the browser while the service remains alive;
7. stop with Ctrl+C, restart, and verify history/results;
8. verify a cloud/network/removable Workspace is blocked before mutation;
9. download `report.html` and confirm it opens as a local file without service authority;
10. only after the Finder/Gatekeeper flow, run the copied bundle's `job-harness-local acceptance-observe` command below to produce one small non-secret observation JSON, then transfer only that observation back to the trusted release workstation.

The bundled helper records pass/fail, OS build, CPU architecture, actual DMG/fixture hashes, and the product Workspace Run ID; it requires no external runtime or repository. The trusted workstation converts each observation into an acceptance record with `release_manifest.py acceptance`, which re-verifies the candidate workflow run ID, candidate commit SHA, app version, architecture, filenames, and hashes from the sidecar. The final release manifest embeds both records only after verifying their candidate identities and hashes. Public release remains blocked until both architectures pass.

On each clean host, the only Terminal commands use the copied runtime itself (paths may be supplied by drag-and-drop):

```bash
"/path/to/Job Harness/runtime/job-harness-local" acceptance-observe \
  --dmg "/path/to/Job-Harness-0.1.0-macos-<arch>.dmg" \
  --fixture-archive "/path/to/acceptance-workspace.zip" \
  --candidate-metadata "/path/to/candidate-metadata.json" \
  --workspace "$HOME/Documents/Job Harness Acceptance" \
  --workspace-run-id "<live-product-run-id>" --result pass \
  --output "$HOME/Desktop/acceptance-observation.json"
```

The trusted-workstation handoff is executable and deliberately separate from the clean-host smoke steps:

```bash
gh run download "$CANDIDATE_RUN_ID" --name candidate-arm64 --dir clean-test/arm64
gh run download "$CANDIDATE_RUN_ID" --name candidate-x86_64 --dir clean-test/x86_64
python3 apps/job-harness-local/packaging/release_manifest.py prepare-fixture \
  --archive clean-test/arm64/acceptance-workspace.zip \
  --candidate-metadata clean-test/arm64/candidate-metadata.json \
  --destination clean-test/arm64/prepared-workspace \
  --output clean-test/arm64/prepared-fixture.json
python3 apps/job-harness-local/packaging/release_manifest.py prepare-fixture \
  --archive clean-test/x86_64/acceptance-workspace.zip \
  --candidate-metadata clean-test/x86_64/candidate-metadata.json \
  --destination clean-test/x86_64/prepared-workspace \
  --output clean-test/x86_64/prepared-fixture.json
# Transfer each complete directory to its clean host; after Steps 1-10, copy back:
#   observations/arm64.json
#   observations/x86_64.json
python3 apps/job-harness-local/packaging/release_manifest.py acceptance \
  --dmg clean-test/arm64/Job-Harness-0.1.0-macos-arm64.dmg \
  --fixture-archive clean-test/arm64/acceptance-workspace.zip \
  --candidate-metadata clean-test/arm64/candidate-metadata.json \
  --observation observations/arm64.json --output acceptance-arm64.json
python3 apps/job-harness-local/packaging/release_manifest.py acceptance \
  --dmg clean-test/x86_64/Job-Harness-0.1.0-macos-x86_64.dmg \
  --fixture-archive clean-test/x86_64/acceptance-workspace.zip \
  --candidate-metadata clean-test/x86_64/candidate-metadata.json \
  --observation observations/x86_64.json --output acceptance-x86_64.json
ARM64_ACCEPTANCE="$(base64 < acceptance-arm64.json | tr -d '\n')"
X86_64_ACCEPTANCE="$(base64 < acceptance-x86_64.json | tr -d '\n')"
gh workflow run local-app-release.yml \
  -f candidate_run_id="$CANDIDATE_RUN_ID" \
  -f acceptance_arm64_base64="$ARM64_ACCEPTANCE" \
  -f acceptance_x86_64_base64="$X86_64_ACCEPTANCE"
```

Acceptance JSON contains no secret. The publication workflow redacts/unsets decoded input variables after writing mode-`0600` temporary files and deletes them in its unconditional cleanup.

- [ ] **Step 7: Self-review the final patch and commit the release system**

```bash
git status --short
git diff --stat HEAD
python3 scripts/check_no_compat_comments.py
python3 scripts/check_local_app_packaging.py
git diff --check
git add .github/workflows apps/job-harness-local scripts docs AGENTS.md README.md
git commit -m "build(local-app): gate the notarized macOS release"
```

Expected: only intentional product, verification, documentation, and release files are staged; `.superpowers/` and `.playwright-cli/` remain untracked and unstaged.
