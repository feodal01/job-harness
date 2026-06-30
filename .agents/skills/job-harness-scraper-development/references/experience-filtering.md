# Grade Filtering

job-harness v2 filters grade by exact requested values, not by minimum
seniority. The public v2 request field is `SearchRequest.grades`; the CLI flag
is repeatable `--grade`.

Supported values are defined by `job_harness.v2.contracts.Grade`:

- `intern`
- `junior`
- `middle`
- `senior`
- `lead`

For example, `--grade middle` means exactly middle. It does not include
senior-only listings. Multi-grade searches repeat the flag, for example
`--grade middle --grade senior`.

## Public API

CLI:

- `job-harness-v2 search --queries "QA | SDET" --grade middle`
- `job-harness-v2 search --queries "QA" --grade middle --grade senior`

Contract:

- `SearchRequest(grades=(Grade.MIDDLE,))`
- `SearchRequest(grades=(Grade.MIDDLE, Grade.SENIOR))`

Omitting `grades` means no grade filter. Invalid grade values are rejected by
the CLI enum parser or by `SearchRequest` construction.

## Source Capability Contract

Each v2 source declares grade support in `source_catalog.sql` through the
`grades` `SearchCriterion` row:

- `native_request`: the source can enforce the requested grade before returning
  listings, for example a URL/API qualification parameter.
- `structured_output`: the source exposes a stable structured grade field that
  post-processing can filter.
- `unsupported`: the source does not expose grade as a reliable structured
  source fact. Text still remains available for query matching and future
  enrichment, but the source must not claim native grade support.

Source descriptors derive from the catalog. Do not keep source-local capability
lists in scraper code.

## Parser Responsibilities

Scrapers may populate `RawListing.native_grade` only when the source exposes a
structured grade or qualification value. Examples include Habr Career
`qualification` and comparable platform-native grade fields.

Scrapers must not estimate grade from ordinary prose inside source modules.
Keep role text in `title`, `description`, `requirements`, `skills`, `raw_text`,
or `raw`. Downstream post-processing owns filtering and any future text
enrichment.

When a source supports grade via a native request parameter, add request mapping
tests proving that the requested `Grade` changes the outgoing URL/API payload.
When a source supports grade through structured output, add real fixture tests
proving `native_grade` is extracted from the captured source artifact.

## Filtering Semantics

Current v2 post-processing keeps a listing for a grade-filtered request only
when `row["native_grade"]` exactly matches one of the requested grades. A
senior-only listing does not pass `grades=(Grade.MIDDLE,)`.

If a source does not expose grade and a grade filter is requested, the source
should still run unless a broader source-selection policy skips it. Its
unsupported grade capability is recorded in criteria diagnostics. The
post-processing plan can mark text enrichment as required when enough text was
collected, but unsupported source capability must not fabricate grade facts.

## Source Notes

HH-family sources and Habr Career can apply a native grade request when exactly
one requested grade maps to the source's native parameter. Multi-grade behavior
should be explicit in the source tests: either issue the source-supported
request shape or fetch more broadly and filter locally through structured
`native_grade`.

Company career sources often do not expose stable grade metadata. They should
emit the exact source text and leave `native_grade=None` unless the ATS/API has a
structured grade field.

Legacy v1 names such as `experience_levels`, `JobListing.experience`,
`FilterSupport`, `search_start`, and `company_careers` belong to the v1 MCP/CLI
surface. Do not use them as guidance for v2 scraper or post-processing changes.
