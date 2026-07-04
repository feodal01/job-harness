# Implement Workplace Scope Request Contract

This ExecPlan records the current v2 workplace and geography request contract.
The older applicant-geography flag design has been replaced directly; do not
reintroduce a parallel public request model.

## Purpose / Big Picture

`job-harness-v2 search` expresses workplace intent with three explicit fields:

- `work_formats`: requested workplace formats, using `remote`, `hybrid`,
  `office`, or `unknown`; `unknown` must be paired with a concrete format.
- `remote_scopes`: remote eligibility only, using `global`,
  `country:<code>`, `region:<code>`, or `unknown`; `unknown` must be paired
  with a concrete scope.
- `vacancy_geographies`: vacancy market, office, or source-card location,
  using `country:<code>`, `region:<code>`, `city:<name>`, or `unknown`;
  `unknown` must be paired with a concrete geography.

The public request, CLI, source criteria catalog, post-processing, reports,
agent workflow text, tests, and benchmark profiles must all use these names and
value shapes. Physical workplace formats never appear in `remote_scopes`.

## Current Behavior

A global remote request is explicit:

```bash
uv --directory plugins/job-harness run job-harness-v2 search \
  --queries "QA | AQA" \
  --work-format remote \
  --remote-scope global \
  --vacancy-geography country:RU
```

A country-compatible remote request is also explicit:

```bash
uv --directory plugins/job-harness run job-harness-v2 search \
  --queries "QA | AQA" \
  --work-format remote \
  --remote-scope country:RU
```

`remote_scopes=["global"]` is global-only. Country and region scope requests use
scope intersection, so a globally remote row satisfies a country or region
remote-scope request. `vacancy_geographies` remains a separate location
constraint: global remote does not satisfy a requested vacancy geography unless
the row also has matching vacancy geography evidence.

## Decision Log

- `work_formats` is the only public workplace-format filter. Requesting
  `remote`, `hybrid`, or `office` is always explicit; `unknown` can only expand
  a concrete format request.
- `remote_scopes` contains only remote eligibility. It accepts `global`,
  `country:<code>`, `region:<code>`, and `unknown`.
- `vacancy_geographies` contains vacancy location intent. City filters are
  encoded as `city:<name>` in this same field.
- Unknown evidence is not included by default. A request must include `unknown`
  explicitly alongside a concrete value for that field to keep unknown rows.
- Source-native broad remote parameters are collection hints only. Final keep
  or remove decisions are made by post-processing from normalized row facts.
- The post-processing filter is represented as a small AST so request
  parameters compile into one explicit filtering rule tree.

## Implementation Surfaces

- Public request: `plugins/job-harness/src/job_harness/v2/contracts/search.py`
- Enums and criteria: `plugins/job-harness/src/job_harness/v2/contracts/enums.py`
  and `plugins/job-harness/src/job_harness/v2/contracts/criteria.py`
- CLI: `plugins/job-harness/src/job_harness/v2/cli.py`
- Source catalog: `plugins/job-harness/src/job_harness/v2/source_catalog.sql`
- Filter AST: `plugins/job-harness/src/job_harness/v2/postprocessing/filter_ast.py`
- Row normalization and result payloads:
  `plugins/job-harness/src/job_harness/v2/postprocessing/pipeline.py`
- Report UI: `plugins/job-harness/src/job_harness/v2/presentation/report_template.html`
- Agent workflow: `plugins/job-harness/skills/job-search-workflow/SKILL.md`
- Benchmark profiles: `benchmarks/v2-search-speed-*.json`

## Acceptance Checks

- CLI help exposes `--work-format`, `--remote-scope`, and
  `--vacancy-geography`.
- CLI help does not expose the retired workplace/geography flags.
- `SearchRequest` rejects retired request keyword arguments.
- `source_catalog.sql` declares only current v2 criteria.
- Processed result payloads serialize `work_formats`, `remote_scopes`, and
  `vacancy_geographies` in `search_request`.
- Reports show `Work format`, `Remote scope`, and `Vacancy geography`.
- Benchmark profiles use only current CLI flags.
- `python3 scripts/verify_v2.py --skip-live` passes.

## Verification Commands

Run from the repository root:

```bash
uv --directory plugins/job-harness run job-harness-v2 search --help
uv --directory plugins/job-harness run ruff check src/job_harness/v2 tests/v2 --select E,F,W,I,B,UP,C4,SIM,RET,ARG,PLC,PLE,PLR --ignore PLR0911,PLR0913
python3 scripts/verify_v2.py --skip-live
git diff --check
```
