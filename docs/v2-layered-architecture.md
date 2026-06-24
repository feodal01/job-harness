# V2 Layered Architecture

This document describes how the contract-first `job_harness.v2` package maps to a layered architecture.

## Current Layer Map

| Layer | V2 package/modules | Responsibility |
| --- | --- | --- |
| Presentation | `job_harness.v2.cli`; `job_harness.v2.presentation` | CLI, markdown, and HTML report surfaces call v2 use cases and read processed data from persistence-facing APIs. They must not parse source pages or write raw evidence directly. |
| Business / Application | `job_harness.v2.application`; `job_harness.v2.runtime.orchestrator`, `catalog`, `retry` | Select supported sources, dispatch independent source attempts, apply retry policy, classify source outcomes, coordinate raw evidence writes through ports, and invoke post-processing. |
| Source Catalog | `job_harness.v2.source_catalog` plus `job_harness.v2/source_catalog.sql` | SQLite-backed ORM-style table of supported source rows: source id, source type, transport, country scope, limits, capabilities, fixture requirements, and fixture cases. The SQL file is the source of truth; Python only maps rows into contracts. |
| Domain Contracts | `job_harness.v2.contracts` | Stable request, source, scraper, raw record, fixture, capability, and outcome contracts. This layer is pure and must not import runtime adapters. |
| Layer Ports | `job_harness.v2.ports` | Explicit handles between layers: source artifact fetching, raw corpus writing, run-store lifecycle, and run-store factory creation. |
| Data Access / Adapters | `job_harness.v2.runtime.sources`, `http` | Source-specific parsers and HTTP artifact fetching. These modules implement external communication details behind contracts or ports. |
| Shared Serialization | `job_harness.v2.serialization` | JSON-safe conversion for contract records and payloads used across application, persistence, post-processing, and presentation without importing runtime internals. |
| Persistence | `job_harness.v2.persistence`; `job_harness.v2/persistence/schema.sql` | SQLite-backed run store for `runs`, `append_attempts`, `raw_listings`, `source_attempts`, `run_manifest`, and `processed_results`. Append sequence allocation is transactional. |
| Database / Storage | `source_catalog.sql`; per-run `run.sqlite`; real source fixture files under `tests/v2/fixtures` | Source metadata, durable evidence, derived processed snapshots, and deterministic parser test inputs. No external database server is required. |

## Dependency Rule

Dependencies point inward/downward:

- contracts import only contracts;
- ports import only domain contracts and shared serialization types;
- the source catalog imports only contracts and the SQLite/data-loading standard library;
- source adapters import contracts and source-local helpers;
- source adapters read metadata from the source catalog instead of declaring source properties locally;
- HTTP and persistence adapters do not import the orchestrator;
- the orchestrator depends on contracts and ports, not concrete HTTP or source adapter implementations;
- runtime modules do not import the persistence package; persistence satisfies the runtime writer port from the application layer;
- post-processing does not read or write files; it receives raw/source-attempt payloads and returns a processed payload;
- filesystem and database access are restricted by an architecture boundary test to CLI/application layout, source catalog loading, persistence, and presentation template rendering;
- v2 source code must not import legacy `job_harness` modules.

The rule is enforced by `plugins/job-harness/tests/v2/test_architecture_boundaries.py` and `python3 scripts/verify_v2.py`.

## Current Fit

The new v2 code is close to a layered architecture, but it is not a full four-layer application yet:

- The presentation layer is the v2 CLI plus `job_harness.v2.presentation`; MCP remains outside this v2 package.
- Business orchestration exists and is testable through fake fetchers, fake sources, and a writer port.
- Data access is split into source parsers, HTTP fetching, and SQLite persistence.
- The database layer is a per-run SQLite file plus SQL-backed source catalog metadata.

This is intentional for the current phase: v2 establishes contracts, independent source execution, transactional run persistence, post-processing, and source fixture discipline before adding broader presentation surfaces.
