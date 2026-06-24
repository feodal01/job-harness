# v2 append: concurrency gaps and live e2e false failure

Status: resolved in v2 SQLite run-store migration
Discovered: 2026-06-22  
Area: `plugins/job-harness/src/job_harness/v2/`, `scripts/verify_v2.py`, `scripts/v2_live_e2e.py`

## Summary

Two related issues around v2 append mode:

1. **Live e2e gate false failure** — `verify_v2.py` (live profile) can report `processed_result_count mismatch` even when append behavior is correct.
2. **No run-level serialization** — parallel append searches against the same `run_id` have undefined behavior: duplicate `append_sequence`, last-writer-wins on `processed-results.json` and `run-manifest.json`, and possibly stale processed output.

In the former JSON/JSONL implementation, sequential append worked, but parallel
append to one run was not safe.

## Resolution

The v2 run store now uses a per-run `run.sqlite` database. `append_sequence`
allocation happens inside `SqliteRunStore.reserve_append_attempt()` with
`BEGIN IMMEDIATE`, and processed results are stored per `(run_id,
append_sequence)` in the `processed_results` table. Live e2e validation reads
the matching processed snapshot from SQLite instead of comparing an in-memory
count to a later overwritten file.

The original analysis below describes the former JSON/JSONL artifact contract.

---

## Issue 1: Live e2e validates stale in-memory counts against overwritten artifact

### Symptom

`python scripts/verify_v2.py` fails in the live profile with:

```text
v2 live e2e failed: processed_result_count mismatch
```

Deterministic checks (`verify_v2.py --skip-live`, `verify_repo.py deterministic`) pass.

### Root cause

`scripts/v2_live_e2e.py` runs two searches in one subprocess:

1. **First search** — new run, `append_sequence=0`, writes `processed-results.json`.
2. **Second search** — `append_to_run_id=first.run_id`, `append_sequence=1`, **re-reads full raw corpus, re-postprocesses, overwrites the same `processed-results.json`**.

Both executions share one `processed_results_path` per run (`RunLayout` / `RunPaths`).

`verify_v2.py` then validates the report:

- For `first`, `_validate_live_processed_artifact` compares `first["processed_result_count"]` (in-memory count from when search 1 finished) with `result_count` read from `artifacts["processed_results"]` on disk.
- By that time the file already reflects search 2 (full corpus + second request filters), so counts diverge.

This is a **validator ordering / artifact identity bug**, not a post-processing regression.

### Intended append semantics (not a bug)

Per `docs/search-system-spec.md` (Append Mode):

- `raw-listings.jsonl` and `source-attempts.jsonl` are append-only.
- Post-processing is re-run over the **entire** raw corpus with the **current** `SearchRequest`.
- `processed-results.json` is a single canonical “current view” per run, atomically overwritten — not a per-append history.

The e2e failure comes from treating that one shared file as if it were frozen at append_sequence 0.

### Suggested fixes

Any of:

- Validate `first` **before** starting the append search inside `v2_live_e2e.py`, or emit separate artifact paths per validation snapshot.
- In `_validate_live_processed_artifact`, when `append_sequence > 0` is not the case for `first`, compare against a snapshot captured at end of run 1 (or skip disk comparison for `first` and only assert append invariants via `_validate_append_artifacts`).
- Compare `processed["append_sequence"]` to `payload["append_sequence"]` and fail fast if the on-disk file is from a later append.

---

## Issue 2: Parallel append to the same `run_id` is racy

### Symptom (if user/agent runs two append searches concurrently)

- Duplicate `append_sequence` values in `raw-listings.jsonl`.
- `run-manifest.json` reflects only the last finished search (`replace_run_manifest` overwrites).
- `processed-results.json` reflects whichever post-process finished last; may miss raw lines still being written by the other search, or apply only the last request’s filters to a partial corpus snapshot.
- No torn JSON on read (atomic write via temp + `os.replace`), but **lost update** / stale view is possible.

### Root cause

**`append_sequence` allocation** — at search start, without locking:

```python
# application.py → run_layout.py
layout.next_append_sequence(request.append_to_run_id)
```

Reads `latest_append_sequence` from manifest (or max from raw JSONL). Two concurrent callers can both read the same value before either updates the manifest.

**Raw corpus writer lock is process-local only:**

```python
# corpus.py — threading.Lock inside one RawCorpusWriter instance
with self._lock:
    os.write(fd, encoded)
```

- Safe for concurrent writes from threads sharing one writer (see `test_concurrent_raw_writes_preserve_line_integrity`).
- **Not** safe across two CLI invocations, two MCP clients, or two `V2SearchApplication.search()` calls in separate processes: each opens its own writer; `O_APPEND` preserves line boundaries but does not coordinate sequence numbers or post-process ordering.

**Post-process has no run lock:**

```python
# pipeline.py
raw_records = _read_jsonl_objects(raw_listings_path)
# ...
_write_json_atomic(output_path, payload)
```

Two post-process runs on the same path → last writer wins.

### Current assumed contract

One active mutation per `run_id` at a time. Append only after the previous search on that run has completed. Independent searches should use a new `run_id`, not append.

This matches the agent workflow (poll `search_status` → append), but is **not enforced** in v2 application/CLI code.

### Suggested fixes

Pick one or combine:

| Approach | Pros | Cons |
|----------|------|------|
| Run-dir file lock (`fcntl` / `filelock`) for entire append | Simple, works across processes | Blocks parallel appends by design |
| Busy rejection (`RunBusyError` if lock held) | Clear agent signal | Requires MCP/CLI to surface error |
| Serialize append in application singleton (MCP only) | No FS changes | Does not help multi-process CLI |
| Per-append processed snapshots (`processed-results-{n}.json`) | Audit trail | Contract change; agents must read “latest” |

Minimum hardening: document + reject concurrent append with an explicit error when lock cannot be acquired.

---

## References

| File | Relevance |
|------|-----------|
| `plugins/job-harness/src/job_harness/v2/application.py` | Resolves paths + `append_sequence`, orchestrate → postprocess |
| `plugins/job-harness/src/job_harness/v2/runtime/run_layout.py` | `next_append_sequence()` |
| `plugins/job-harness/src/job_harness/v2/runtime/corpus.py` | Append-only JSONL, thread lock |
| `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py` | Full corpus recompute, atomic overwrite |
| `plugins/job-harness/src/job_harness/v2/runtime/orchestrator.py` | Manifest write with `latest_append_sequence` |
| `scripts/v2_live_e2e.py` | Two sequential live searches, one report |
| `scripts/verify_v2.py` | `_validate_live_processed_artifact`, `_validate_append_artifacts` |
| `docs/search-system-spec.md` | Append mode semantics |
| `plugins/job-harness/tests/v2/test_application_cli.py` | Sequential append happy path |

## Verification notes

- `verify_v2.py --skip-live`: passes (164 v2 tests).
- `verify_v2.py` with live e2e: fails with `processed_result_count mismatch` on `first` validation (Issue 1).
- Sequential append unit test `test_application_runs_search_save_postprocess_and_append` passes — expected behavior when searches do not overlap.
