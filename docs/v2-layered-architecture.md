# V2 Layered Architecture

This document maps the implemented independent-scraper graph to repository
layers. The full contract and diagrams are in
[`v2-to-be-scraper-contract-architecture.md`](./v2-to-be-scraper-contract-architecture.md).

## Current Layer Map

| Layer | V2 package/modules | Responsibility |
| --- | --- | --- |
| Presentation | `cli`, `presentation` | CLI commands and HTML/Markdown rendering from the clean final public projection. |
| Application | `application`, `runtime.graph_pipeline` | Execution creation, source selection, graph composition, final report creation. |
| Coordination | `runtime.graph_coordinator`, `runtime.executors`, `runtime.final_assembly` | Event consumption, dependency scheduling, leased parser execution, global drain barrier. |
| Independent scraper contracts | `contracts.independent`, `contracts.graph`, `contracts.facts` | Typed inputs/results, manifests, pinned parser references, task/event/fact contracts. |
| Scraper bundles | `runtime.source_bundles`, `runtime.source_registry`, `runtime.sources` | Self-contained listing/detail/profile/site implementations and pure initial-input planning. |
| Runtime adapters | `runtime.parser_runtime`, `runtime.resource_gate`, `runtime.http` | Safe HTTP access, deployment-scoped concurrency/pacing, transport implementation. |
| Derivation and selection | `runtime.fact_derivers`, `runtime.selection`, `runtime.public_projection` | Versioned derived facts, preliminary/final decisions, stable public rows. |
| Persistence | `persistence.graph_repository`, `persistence/graph_schema.sql` | Durable invocations, immutable observations, identities, dependencies, events, evaluations, final snapshots. |
| Catalog | `source_catalog`, `source_catalog.sql` | Implemented source inventory, source types, limits, capabilities, and fixture requirements. |

## Dependency Rule

- Contracts do not import runtime, persistence, presentation, or filesystem code.
- Scrapers receive typed input and `ParserRuntime`; they do not read graph storage
  or create downstream tasks.
- `ParserRegistry` performs exact pinned implementation lookup. URL routing is a
  coordinator planning operation, never a task-runner fallback.
- Persistence owns SQLite and transaction boundaries. Runtime policy is injected
  into repository operations through typed callbacks.
- Presentation reads only execution-scoped final payloads and does not expose
  cursors, retries, pacing, raw source arrays, or search-query internals per row.
- Source modules do not import legacy v1 code.

These rules are enforced by
`plugins/job-harness/tests/v2/test_architecture_boundaries.py` and
`scripts/verify_v2.py`.

## Storage Boundaries

Each run has one `run.sqlite`. Identity resources may be reused by append
executions, while parser observations, tasks, events, fact sets, evaluations,
and final snapshots remain execution-scoped. The deployment limiter is a
separate shared database at `{runsRoot}/_runtime/resource-gate.sqlite`; no
network call keeps the graph transaction open.
