# V2 Layered Architecture

This document describes how the contract-first `job_harness.v2` package maps to a layered architecture.

## Current Layer Map

| Layer | V2 package/modules | Responsibility |
| --- | --- | --- |
| Presentation | Not implemented in v2 yet | CLI, MCP tools, and final agent-facing formatting will live above v2. They should call v2 use cases and never parse source pages or write raw files directly. |
| Business / Application | `job_harness.v2.runtime.orchestrator`, `catalog`, `retry` | Select supported sources, dispatch independent source attempts, apply retry policy, classify source outcomes, and coordinate raw evidence writes through ports. |
| Source Catalog | `job_harness.v2.source_catalog` plus `job_harness.v2/source_catalog.sql` | SQLite-backed ORM-style table of supported source rows: source id, source type, transport, country scope, limits, capabilities, fixture requirements, and fixture cases. The SQL file is the source of truth; Python only maps rows into contracts. |
| Domain Contracts | `job_harness.v2.contracts` | Stable request, source, scraper, raw record, fixture, capability, and outcome contracts. This layer is pure and must not import runtime adapters. |
| Data Access / Adapters | `job_harness.v2.runtime.sources`, `http`, `corpus`, `serialization` | Source-specific parsers, HTTP artifact fetching, JSONL corpus writing, and serialization. These modules implement external communication details behind contracts or ports. |
| Database / Storage | `source_catalog.sql`; run directories containing `raw-listings.jsonl`, `source-attempts.jsonl`, `run-manifest.json`, `processed-results.json`; real source fixture files under `tests/v2/fixtures` | Source metadata, durable evidence, and deterministic parser test inputs. No database server exists yet; catalog rows are loaded into in-memory SQLite. |

## Dependency Rule

Dependencies point inward/downward:

- contracts import only contracts;
- the source catalog imports only contracts and the SQLite/data-loading standard library;
- source adapters import contracts and source-local helpers;
- source adapters read metadata from the source catalog instead of declaring source properties locally;
- HTTP and corpus adapters do not import the orchestrator;
- the orchestrator depends on contracts and ports, not concrete HTTP or source adapter implementations;
- v2 source code must not import legacy `job_harness` modules.

The rule is enforced by `plugins/job-harness/tests/v2/test_architecture_boundaries.py` and `python3 scripts/verify_v2.py`.

## Current Fit

The new v2 code is close to a layered architecture, but it is not a full four-layer application yet:

- The presentation layer is still legacy CLI/MCP code outside v2.
- Business orchestration exists and is testable through fake fetchers, fake sources, and a corpus writer port.
- Data access is split into source parsers, HTTP fetching, and file persistence.
- The database layer is currently explicit files, not a DB.

This is intentional for the current phase: v2 first establishes contracts, independent source execution, raw evidence persistence, and source fixture discipline. Presentation and post-processing can be moved on top after the v2 core boundary is stable.
