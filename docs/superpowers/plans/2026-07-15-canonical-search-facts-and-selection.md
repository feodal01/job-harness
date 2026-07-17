# Canonical Search Facts And Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ambiguous search and output fields with dimensioned canonical facts, then make filtering, ranking, graph selection, and public output use the same deterministic evidence.

**Architecture:** Source parsers continue to emit explicit observations through the independent scraper contracts. A pure versioned deriver converts those observations into one typed `CanonicalSelectionFacts` payload; a deterministic `RoleMatcher` and tri-state criterion evaluator consume that payload, while the ranker and public projector reuse the same semantics. This is the first implementation slice of the approved production-readiness design; parser routing, graph scheduling, retry/resume, persistence batching, and crash-atomic artifacts are owned by separate plans.

**Tech Stack:** Python 3.12, frozen dataclasses, `StrEnum`, SQLite-backed graph runtime, `unittest`/pytest, Ruff, mypy, repository verification scripts.

## Global Constraints

- Remote Actor execution is not required. Scraper independence is a contract property, not a deployment topology.
- The runtime does not search the web by bare company name to invent profile, official-site, or career-page URLs.
- The runtime does not perform implicit currency conversion.
- Compatibility shims for the current v2 contracts are forbidden. Contracts, callers, fixtures, tests, and documentation change together.
- Operational controls such as timeout, retry count, pacing, lease duration, queue priority, and request budget are not public search input.
- Parser observations contain only explicit source evidence. Pure, versioned derivers may normalize that evidence, but cannot manufacture eligibility.
- The compensation minimum, currency, and period are mandatory together. Gross/net is optional.
- A hard compensation minimum matches only when the explicit lower bound is at least the requested minimum and currency/period are equal.
- Maximum-only and dimensionless compensation values are unknown, not matches.
- `RUR` is normalized to `RUB`; no other currency is converted or guessed.
- Physical office locations never widen remote eligibility.
- An explicit grade in the vacancy title outranks a coarse source bucket.
- One deterministic `RoleMatcher` produces the title decision used by both filtering and ranking.
- Only candidates with `match` for every requested hard criterion are final keeps.
- An unknown hard criterion is rejected as `insufficient_evidence:<criterion>` and retained in filtered-out diagnostics.
- Public output excludes query input, page/rank, request settings, raw intermediate arrays, queue state, and debug transport fields.
- Every production change begins with a failing test through the narrowest real production path.
- The completed plan must leave `python3 scripts/verify_v2.py --skip-live` green before the next production-readiness plan begins; focused tests are the gate between the contract-migration tasks inside this atomic slice.
- Execute commit steps only in an isolated execution worktree whose baseline contains the current graph refactor; never stage unrelated changes from the existing dirty worktree.

---

## File Map

**New focused modules**

- `plugins/job-harness/src/job_harness/v2/contracts/canonical_facts.py`: typed canonical facts and JSON payload parsing/serialization boundaries.
- `plugins/job-harness/src/job_harness/v2/runtime/role_matching.py`: versioned role-token normalization and ordered/proximity matching.

**Public request and source-observation contracts**

- `plugins/job-harness/src/job_harness/v2/contracts/enums.py`: `CompensationPeriod`, `SearchCriterion.COMPENSATION`, and selection state enums.
- `plugins/job-harness/src/job_harness/v2/contracts/search.py`: `CompensationCriterion` and `SearchRequest.compensation`.
- `plugins/job-harness/src/job_harness/v2/contracts/criteria.py`: criterion metadata for dimensioned compensation.
- `plugins/job-harness/src/job_harness/v2/contracts/facts.py`: tri-state `SelectionDecision` contract.
- `plugins/job-harness/src/job_harness/v2/contracts/records.py`: explicit structured location, salary dimension, and remote-scope evidence retained by legacy source parsers.
- `plugins/job-harness/src/job_harness/v2/contracts/independent.py`: structured `SourceLocation` and normalized `SalaryRange` contracts.
- `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`: exports for the new public contracts.
- `plugins/job-harness/src/job_harness/v2/cli.py`: compensation CLI dimensions and cross-field validation.
- `plugins/job-harness/src/job_harness/v2/source_catalog.sql`: criterion name migration from `salary_from` to `compensation`.

**Normalization, selection, and presentation**

- `plugins/job-harness/src/job_harness/v2/runtime/source_bundles.py`: lossless source observation adaptation without physical-location scope widening.
- `plugins/job-harness/src/job_harness/v2/runtime/fact_derivers.py`: `selection-facts.v5` derivation.
- `plugins/job-harness/src/job_harness/v2/runtime/fact_requirement_planner.py`: canonical v5 fact paths.
- `plugins/job-harness/src/job_harness/v2/postprocessing/filter_ast.py`: tri-state structured-filter composition, including OR short-circuit semantics.
- `plugins/job-harness/src/job_harness/v2/postprocessing/filter_policy.py`: hard-criterion decisions over canonical facts.
- `plugins/job-harness/src/job_harness/v2/runtime/selection.py`: graph adapter for canonical facts and tri-state decisions.
- `plugins/job-harness/src/job_harness/v2/runtime/ranking.py`: ranking through the shared `RoleMatcher` and canonical grade.
- `plugins/job-harness/src/job_harness/v2/runtime/public_projection.py`: exact user-facing canonical facts with internal evidence removed.
- `plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py`: continue enrichment only for `needs_evidence`, never for explicit mismatches.
- `plugins/job-harness/src/job_harness/v2/presentation/report_template.html`: dimensioned request summary and canonical result fields.

**Tests and documentation**

- `plugins/job-harness/tests/v2/test_contracts_search.py`
- `plugins/job-harness/tests/v2/test_contracts_criteria.py`
- `plugins/job-harness/tests/v2/test_contracts_records_and_scraper.py`
- `plugins/job-harness/tests/v2/test_contracts_independent_scrapers.py`
- `plugins/job-harness/tests/v2/test_application_cli.py`
- `plugins/job-harness/tests/v2/test_runtime_independent_source_bundles.py`
- `plugins/job-harness/tests/v2/test_runtime_fact_derivers.py`
- `plugins/job-harness/tests/v2/test_runtime_role_matching.py`
- `plugins/job-harness/tests/v2/test_filter_policy.py`
- `plugins/job-harness/tests/v2/test_runtime_ranking.py`
- `plugins/job-harness/tests/v2/test_runtime_graph_coordinator.py`
- `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- `plugins/job-harness/tests/v2/test_formatters.py`
- `plugins/job-harness/tests/v2/test_source_catalog.py`
- `docs/search-system-spec.md`
- `plugins/job-harness/skills/job-search-workflow/SKILL.md`

### Task 1: Dimensioned Compensation Request Contract

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/enums.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/search.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/criteria.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`
- Modify: `plugins/job-harness/src/job_harness/v2/cli.py`
- Modify: `plugins/job-harness/src/job_harness/v2/source_catalog.sql`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/hh_ru.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/habr_career.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/finder_work.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/it_jobs_uz.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/hirify.py`
- Modify: `plugins/job-harness/src/job_harness/v2/presentation/report_template.html`
- Test: `plugins/job-harness/tests/v2/test_contracts_search.py`
- Test: `plugins/job-harness/tests/v2/test_contracts_criteria.py`
- Test: `plugins/job-harness/tests/v2/test_application_cli.py`
- Test: `plugins/job-harness/tests/v2/test_source_catalog.py`
- Test: `plugins/job-harness/tests/v2/test_formatters.py`

**Interfaces:**
- Consumes: existing `SearchRequest`, `SearchCriterion`, CLI parser, and source catalog criterion declarations.
- Produces: `CompensationCriterion(minimum: int, currency: str, period: CompensationPeriod, gross: bool | None = None)` and `SearchRequest.compensation: CompensationCriterion | None`.

- [ ] **Step 1: Write failing public-contract tests**

Add these cases to `test_contracts_search.py` and import `CompensationCriterion` and `CompensationPeriod` from `job_harness.v2.contracts`:

```python
def test_normalizes_dimensioned_compensation(self) -> None:
    request = SearchRequest(
        query_variants=("QA",),
        compensation=CompensationCriterion(
            minimum=200_000,
            currency="rur",
            period=CompensationPeriod.MONTH,
            gross=False,
        ),
    )

    self.assertEqual(200_000, request.compensation.minimum)
    self.assertEqual("RUB", request.compensation.currency)
    self.assertEqual(CompensationPeriod.MONTH, request.compensation.period)
    self.assertIn(SearchCriterion.COMPENSATION, request.requested_criteria)

def test_rejects_invalid_compensation_values(self) -> None:
    with self.assertRaisesRegex(ValueError, "minimum"):
        CompensationCriterion(0, "RUB", CompensationPeriod.MONTH)
    with self.assertRaisesRegex(ValueError, "currency"):
        CompensationCriterion(100_000, "rubles", CompensationPeriod.MONTH)

def test_rejects_removed_salary_from_request_field(self) -> None:
    with self.assertRaises(TypeError):
        cast(Any, SearchRequest)(query_variants=("QA",), salary_from=100_000)
```

Add these cases to `test_application_cli.py`:

```python
def test_cli_builds_dimensioned_compensation(self) -> None:
    args = _build_parser().parse_args(
        [
            "search",
            "--query",
            "QA",
            "--salary-minimum",
            "250000",
            "--salary-currency",
            "RUR",
            "--salary-period",
            "month",
            "--salary-gross",
            "false",
        ]
    )

    request = _request_from_args(args)

    self.assertEqual(250_000, request.compensation.minimum)
    self.assertEqual("RUB", request.compensation.currency)
    self.assertEqual(CompensationPeriod.MONTH, request.compensation.period)
    self.assertIs(request.compensation.gross, False)

def test_cli_rejects_partial_compensation_dimensions(self) -> None:
    args = _build_parser().parse_args(
        ["search", "--query", "QA", "--salary-minimum", "250000"]
    )

    with self.assertRaisesRegex(
        ValueError,
        "salary minimum, currency, and period must be supplied together",
    ):
        _request_from_args(args)
```

- [ ] **Step 2: Run the narrow tests and confirm the old scalar contract fails**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_contracts_search.py \
  tests/v2/test_contracts_criteria.py \
  tests/v2/test_application_cli.py -q
```

Expected: collection or assertion failures because `CompensationCriterion`, `CompensationPeriod`, `SearchCriterion.COMPENSATION`, and the four CLI flags do not exist.

- [ ] **Step 3: Implement the request and enum contracts**

Add to `contracts/enums.py` and replace `SALARY_FROM` with `COMPENSATION`:

```python
class CompensationPeriod(StrEnum):
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class SearchCriterion(StrEnum):
    QUERY = "query"
    GRADES = "grades"
    COMPENSATION = "compensation"
    PUBLISHED_SINCE = "published_since"
    RELOCATION = "relocation"
    WORK_FORMATS = "work_formats"
    REMOTE_SCOPES = "remote_scopes"
    VACANCY_GEOGRAPHIES = "vacancy_geographies"
    EMPLOYER_GEOGRAPHIES = "employer_geographies"
```

Add to `contracts/search.py`, replace the `salary_from` field with `compensation`, remove its scalar validation, and change `requested_criteria` to test `self.compensation is not None`:

```python
@dataclass(frozen=True)
class CompensationCriterion:
    minimum: int
    currency: str
    period: CompensationPeriod
    gross: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.minimum, bool) or not isinstance(self.minimum, int) or self.minimum < 1:
            raise ValueError("compensation minimum must be >= 1")
        if not isinstance(self.currency, str):
            raise ValueError("compensation currency must be an ISO 4217 alpha-3 code")
        currency = self.currency.strip().upper()
        if currency == "RUR":
            currency = "RUB"
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("compensation currency must be an ISO 4217 alpha-3 code")
        period = self.period
        if not isinstance(period, CompensationPeriod):
            try:
                period = CompensationPeriod(period)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid compensation period") from exc
        if self.gross is not None and not isinstance(self.gross, bool):
            raise ValueError("compensation gross must be boolean when provided")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "period", period)


compensation: CompensationCriterion | None = None
```

The criterion tuple in `SearchRequest.requested_criteria` must contain:

```python
(self.compensation is not None, SearchCriterion.COMPENSATION),
```

Export `CompensationCriterion` and `CompensationPeriod` from `contracts/__init__.py`.

- [ ] **Step 4: Implement CLI all-or-none validation**

Replace `--salary-from` with:

```python
search.add_argument("--salary-minimum", type=int)
search.add_argument("--salary-currency")
search.add_argument(
    "--salary-period",
    choices=tuple(period.value for period in CompensationPeriod),
)
search.add_argument("--salary-gross", choices=("true", "false"))
```

Add this helper and call it as `compensation=_compensation_from_args(args)` in `_request_from_args`:

```python
def _compensation_from_args(args: argparse.Namespace) -> CompensationCriterion | None:
    mandatory = (
        args.salary_minimum,
        args.salary_currency,
        args.salary_period,
    )
    if all(value is None for value in mandatory):
        if args.salary_gross is not None:
            raise ValueError(
                "salary minimum, currency, and period must be supplied together"
            )
        return None
    if any(value is None for value in mandatory):
        raise ValueError(
            "salary minimum, currency, and period must be supplied together"
        )
    return CompensationCriterion(
        minimum=args.salary_minimum,
        currency=args.salary_currency,
        period=CompensationPeriod(args.salary_period),
        gross=_optional_bool(args.salary_gross),
    )
```

- [ ] **Step 5: Migrate criterion metadata and disable dimensionless native forwarding**

In `contracts/criteria.py`, use this descriptor:

```python
SearchCriterionDescriptor(
    criterion=SearchCriterion.COMPENSATION,
    request_field="compensation",
    source_fact_fields=(
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "salary_gross",
    ),
    text_enrichment=TextEnrichmentPolicy.ALLOWED,
    text_enrichment_fields=(
        TextField.DESCRIPTION,
        TextField.REQUIREMENTS,
        TextField.RAW_TEXT,
    ),
),
```

Apply the catalog criterion rename mechanically, then change the five currently native declarations (`habr_career`, `hh_ru`, `finder_work`, `it_jobs_uz`, `hirify`) from `native_request` to `structured_output` until a provider-specific request contract can prove an exact currency and period:

```bash
perl -pi -e "s/'salary_from'/'compensation'/g" \
  plugins/job-harness/src/job_harness/v2/source_catalog.sql
```

Remove the `request.salary_from` URL-parameter branches from the five source modules listed above. Do not substitute `request.compensation.minimum`; sending only the number would silently discard currency and period.

Update the report request summary with this formatter:

```javascript
function compensationCriterionText(value) {
  if (!value) return "";
  const gross = value.gross === true ? " gross" : value.gross === false ? " net" : "";
  return `${text(value.minimum)} ${text(value.currency)} / ${text(value.period)}${gross}`;
}
```

Render it with:

```javascript
requestField("Compensation", compensationCriterionText(request.compensation) || "N/a"),
```

- [ ] **Step 6: Update criterion, catalog, and CLI assertions and run them**

Replace request-level references to `SearchCriterion.SALARY_FROM`, `request.salary_from`, and `--salary-from` in the listed tests with the new contract. Keep source payload fields such as `SalaryRange.salary_from` unchanged because they describe source observations rather than search input.

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_contracts_search.py \
  tests/v2/test_contracts_criteria.py \
  tests/v2/test_application_cli.py \
  tests/v2/test_source_catalog.py \
  tests/v2/test_formatters.py -q
```

Expected: PASS.

Run:

```bash
rg -n "request\.salary_from|SearchCriterion\.SALARY_FROM|--salary-from" \
  plugins/job-harness/src plugins/job-harness/tests scripts docs
```

Expected: no matches.

- [ ] **Step 7: Commit the request-contract slice**

```bash
git add \
  plugins/job-harness/src/job_harness/v2/contracts/enums.py \
  plugins/job-harness/src/job_harness/v2/contracts/search.py \
  plugins/job-harness/src/job_harness/v2/contracts/criteria.py \
  plugins/job-harness/src/job_harness/v2/contracts/__init__.py \
  plugins/job-harness/src/job_harness/v2/cli.py \
  plugins/job-harness/src/job_harness/v2/source_catalog.sql \
  plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/hh_ru.py \
  plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/habr_career.py \
  plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/finder_work.py \
  plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/it_jobs_uz.py \
  plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/hirify.py \
  plugins/job-harness/src/job_harness/v2/presentation/report_template.html \
  plugins/job-harness/tests/v2/test_contracts_search.py \
  plugins/job-harness/tests/v2/test_contracts_criteria.py \
  plugins/job-harness/tests/v2/test_application_cli.py \
  plugins/job-harness/tests/v2/test_source_catalog.py \
  plugins/job-harness/tests/v2/test_formatters.py
git diff --cached --check
git commit -m "refactor: dimension compensation search criteria"
```

### Task 2: Lossless Source Location, Workplace, And Compensation Evidence

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/records.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/independent.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/source_bundles.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/hh_ru.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/it_jobs_uz.py`
- Modify: `plugins/job-harness/tests/v2/fixtures/scrapers/hh_ru/success/expected.raw.json`
- Modify: `plugins/job-harness/tests/v2/fixtures/scrapers/it_jobs_uz/success/expected.raw.json`
- Test: `plugins/job-harness/tests/v2/test_contracts_records_and_scraper.py`
- Test: `plugins/job-harness/tests/v2/test_contracts_independent_scrapers.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_independent_source_bundles.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_sources_contract_first.py`

**Interfaces:**
- Consumes: `RawListing` emitted by existing source modules.
- Produces: `SourceLocation(text, cities, countries, regions)`, complete `SalaryRange`, detail-level `native_grade`, and remote scopes drawn only from explicit remote-eligibility fields.

- [ ] **Step 1: Write failing losslessness and no-widening tests**

Add to `test_runtime_independent_source_bundles.py` and import `_detail_output` and `_listing_output` from `job_harness.v2.runtime.source_bundles`:

```python
def test_listing_adapter_preserves_all_structured_locations(self) -> None:
    output = _listing_output(
        RawListing(
            source_listing_id="1",
            title="Data Analyst",
            url="https://example.com/jobs/1",
            source="test",
            location_text="London | Vilnius",
            location_cities=("London", "Vilnius"),
            location_countries=("GB", "LT"),
            location_regions=("EU",),
        ),
        target_provider_id="test",
    )

    self.assertEqual("London | Vilnius", output.location.text)
    self.assertEqual(("London", "Vilnius"), output.location.cities)
    self.assertEqual(("GB", "LT"), output.location.countries)
    self.assertEqual(("EU",), output.location.regions)

def test_physical_locations_do_not_widen_remote_scope(self) -> None:
    output = _listing_output(
        RawListing(
            source_listing_id="2",
            title="Data Analyst",
            url="https://example.com/jobs/2",
            source="test",
            location_text="London, Vilnius; Remote, Germany",
            location_cities=("London", "Vilnius"),
            location_countries=("GB", "LT"),
            remote_in_country=True,
            remote_scope_countries=("DE",),
        ),
        target_provider_id="test",
    )

    self.assertEqual(
        (RemoteScope(kind="country", code="DE"),),
        output.remote_scopes,
    )

def test_listing_adapter_keeps_compensation_dimensions_and_normalizes_rur(self) -> None:
    output = _listing_output(
        RawListing(
            source_listing_id="3",
            title="QA Lead",
            url="https://example.com/jobs/3",
            source="test",
            salary_min=300_000,
            salary_max=400_000,
            salary_currency="RUR",
            salary_period="month",
            salary_gross=True,
        ),
        target_provider_id="test",
    )

    self.assertEqual(300_000, output.salary.salary_from)
    self.assertEqual("RUB", output.salary.currency)
    self.assertEqual("month", output.salary.period)
    self.assertIs(output.salary.gross, True)

def test_detail_adapter_preserves_provider_grade_evidence(self) -> None:
    detail = _detail_output(
        RawListing(
            source_listing_id="4",
            title="QA Engineer",
            url="https://example.com/jobs/4",
            source="test",
            native_grade="senior",
        ),
        VacancyDetailInput(
            target_provider_id="test",
            vacancy_url="https://example.com/jobs/4",
            source_listing_id="4",
        ),
    )

    self.assertEqual("senior", detail.native_grade)
```

- [ ] **Step 2: Run the adapter tests and confirm fields are missing**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_contracts_records_and_scraper.py \
  tests/v2/test_contracts_independent_scrapers.py \
  tests/v2/test_runtime_independent_source_bundles.py -q
```

Expected: failures because the structured location, salary dimension, and explicit remote-scope fields do not exist and the adapter currently derives remote scopes from physical location.

- [ ] **Step 3: Extend observation contracts with explicit structured evidence**

Add these optional fields to `RawListing` immediately after their corresponding scalar fields:

```python
location_cities: tuple[str, ...] = ()
location_countries: tuple[str, ...] = ()
location_regions: tuple[str, ...] = ()
salary_period: str | None = None
salary_gross: bool | None = None
remote_scope_countries: tuple[str, ...] = ()
remote_scope_regions: tuple[str, ...] = ()
```

Validate the period and trim/deduplicate tuple values in `RawListing.__post_init__`:

```python
if self.salary_period not in {None, "hour", "day", "month", "year"}:
    raise ValueError("invalid salary period")
for field_name in (
    "location_cities",
    "location_countries",
    "location_regions",
    "remote_scope_countries",
    "remote_scope_regions",
):
    values = tuple(dict.fromkeys(value.strip() for value in getattr(self, field_name) if value.strip()))
    object.__setattr__(self, field_name, values)
```

Replace `SourceLocation` with:

```python
@dataclass(frozen=True)
class SourceLocation:
    text: str | None = None
    cities: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        text = self.text.strip() if self.text is not None else None
        cities = _clean_values(self.cities)
        countries = tuple(value.upper() for value in _clean_values(self.countries))
        regions = tuple(value.upper() for value in _clean_values(self.regions))
        if not any((text, cities, countries, regions)):
            raise ValueError("source location requires explicit evidence")
        if any(not re.fullmatch(r"[A-Z]{2}", value) for value in countries):
            raise ValueError("location countries must be ISO 3166-1 alpha-2 codes")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "cities", cities)
        object.__setattr__(self, "countries", countries)
        object.__setattr__(self, "regions", regions)
```

Use this helper in `contracts/independent.py`:

```python
def _clean_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
```

Normalize `RUR` in `SalaryRange.__post_init__` before ISO validation:

```python
if self.currency is not None:
    currency = self.currency.strip().upper()
    if currency == "RUR":
        currency = "RUB"
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be an ISO 4217 alpha-3 code")
    object.__setattr__(self, "currency", currency)
```

Add `native_grade: str | None = None` to `VacancyDetailOutput` immediately after `application_channels`. This is explicit provider/experience evidence, not a grade inferred by the adapter.

- [ ] **Step 4: Make the source-bundle adapter lossless and fail closed on remote scope**

Use `_source_location` for both listing and detail output:

```python
def _source_location(listing: RawListing) -> SourceLocation | None:
    text = listing.location_text.strip() if listing.location_text else None
    cities = listing.location_cities or ((listing.city,) if listing.city else ())
    normalized_country = (
        normalize_source_geographies(listing.country)
        if listing.country and not listing.location_countries
        else ()
    )
    countries = listing.location_countries or tuple(
        value for value in normalized_country if not is_region_scope(value)
    )
    regions = listing.location_regions or tuple(
        value for value in normalized_country if is_region_scope(value)
    )
    if not any((text, cities, countries, regions)):
        return None
    return SourceLocation(
        text=text,
        cities=cities,
        countries=countries,
        regions=regions,
    )
```

Replace `_salary` with:

```python
def _salary(listing: RawListing) -> SalaryRange | None:
    if listing.salary_min is None and listing.salary_max is None:
        return None
    return SalaryRange(
        salary_from=listing.salary_min,
        salary_to=listing.salary_max,
        currency=listing.salary_currency,
        gross=listing.salary_gross,
        period=listing.salary_period,
    )
```

Pass `native_grade=listing.native_grade` from `_detail_output` into `VacancyDetailOutput`.

Preserve dimensions already explicit in the two audited source contracts:

```python
# hh_ru.py: HH compensation is a monthly amount and the payload exposes gross.
class _Compensation:
    def __init__(
        self,
        *,
        minimum: int | None,
        maximum: int | None,
        currency: str | None,
        gross: bool | None,
        text: str | None,
    ) -> None:
        self.minimum = minimum
        self.maximum = maximum
        self.currency = currency
        self.gross = gross
        self.text = text

# In _hh_listing(...)
salary_period="month" if compensation.minimum is not None or compensation.maximum is not None else None,
salary_gross=compensation.gross,

# it_jobs_uz.py: salaryPeriod is already explicit in the response fixture.
salary_period=period if period in {"hour", "day", "month", "year"} else None,
```

Pass `gross=gross` from `_compensation` into `_Compensation`, and update both expected raw fixtures with the exact new `salary_period`/`salary_gross` fields. Sources without explicit or provider-contract period evidence keep `salary_period=None` and therefore evaluate a hard compensation criterion as unknown.

Add `salary_period` and `salary_gross` to the field tuple in `_assert_listing_matches` so the fixture additions are asserted through the real source parser path.

Replace `_explicit_remote_scopes` with this implementation. It deliberately does not read `listing.country`, `listing.city`, or `listing.location_text`:

```python
def _explicit_remote_scopes(listing: RawListing) -> tuple[RemoteScope, ...]:
    if listing.remote_global is True:
        return (RemoteScope(kind="worldwide", code=None),)
    scopes = [
        *(RemoteScope(kind="country", code=code.upper()) for code in listing.remote_scope_countries),
        *(RemoteScope(kind="region", code=code.upper()) for code in listing.remote_scope_regions),
    ]
    return tuple(dict.fromkeys(scopes))
```

- [ ] **Step 5: Run contract and adapter tests**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_contracts_records_and_scraper.py \
  tests/v2/test_contracts_independent_scrapers.py \
  tests/v2/test_runtime_independent_source_bundles.py \
  tests/v2/test_runtime_sources_contract_first.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the source-evidence slice**

```bash
git add \
  plugins/job-harness/src/job_harness/v2/contracts/records.py \
  plugins/job-harness/src/job_harness/v2/contracts/independent.py \
  plugins/job-harness/src/job_harness/v2/contracts/__init__.py \
  plugins/job-harness/src/job_harness/v2/runtime/source_bundles.py \
  plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/hh_ru.py \
  plugins/job-harness/src/job_harness/v2/runtime/sources/aggregators/it_jobs_uz.py \
  plugins/job-harness/tests/v2/fixtures/scrapers/hh_ru/success/expected.raw.json \
  plugins/job-harness/tests/v2/fixtures/scrapers/it_jobs_uz/success/expected.raw.json \
  plugins/job-harness/tests/v2/test_contracts_records_and_scraper.py \
  plugins/job-harness/tests/v2/test_contracts_independent_scrapers.py \
  plugins/job-harness/tests/v2/test_runtime_independent_source_bundles.py \
  plugins/job-harness/tests/v2/test_runtime_sources_contract_first.py
git diff --cached --check
git commit -m "refactor: preserve structured source evidence"
```

### Task 3: Typed Canonical Selection Facts V5

**Files:**
- Create: `plugins/job-harness/src/job_harness/v2/contracts/canonical_facts.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/fact_derivers.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/fact_requirement_planner.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_fact_derivers.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_fact_requirement_planner.py`

**Interfaces:**
- Consumes: merged parser fact JSON containing `title`, `native_grade`, structured `location`, `salary`, `work_formats`, `remote_scopes`, and text evidence.
- Produces: `CanonicalSelectionFacts` serialized as `derived_facts.structured-selection-facts` with schema id `selection-facts.v5` and deriver version `5.0`.

- [ ] **Step 1: Replace flat-fact assertions with failing canonical-v5 regressions**

Replace the grade/location/workplace/compensation tests in `test_runtime_fact_derivers.py` with these exact expectations:

```python
def test_title_grade_wins_and_records_source_conflict(self) -> None:
    derivation = derive_selection_facts(
        {
            "title": "Middle/Senior QA Engineer",
            "native_grade": "lead",
        }
    )[0]

    self.assertEqual("selection-facts.v5", derivation.output_schema_id)
    self.assertEqual(
        {
            "title_evidence": ["middle", "senior"],
            "source_evidence": ["lead"],
            "resolved": ["middle", "senior"],
            "conflict": True,
            "evidence": ["title", "native_grade"],
        },
        derivation.payload["grade"],
    )

def test_preserves_mixed_location_components(self) -> None:
    derivation = derive_selection_facts(
        {
            "title": "Data Analyst",
            "location": {
                "text": "London | Vilnius",
                "cities": ["London", "Vilnius"],
                "countries": ["GB", "LT"],
                "regions": ["EU"],
            },
        }
    )[0]

    self.assertEqual(
        {
            "raw_text": "London | Vilnius",
            "cities": ["London", "Vilnius"],
            "countries": ["GB", "LT"],
            "regions": ["EU"],
            "evidence": ["location.text", "location.cities", "location.countries", "location.regions"],
        },
        derivation.payload["location"],
    )

def test_keeps_physical_location_separate_from_remote_scope(self) -> None:
    derivation = derive_selection_facts(
        {
            "title": "Data Analyst",
            "location": {
                "text": "London, Vilnius; Remote, Germany",
                "cities": ["London", "Vilnius"],
                "countries": ["GB", "LT"],
                "regions": ["EU"],
            },
            "work_formats": ["remote", "hybrid"],
            "remote_scopes": [{"kind": "country", "code": "DE"}],
        }
    )[0]

    self.assertEqual(["remote", "hybrid"], derivation.payload["workplace"]["formats"])
    self.assertEqual(["country:DE"], derivation.payload["workplace"]["remote_scopes"])

def test_normalizes_complete_compensation_without_converting_currency(self) -> None:
    derivation = derive_selection_facts(
        {
            "title": "QA Lead",
            "salary": {
                "salary_from": 300_000,
                "salary_to": 400_000,
                "currency": "RUR",
                "period": "month",
                "gross": True,
            },
        }
    )[0]

    self.assertEqual(
        {
            "minimum": 300_000,
            "maximum": 400_000,
            "currency": "RUB",
            "period": "month",
            "gross": True,
            "evidence": ["salary.salary_from", "salary.salary_to", "salary.currency", "salary.period", "salary.gross"],
        },
        derivation.payload["compensation"],
    )
```

Keep the relocation-versus-visa test but assert nested boolean facts:

```python
self.assertEqual(
    {"supported": None, "evidence": []},
    visa_only.payload["relocation"],
)
self.assertEqual(
    {"supported": True, "evidence": ["description"]},
    visa_only.payload["visa_sponsorship"],
)
```

- [ ] **Step 2: Run the deriver tests and confirm v4 flat payloads fail**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_runtime_fact_derivers.py \
  tests/v2/test_runtime_fact_requirement_planner.py -q
```

Expected: failures showing `selection-facts.v4`, flat `grade`, `salary_min`, `work_formats`, and `vacancy_geographies` fields.

- [ ] **Step 3: Add the typed canonical fact contract**

Create `contracts/canonical_facts.py` with these complete dataclasses:

```python
"""Canonical facts used by hard selection and public projection."""

from __future__ import annotations

from dataclasses import dataclass

from job_harness.v2.contracts.enums import CompensationPeriod


@dataclass(frozen=True)
class LocationFact:
    raw_text: str | None
    cities: tuple[str, ...]
    countries: tuple[str, ...]
    regions: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class WorkplaceFact:
    formats: tuple[str, ...]
    remote_scopes: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class GradeFact:
    title_evidence: tuple[str, ...]
    source_evidence: tuple[str, ...]
    resolved: tuple[str, ...]
    conflict: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CompensationFact:
    minimum: int | None
    maximum: int | None
    currency: str | None
    period: CompensationPeriod | None
    gross: bool | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class BooleanEvidenceFact:
    supported: bool | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalSelectionFacts:
    location: LocationFact
    workplace: WorkplaceFact
    grade: GradeFact
    compensation: CompensationFact
    relocation: BooleanEvidenceFact
    visa_sponsorship: BooleanEvidenceFact
    employer_geographies: tuple[str, ...]
```

Export every type from `contracts/__init__.py`.

- [ ] **Step 4: Refactor derivation into typed helpers and serialize as v5**

Keep the existing explicit text-pattern tables, but make each helper return one canonical dataclass. Replace `derive_selection_facts` with:

```python
def derive_selection_facts(facts: JsonObject) -> tuple[FactDerivation, ...]:
    canonical = CanonicalSelectionFacts(
        location=_location_fact(facts),
        workplace=_workplace_fact(facts),
        grade=_grade_fact(facts),
        compensation=_compensation_fact(facts),
        relocation=_boolean_fact(facts, kind="relocation"),
        visa_sponsorship=_boolean_fact(facts, kind="visa_sponsorship"),
        employer_geographies=_employer_geographies(facts),
    )
    payload = to_jsonable(canonical)
    if not isinstance(payload, dict):
        raise TypeError("canonical selection facts must serialize to an object")
    return (
        FactDerivation(
            deriver_id="structured-selection-facts",
            deriver_version="5.0",
            output_schema_id="selection-facts.v5",
            payload=payload,
        ),
    )
```

Implement title precedence with every explicit grade preserved in title order:

```python
def _grade_fact(facts: JsonObject) -> GradeFact:
    title = _text(facts.get("title")) or ""
    positioned = sorted(
        (
            match.start(),
            grade,
        )
        for grade, pattern in _GRADE_PATTERNS
        for match in pattern.finditer(title)
    )
    title_grades = tuple(dict.fromkeys(grade for _, grade in positioned))
    native = _text(facts.get("native_grade"))
    source_grades = ()
    if native:
        normalized = native.casefold().strip().rstrip(".")
        source_grades = (_NATIVE_GRADE_ALIASES.get(normalized, normalized),)
    resolved = title_grades or source_grades
    conflict = bool(title_grades and source_grades and not set(title_grades) & set(source_grades))
    evidence = tuple(
        field
        for present, field in ((bool(title_grades), "title"), (bool(source_grades), "native_grade"))
        if present
    )
    return GradeFact(title_grades, source_grades, resolved, conflict, evidence)
```

Use these exact fact builders. They never derive remote eligibility from the physical `location` object:

```python
def _location_fact(facts: JsonObject) -> LocationFact:
    location = _object(facts.get("location")) or {}
    raw_text = _text(location.get("text"))
    cities = _strings(location.get("cities"))
    countries = tuple(value.upper() for value in _strings(location.get("countries")))
    regions = tuple(value.upper() for value in _strings(location.get("regions")))
    evidence = tuple(
        path
        for present, path in (
            (raw_text is not None, "location.text"),
            (bool(cities), "location.cities"),
            (bool(countries), "location.countries"),
            (bool(regions), "location.regions"),
        )
        if present
    )
    return LocationFact(raw_text, cities, countries, regions, evidence)


def _workplace_fact(facts: JsonObject) -> WorkplaceFact:
    structured_formats = tuple(
        "office" if value == "onsite" else value
        for value in _strings(facts.get("work_formats"))
    )
    formats = tuple(dict.fromkeys(structured_formats))
    evidence: list[str] = ["work_formats"] if formats else []
    if not formats:
        for work_format, patterns in _WORK_FORMAT_TEXT_PATTERNS.items():
            matching_paths = _matching_text_paths(facts, patterns)
            if matching_paths:
                formats += (work_format,)
                evidence.extend(matching_paths)

    scopes: list[str] = []
    raw_scopes = facts.get("remote_scopes")
    if isinstance(raw_scopes, list):
        for raw_scope in raw_scopes:
            scope = _object(raw_scope)
            if scope is None:
                continue
            kind = _text(scope.get("kind"))
            code = _text(scope.get("code"))
            if kind == "worldwide":
                scopes.append("global")
            elif kind in {"country", "region"} and code:
                scopes.append(f"{kind}:{code.upper()}")
        if scopes:
            evidence.append("remote_scopes")

    if "remote" in formats and not scopes:
        global_paths = _matching_text_paths(facts, _GLOBAL_REMOTE_SCOPE_PATTERNS)
        russian_paths = _matching_text_paths(facts, _RUSSIA_REMOTE_SCOPE_PATTERNS)
        if global_paths:
            scopes.append("global")
            evidence.extend(global_paths)
        if russian_paths:
            scopes.append("country:RU")
            evidence.extend(russian_paths)

    return WorkplaceFact(
        formats=tuple(dict.fromkeys(formats)),
        remote_scopes=tuple(dict.fromkeys(scopes)),
        evidence=tuple(dict.fromkeys(evidence)),
    )


def _compensation_fact(facts: JsonObject) -> CompensationFact:
    salary = _object(facts.get("salary")) or {}
    minimum = _integer(salary.get("salary_from"))
    maximum = _integer(salary.get("salary_to"))
    currency = _text(salary.get("currency"))
    if currency:
        currency = currency.upper()
        if currency == "RUR":
            currency = "RUB"
    raw_period = _text(salary.get("period"))
    period = CompensationPeriod(raw_period) if raw_period in {item.value for item in CompensationPeriod} else None
    gross = salary.get("gross") if isinstance(salary.get("gross"), bool) else None
    evidence = tuple(
        path
        for present, path in (
            (minimum is not None, "salary.salary_from"),
            (maximum is not None, "salary.salary_to"),
            (currency is not None, "salary.currency"),
            (period is not None, "salary.period"),
            (gross is not None, "salary.gross"),
        )
        if present
    )
    return CompensationFact(minimum, maximum, currency, period, gross, evidence)


def _boolean_fact(facts: JsonObject, *, kind: str) -> BooleanEvidenceFact:
    explicit = facts.get(kind)
    if isinstance(explicit, bool):
        return BooleanEvidenceFact(explicit, (kind,))
    if kind == "relocation":
        negative = _RELOCATION_NEGATIVE_PATTERNS
        positive = _RELOCATION_POSITIVE_PATTERNS
    elif kind == "visa_sponsorship":
        negative = _VISA_SPONSORSHIP_NEGATIVE_PATTERNS
        positive = _VISA_SPONSORSHIP_POSITIVE_PATTERNS
    else:
        raise ValueError(f"unsupported boolean fact kind: {kind}")
    negative_paths = _matching_text_paths(facts, negative)
    if negative_paths:
        return BooleanEvidenceFact(False, negative_paths)
    positive_paths = _matching_text_paths(facts, positive)
    if positive_paths:
        return BooleanEvidenceFact(True, positive_paths)
    return BooleanEvidenceFact(None, ())


def _matching_text_paths(
    facts: JsonObject,
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[str, ...]:
    return tuple(
        path
        for path, text in _selection_text_by_path(facts)
        if any(pattern.search(text) for pattern in patterns)
    )


def _selection_text_by_path(facts: JsonObject) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for field in (
        "title",
        "summary",
        "description",
        "requirements",
        "responsibilities",
        "conditions",
        "raw_text",
    ):
        value = facts.get(field)
        if isinstance(value, str) and value.strip():
            values.append((field, value))
        elif isinstance(value, list | tuple):
            values.extend((field, item) for item in value if isinstance(item, str) and item.strip())
    additional = _object(facts.get("additional_sections")) or {}
    values.extend(
        (f"additional_sections.{key}", value)
        for key, value in additional.items()
        if isinstance(value, str) and value.strip()
    )
    return tuple(values)
```

- [ ] **Step 5: Update fact requirement paths to v5**

Use these paths in `fact_requirement_planner.py`:

```python
_DERIVED_SELECTION_FACTS = "derived_facts.structured-selection-facts"

detail_facts = {
    SearchCriterion.GRADES: (
        f"{_DERIVED_SELECTION_FACTS}.grade.resolved",
        "native_grade",
    ),
    SearchCriterion.COMPENSATION: (
        f"{_DERIVED_SELECTION_FACTS}.compensation.minimum",
        "salary",
    ),
    SearchCriterion.RELOCATION: (
        f"{_DERIVED_SELECTION_FACTS}.relocation.supported",
        "description",
    ),
    SearchCriterion.WORK_FORMATS: (
        f"{_DERIVED_SELECTION_FACTS}.workplace.formats",
        "work_formats",
    ),
    SearchCriterion.REMOTE_SCOPES: (
        f"{_DERIVED_SELECTION_FACTS}.workplace.remote_scopes",
        "remote_scopes",
    ),
    SearchCriterion.VACANCY_GEOGRAPHIES: (
        f"{_DERIVED_SELECTION_FACTS}.location.countries",
        "location",
    ),
}
```

The independent `VacancyDetailOutput` contract from Task 2 supplies `native_grade` when the detail provider exposes an experience bucket. The selector, not this planner, decides whether a partially known compensation object satisfies the complete dimensional criterion.

- [ ] **Step 6: Run canonical fact and requirement tests**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_runtime_fact_derivers.py \
  tests/v2/test_runtime_fact_requirement_planner.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit the canonical-fact slice**

```bash
git add \
  plugins/job-harness/src/job_harness/v2/contracts/canonical_facts.py \
  plugins/job-harness/src/job_harness/v2/contracts/__init__.py \
  plugins/job-harness/src/job_harness/v2/runtime/fact_derivers.py \
  plugins/job-harness/src/job_harness/v2/runtime/fact_requirement_planner.py \
  plugins/job-harness/tests/v2/test_runtime_fact_derivers.py \
  plugins/job-harness/tests/v2/test_runtime_fact_requirement_planner.py
git diff --cached --check
git commit -m "refactor: derive canonical selection facts"
```

### Task 4: Shared Ordered Role Matcher

**Files:**
- Create: `plugins/job-harness/src/job_harness/v2/runtime/role_matching.py`
- Create: `plugins/job-harness/tests/v2/test_runtime_role_matching.py`

**Interfaces:**
- Consumes: `RoleMatcher(query_variants: tuple[str, ...])` and vacancy title text.
- Produces: `RoleMatch(matched, query_variant, matched_positions, strength, alias_version)` used unchanged by filter and ranker.

- [ ] **Step 1: Write failing role-matching regressions**

Create `test_runtime_role_matching.py`:

```python
from __future__ import annotations

import unittest

from job_harness.v2.runtime.role_matching import RoleMatcher


class RoleMatcherTest(unittest.TestCase):
    def test_matches_ordered_role_tokens_with_bounded_gaps(self) -> None:
        matcher = RoleMatcher(("Data Analyst",))

        match = matcher.match("Senior BI / Data Platform Analyst")

        self.assertTrue(match.matched)
        self.assertEqual("Data Analyst", match.query_variant)
        self.assertEqual((2, 4), match.matched_positions)

    def test_rejects_reversed_role_tokens(self) -> None:
        matcher = RoleMatcher(("Java Engineer",))

        match = matcher.match("QA Automation Engineer (Java)")

        self.assertFalse(match.matched)
        self.assertEqual(0.0, match.strength)

    def test_rejects_more_than_three_intervening_title_tokens(self) -> None:
        matcher = RoleMatcher(("Data Analyst",))

        self.assertFalse(
            matcher.match("Data and BI platform reporting principal Analyst").matched
        )

    def test_applies_versioned_phrase_aliases_before_matching(self) -> None:
        matcher = RoleMatcher(("QA Engineer",))

        match = matcher.match("Quality Assurance Engineer")

        self.assertTrue(match.matched)
        self.assertEqual("1", match.alias_version)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the role tests and confirm the module is absent**

Run:

```bash
uv --directory plugins/job-harness run pytest tests/v2/test_runtime_role_matching.py -q
```

Expected: import failure for `job_harness.v2.runtime.role_matching`.

- [ ] **Step 3: Implement deterministic token normalization and ordered matching**

Create `runtime/role_matching.py`:

```python
"""Deterministic role-title matching shared by filtering and ranking."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[\w+#]+", re.UNICODE)
_MAX_INTERVENING_TOKENS = 3
_ALIAS_VERSION = "1"
_PHRASE_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("software", "development", "engineer", "in", "test"), "sdet"),
    (("quality", "assurance"), "qa"),
    (("test", "automation"), "aqa"),
    (("artificial", "intelligence"), "ai"),
    (("machine", "learning"), "ml"),
)


@dataclass(frozen=True)
class RoleMatch:
    matched: bool
    query_variant: str | None
    matched_positions: tuple[int, ...]
    strength: float
    alias_version: str = _ALIAS_VERSION


class RoleMatcher:
    def __init__(self, query_variants: tuple[str, ...]) -> None:
        self._queries = tuple(
            (query, _canonical_tokens(query))
            for query in query_variants
            if _canonical_tokens(query)
        )

    def match(self, title: str) -> RoleMatch:
        title_tokens = _canonical_tokens(title)
        candidates = tuple(
            result
            for query, query_tokens in self._queries
            if (result := _match_query(query, query_tokens, title_tokens)).matched
        )
        if not candidates:
            return RoleMatch(False, None, (), 0.0)
        return max(
            candidates,
            key=lambda item: (
                item.strength,
                -sum(item.matched_positions),
                item.query_variant or "",
            ),
        )


def _match_query(
    query: str,
    query_tokens: tuple[str, ...],
    title_tokens: tuple[str, ...],
) -> RoleMatch:
    positions: list[int] = []
    start = 0
    for token in query_tokens:
        position = next(
            (index for index in range(start, len(title_tokens)) if title_tokens[index] == token),
            None,
        )
        if position is None:
            return RoleMatch(False, None, (), 0.0)
        if positions and position - positions[-1] - 1 > _MAX_INTERVENING_TOKENS:
            return RoleMatch(False, None, (), 0.0)
        positions.append(position)
        start = position + 1
    total_gaps = sum(right - left - 1 for left, right in zip(positions, positions[1:]))
    exact = tuple(title_tokens[positions[0] : positions[-1] + 1]) == query_tokens
    strength = 1.0 if exact else round(max(0.5, 0.9 - total_gaps * 0.1), 2)
    return RoleMatch(True, query, tuple(positions), strength)


def _canonical_tokens(value: str) -> tuple[str, ...]:
    raw = tuple(token.casefold() for token in _TOKEN_RE.findall(value))
    canonical: list[str] = []
    index = 0
    while index < len(raw):
        alias = next(
            (
                (phrase, replacement)
                for phrase, replacement in _PHRASE_ALIASES
                if raw[index : index + len(phrase)] == phrase
            ),
            None,
        )
        if alias is None:
            canonical.append(raw[index])
            index += 1
            continue
        phrase, replacement = alias
        canonical.append(replacement)
        index += len(phrase)
    return tuple(canonical)
```

- [ ] **Step 4: Run role matcher tests**

Run:

```bash
uv --directory plugins/job-harness run pytest tests/v2/test_runtime_role_matching.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the role matcher**

```bash
git add \
  plugins/job-harness/src/job_harness/v2/runtime/role_matching.py \
  plugins/job-harness/tests/v2/test_runtime_role_matching.py
git diff --cached --check
git commit -m "feat: add deterministic role matcher"
```

### Task 5: Tri-State Hard Criteria And Graph Selection

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/enums.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/facts.py`
- Modify: `plugins/job-harness/src/job_harness/v2/contracts/__init__.py`
- Modify: `plugins/job-harness/src/job_harness/v2/postprocessing/filter_ast.py`
- Modify: `plugins/job-harness/src/job_harness/v2/postprocessing/filter_policy.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/selection.py`
- Modify: `plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py`
- Test: `plugins/job-harness/tests/v2/test_filter_policy.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_coordinator.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py`

**Interfaces:**
- Consumes: `CanonicalSelectionFacts`, `CompensationCriterion`, `RoleMatch`, and structured filter AST.
- Produces: `SelectionDecision(outcome, reasons, criteria)` where `keep` means every hard criterion matched and `can_enrich` means no explicit mismatch has made the branch impossible.

- [ ] **Step 1: Write failing tri-state policy tests**

Add these tests to `test_filter_policy.py` using canonical `VacancyFilterFacts` fields introduced in this task:

```python
def test_unknown_grade_is_not_a_final_keep(self) -> None:
    decision = decide_vacancy_filter(
        criteria=VacancyFilterCriteria(queries=("QA",), grades=("senior",)),
        vacancy=VacancyFilterFacts(title="QA Engineer"),
    )

    self.assertEqual(SelectionOutcome.NEEDS_EVIDENCE, decision.outcome)
    self.assertFalse(decision.keep)
    self.assertTrue(decision.can_enrich)
    self.assertIn("insufficient_evidence:grades", decision.reasons)

def test_dimensionally_incomparable_compensation_is_unknown(self) -> None:
    decision = decide_vacancy_filter(
        criteria=VacancyFilterCriteria(
            queries=("QA",),
            compensation=CompensationCriterion(250_000, "RUB", CompensationPeriod.MONTH),
        ),
        vacancy=VacancyFilterFacts(
            title="QA Engineer",
            compensation=CompensationFact(
                minimum=300_000,
                maximum=400_000,
                currency="USD",
                period=CompensationPeriod.YEAR,
                gross=None,
                evidence=("salary",),
            ),
        ),
    )

    self.assertEqual(SelectionOutcome.NEEDS_EVIDENCE, decision.outcome)
    self.assertIn("insufficient_evidence:compensation", decision.reasons)

def test_compensation_requires_explicit_matching_lower_bound(self) -> None:
    criteria = VacancyFilterCriteria(
        queries=("QA",),
        compensation=CompensationCriterion(250_000, "RUB", CompensationPeriod.MONTH),
    )
    maximum_only = VacancyFilterFacts(
        title="QA Engineer",
        compensation=CompensationFact(
            minimum=None,
            maximum=400_000,
            currency="RUB",
            period=CompensationPeriod.MONTH,
            gross=None,
            evidence=("salary",),
        ),
    )

    self.assertEqual(
        SelectionOutcome.NEEDS_EVIDENCE,
        decide_vacancy_filter(criteria=criteria, vacancy=maximum_only).outcome,
    )

def test_matching_or_branch_short_circuits_unknown_alternative(self) -> None:
    expression = AnyFilter(
        filters=(
            FieldFilter("relocation", "any_of", ("true",), "relocation_mismatch"),
            FieldFilter("work_format", "any_of", ("hybrid",), "work_format_mismatch"),
        )
    )

    evaluation = evaluate_filter_ast(
        expression,
        FilterFacts(relocation=None, work_formats=("hybrid",)),
    )

    self.assertEqual(CriterionState.MATCH, evaluation.state)
    self.assertEqual((), evaluation.reasons)
```

Add this bundle at module scope and insert the async test method inside the existing `GraphSearchPipelineTest` class in `test_runtime_graph_pipeline.py`:

```python
class _UnknownGradeDetailBundle:
    manifest = ParserManifest(
        parser_id="test.grade-detail",
        parser_type=ParserType.VACANCY_DETAIL,
        implementation_version="1.0",
        input_schema_id="test.grade-detail.input.v1",
        output_schema_id="vacancy-detail-output.v1",
        transport=TransportKind.HTTP,
        provider_ids=("test",),
        supported_url_patterns=(r"https://example\.com/jobs/.*",),
        output_facts=("native_grade",),
        invocation_scope=InvocationScope.STATELESS_UNIT,
    )
    input_type = VacancyDetailInput
    result_type = VacancyDetailResult

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        parser_input: VacancyDetailInput,
        runtime: ParserRuntime,
    ) -> VacancyDetailResult:
        del runtime
        self.calls += 1
        return VacancyDetailResult(
            outcome=SingletonResultOutcome.SUCCESS,
            item=VacancyDetailOutput(
                target_provider_id=parser_input.target_provider_id,
                source_listing_id=parser_input.source_listing_id,
                canonical_vacancy_url=parser_input.vacancy_url,
                title="QA Engineer",
                company=None,
                description=None,
                requirements=(),
                responsibilities=(),
                conditions=(),
                skills=(),
                employment_types=(),
                salary=None,
                work_formats=(),
                remote_scopes=(),
                application_channels=(),
                native_grade=None,
            ),
        )


async def test_unknown_grade_enriches_then_rejects_after_provider_exhaustion(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        detail = _UnknownGradeDetailBundle()
        pipeline = GraphSearchPipeline(
            config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
            registry=ParserRegistry((_SearchBundle(), detail)),
            runtime_factory=_RuntimeFactory(),
        )

        execution = await pipeline.run(
            SearchRequest(
                query_variants=("QA",),
                grades=(Grade.SENIOR,),
            ),
            run_id="r-unknown-grade",
        )

        self.assertEqual(1, detail.calls)
        self.assertEqual(0, execution.processed_payload["result_count"])
        self.assertEqual(1, len(execution.processed_payload["filtered_out_results"]))
        self.assertEqual(
            ["insufficient_evidence:grades"],
            execution.processed_payload["filtered_out_results"][0]["decision_reasons"],
        )
```

- [ ] **Step 2: Run policy and coordinator tests and confirm unknown currently passes or rejects too early**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_filter_policy.py \
  tests/v2/test_runtime_graph_coordinator.py \
  tests/v2/test_runtime_graph_pipeline.py \
  tests/v2/test_postprocessing_pipeline.py -q
```

Expected: failures because policy decisions are boolean, unknown grade/salary currently pass, and preliminary graph selection cannot distinguish `needs_evidence` from `reject`.

- [ ] **Step 3: Define tri-state selection contracts**

Add to `contracts/enums.py`:

```python
class CriterionState(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class SelectionOutcome(StrEnum):
    KEEP = "keep"
    REJECT = "reject"
    NEEDS_EVIDENCE = "needs_evidence"
```

Replace `SelectionDecision` in `contracts/facts.py` with:

```python
@dataclass(frozen=True)
class CriterionEvaluation:
    criterion: str
    state: CriterionState
    reason: str | None = None


@dataclass(frozen=True)
class SelectionDecision:
    outcome: SelectionOutcome
    reasons: tuple[str, ...]
    criteria: tuple[CriterionEvaluation, ...] = ()

    @property
    def keep(self) -> bool:
        return self.outcome == SelectionOutcome.KEEP

    @property
    def can_enrich(self) -> bool:
        return self.outcome != SelectionOutcome.REJECT
```

Export the enums and dataclasses from `contracts/__init__.py`. Update `keep_all` and repository `_keep_selection` to return `SelectionDecision(SelectionOutcome.KEEP, ())`.

- [ ] **Step 4: Make the structured filter AST tri-state**

Replace `FilterEvaluation.keep` with `state: CriterionState`. A field with no concrete value returns `UNKNOWN` and reason `insufficient_evidence:<criterion>`; a concrete non-match returns `MISMATCH`; a match returns `MATCH`.

```python
@dataclass(frozen=True)
class FilterEvaluation:
    state: CriterionState
    reasons: tuple[str, ...]


_FIELD_CRITERIA: dict[FilterField, str] = {
    "work_format": "work_formats",
    "remote_scope": "remote_scopes",
    "vacancy_geography": "vacancy_geographies",
    "employer_geography": "employer_geographies",
    "relocation": "relocation",
}


def _evaluate_field_filter(
    condition: FieldFilter,
    facts: FilterFacts,
) -> FilterEvaluation:
    values = _field_values(condition.field, facts)
    concrete = tuple(value for value in values if value != UNKNOWN_VALUE)
    if not concrete:
        criterion = _FIELD_CRITERIA[condition.field]
        return FilterEvaluation(
            CriterionState.UNKNOWN,
            (f"insufficient_evidence:{criterion}",),
        )
    if condition.op == "any_of":
        matched = bool(set(concrete) & set(condition.values))
    elif condition.op == "none_of":
        matched = not bool(set(concrete) & set(condition.values))
    elif condition.op == "intersects":
        matched = _values_intersect_geographies(concrete, condition.values)
    else:
        raise ValueError(f"unsupported filter operator: {condition.op}")
    return FilterEvaluation(
        CriterionState.MATCH if matched else CriterionState.MISMATCH,
        () if matched else (condition.reason,),
    )
```

Use these exact combiners:

```python
def _combine_all(evaluations: tuple[FilterEvaluation, ...]) -> FilterEvaluation:
    mismatches = tuple(item for item in evaluations if item.state == CriterionState.MISMATCH)
    if mismatches:
        return FilterEvaluation(
            state=CriterionState.MISMATCH,
            reasons=_dedupe([reason for item in mismatches for reason in item.reasons]),
        )
    unknowns = tuple(item for item in evaluations if item.state == CriterionState.UNKNOWN)
    if unknowns:
        return FilterEvaluation(
            state=CriterionState.UNKNOWN,
            reasons=_dedupe([reason for item in unknowns for reason in item.reasons]),
        )
    return FilterEvaluation(CriterionState.MATCH, ())


def _combine_any(evaluations: tuple[FilterEvaluation, ...]) -> FilterEvaluation:
    matched = next(
        (item for item in evaluations if item.state == CriterionState.MATCH),
        None,
    )
    if matched is not None:
        return FilterEvaluation(CriterionState.MATCH, ())
    unknowns = tuple(item for item in evaluations if item.state == CriterionState.UNKNOWN)
    if unknowns:
        return FilterEvaluation(
            CriterionState.UNKNOWN,
            _dedupe([reason for item in unknowns for reason in item.reasons]),
        )
    return min(
        evaluations,
        key=lambda item: (len(item.reasons), item.reasons),
    )
```

Delete the `allow_unknown` parameter. `NotFilter` preserves `UNKNOWN`, swaps `MATCH` and `MISMATCH`, and emits its configured reason only for the resulting mismatch.

- [ ] **Step 5: Evaluate every hard criterion explicitly in filter policy**

Change `VacancyFilterCriteria.salary_from` to `compensation: CompensationCriterion | None`. Change `VacancyFilterFacts.native_grade` to `grades: tuple[str, ...]` and replace scalar salary bounds with `compensation: CompensationFact | None`.

Replace `VacancyFilterDecision` with this explicit policy result:

```python
@dataclass(frozen=True)
class VacancyFilterDecision:
    outcome: SelectionOutcome
    title_matches: bool
    include_in_filtered_out: bool
    reasons: tuple[str, ...]
    criteria: tuple[CriterionEvaluation, ...]

    @property
    def keep(self) -> bool:
        return self.outcome == SelectionOutcome.KEEP

    @property
    def can_enrich(self) -> bool:
        return self.outcome != SelectionOutcome.REJECT
```

Use these exact criterion helpers:

```python
def _grade_evaluation(
    requested: tuple[str, ...],
    actual: tuple[str, ...],
) -> CriterionEvaluation:
    if not requested:
        return CriterionEvaluation("grades", CriterionState.MATCH)
    if not actual:
        return CriterionEvaluation(
            "grades",
            CriterionState.UNKNOWN,
            "insufficient_evidence:grades",
        )
    if set(requested) & set(actual):
        return CriterionEvaluation("grades", CriterionState.MATCH)
    return CriterionEvaluation("grades", CriterionState.MISMATCH, "grade_mismatch")


def _compensation_evaluation(
    requested: CompensationCriterion | None,
    actual: CompensationFact | None,
) -> CriterionEvaluation:
    if requested is None:
        return CriterionEvaluation("compensation", CriterionState.MATCH)
    if (
        actual is None
        or actual.minimum is None
        or actual.currency is None
        or actual.period is None
        or actual.currency != requested.currency
        or actual.period != requested.period
        or (requested.gross is not None and actual.gross is None)
    ):
        return CriterionEvaluation(
            "compensation",
            CriterionState.UNKNOWN,
            "insufficient_evidence:compensation",
        )
    if requested.gross is not None and actual.gross != requested.gross:
        return CriterionEvaluation(
            "compensation",
            CriterionState.MISMATCH,
            "compensation_gross_mismatch",
        )
    if actual.minimum >= requested.minimum:
        return CriterionEvaluation("compensation", CriterionState.MATCH)
    return CriterionEvaluation(
        "compensation",
        CriterionState.MISMATCH,
        "compensation_below_requested_minimum",
    )
```

Compute query state from `RoleMatcher(criteria.queries).match(vacancy.title)`. Missing `posted_at` for a requested `published_since` criterion is `UNKNOWN`. Explicit exclusions remain terminal mismatches. Aggregate all criterion and AST states with the same `all` rules and map them to outcomes:

```python
def _selection_outcome(criteria: tuple[CriterionEvaluation, ...]) -> SelectionOutcome:
    if any(item.state == CriterionState.MISMATCH for item in criteria):
        return SelectionOutcome.REJECT
    if any(item.state == CriterionState.UNKNOWN for item in criteria):
        return SelectionOutcome.NEEDS_EVIDENCE
    return SelectionOutcome.KEEP
```

Return the policy result with:

```python
outcome = _selection_outcome(evaluations)
reasons = tuple(
    dict.fromkeys(
        evaluation.reason
        for evaluation in evaluations
        if evaluation.reason is not None
    )
)
return VacancyFilterDecision(
    outcome=outcome,
    title_matches=role_match.matched,
    include_in_filtered_out=(
        role_match.matched and outcome != SelectionOutcome.KEEP
    ),
    reasons=reasons,
    criteria=evaluations,
)
```

- [ ] **Step 6: Adapt graph selection without treating unknown as a pass**

In `runtime/selection.py`, read only `selection-facts.v5` nested fields into `VacancyFilterFacts`. Build vacancy geography scopes exactly once from the canonical location:

```python
def _canonical_vacancy_geographies(location: JsonObject) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *(f"city:{value}" for value in _strings(location.get("cities"))),
                *(f"country:{value}" for value in _strings(location.get("countries"))),
                *(f"region:{value}" for value in _strings(location.get("regions"))),
            )
        )
    )
```

The adapter reads `grade.resolved`, the complete `compensation` object, `workplace.formats`, `workplace.remote_scopes`, the canonical location scopes above, `employer_geographies`, and `relocation.supported`. Return the policy result unchanged:

```python
return SelectionDecision(
    outcome=decision.outcome,
    reasons=decision.reasons,
    criteria=decision.criteria,
)
```

Use this return from both `evaluate` and `evaluate_preliminary`; delete `allow_unknown_structured`.

In `graph_repository.py`, every preliminary guard must use:

```python
if not selection.can_enrich:
    # Persist the existing final reject evaluation and stop this branch.
```

After checking missing required providers, use:

```python
if missing:
    outcome = "enrich"
    stage = "preliminary"
elif final_selection.keep:
    outcome = "keep"
    stage = "final"
else:
    outcome = "reject"
    stage = "final"
```

Persist `final_selection.reasons` for the last branch. This makes an unknown fact enrichable while a provider exists and a final `insufficient_evidence:*` rejection after the provider set is exhausted.

- [ ] **Step 7: Run tri-state policy, postprocessing, and graph tests**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_filter_policy.py \
  tests/v2/test_postprocessing_pipeline.py \
  tests/v2/test_runtime_graph_coordinator.py \
  tests/v2/test_runtime_graph_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit the tri-state slice**

```bash
git add \
  plugins/job-harness/src/job_harness/v2/contracts/enums.py \
  plugins/job-harness/src/job_harness/v2/contracts/facts.py \
  plugins/job-harness/src/job_harness/v2/contracts/__init__.py \
  plugins/job-harness/src/job_harness/v2/postprocessing/filter_ast.py \
  plugins/job-harness/src/job_harness/v2/postprocessing/filter_policy.py \
  plugins/job-harness/src/job_harness/v2/runtime/selection.py \
  plugins/job-harness/src/job_harness/v2/persistence/graph_repository.py \
  plugins/job-harness/tests/v2/test_filter_policy.py \
  plugins/job-harness/tests/v2/test_postprocessing_pipeline.py \
  plugins/job-harness/tests/v2/test_runtime_graph_coordinator.py \
  plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py
git diff --cached --check
git commit -m "refactor: make hard selection tri-state"
```

### Task 6: Shared Ranking Semantics And Canonical Public Projection

**Files:**
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/ranking.py`
- Modify: `plugins/job-harness/src/job_harness/v2/runtime/public_projection.py`
- Modify: `plugins/job-harness/src/job_harness/v2/presentation/formatters.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_ranking.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_formatters.py`

**Interfaces:**
- Consumes: the same `RoleMatcher` and `selection-facts.v5` payload used by hard selection.
- Produces: role-consistent deterministic scores and a public result containing exact canonical location, workplace, grade, compensation, and relocation facts.

- [ ] **Step 1: Write failing ranking and projection regressions**

Replace `test_runtime_ranking.py` with assertions that a role mismatch cannot outscore a match and that grade bonus reads `grade.resolved`:

```python
def test_role_mismatch_cannot_be_promoted_by_description_tokens(self) -> None:
    ranker = GraphVacancyRanker(SearchRequest(query_variants=("Java Engineer",)))

    mismatch = ranker.score(
        {
            "title": "QA Automation Engineer (Java)",
            "description": "Java engineer Java engineer",
            "derived_facts": {
                "structured-selection-facts": {
                    "grade": {"resolved": [], "conflict": False}
                }
            },
        }
    )
    match = ranker.score(
        {
            "title": "Senior Java Platform Engineer",
            "derived_facts": {
                "structured-selection-facts": {
                    "grade": {"resolved": ["senior"], "conflict": False}
                }
            },
        }
    )

    self.assertEqual(0.0, mismatch)
    self.assertGreater(match, mismatch)
```

Add a projection test to `test_runtime_graph_pipeline.py` or the existing projection test module:

```python
def test_public_projection_uses_exact_canonical_selection_facts(self) -> None:
    projected = public_vacancy_projection(
        {
            "title": "Middle QA Lead",
            "vacancy_url": "https://example.com/jobs/1",
            "location": {"text": "stale"},
            "salary": {"salary_from": 1},
            "work_formats": ["remote"],
            "remote_scopes": [],
            "native_grade": "senior",
            "derived_facts": {
                "structured-selection-facts": {
                    "location": {
                        "raw_text": "London | Vilnius",
                        "cities": ["London", "Vilnius"],
                        "countries": ["GB", "LT"],
                        "regions": ["EU"],
                        "evidence": ["location"],
                    },
                    "workplace": {
                        "formats": ["hybrid", "remote"],
                        "remote_scopes": ["country:DE"],
                        "evidence": ["work_formats", "remote_scopes"],
                    },
                    "grade": {
                        "title_evidence": ["middle", "lead"],
                        "source_evidence": ["senior"],
                        "resolved": ["middle", "lead"],
                        "conflict": True,
                        "evidence": ["title", "native_grade"],
                    },
                    "compensation": {
                        "minimum": 300000,
                        "maximum": 400000,
                        "currency": "RUB",
                        "period": "month",
                        "gross": True,
                        "evidence": ["salary"],
                    },
                    "relocation": {"supported": True, "evidence": ["description"]},
                    "visa_sponsorship": {"supported": False, "evidence": ["description"]},
                    "employer_geographies": ["country:RU"],
                }
            },
        }
    )

    self.assertEqual(["London", "Vilnius"], projected["location"]["cities"])
    self.assertEqual(["country:DE"], projected["workplace"]["remoteScopes"])
    self.assertEqual(["middle", "lead"], projected["grade"]["resolved"])
    self.assertEqual("RUB", projected["compensation"]["currency"])
    self.assertIs(projected["relocationSupported"], True)
    self.assertNotIn("evidence", str(projected))
    self.assertNotIn("nativeGrade", projected)
```

- [ ] **Step 2: Run ranking and projection tests and confirm semantics diverge**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_runtime_ranking.py \
  tests/v2/test_runtime_graph_pipeline.py \
  tests/v2/test_formatters.py -q
```

Expected: failures because ranking uses unordered token overlap and projection exposes raw/flat facts rather than canonical v5 facts.

- [ ] **Step 3: Make ranking consume the shared role match**

Replace ranker's query-token implementation with:

```python
class GraphVacancyRanker:
    def __init__(self, request: SearchRequest) -> None:
        self._matcher = RoleMatcher(request.query_variants)
        self._grades = frozenset(grade.value for grade in request.grades)

    def score(self, facts: JsonObject) -> float:
        match = self._matcher.match(_text(facts.get("title")))
        if not match.matched:
            return 0.0
        relevance = match.strength * 75.0
        resolved_grades = frozenset(_canonical_grade_values(facts))
        if self._grades and self._grades & resolved_grades:
            relevance += 20.0
        if _text(facts.get("description")) or _text(facts.get("summary")):
            relevance += 2.0
        return round(min(relevance, 100.0), 2)
```

Delete the duplicate token-overlap helpers from `ranking.py`.

- [ ] **Step 4: Project canonical facts and suppress raw intermediates**

Extend `_INTERNAL_KEYS` so the base object does not project raw selection inputs:

```python
_INTERNAL_KEYS = {
    "derived_facts",
    "target_provider_id",
    "location",
    "salary",
    "work_formats",
    "remote_scopes",
    "native_grade",
    "relocation",
    "visa_sponsorship",
    "raw",
    "raw_text",
}
```

Replace `_project_selection_facts` with:

```python
def _project_selection_facts(projected: JsonObject, selection: JsonObject) -> None:
    location = _public_fact(selection.get("location"), drop=("evidence",))
    if location:
        projected["location"] = _camelize_fact(location)
    workplace = _public_fact(selection.get("workplace"), drop=("evidence",))
    if workplace:
        projected["workplace"] = _camelize_fact(workplace)
    grade = _public_fact(
        selection.get("grade"),
        keep=("resolved", "conflict"),
    )
    if grade:
        projected["grade"] = grade
    compensation = _public_fact(selection.get("compensation"), drop=("evidence",))
    if compensation and any(value is not None for value in compensation.values()):
        projected["compensation"] = compensation
    relocation = _object(selection.get("relocation"))
    if relocation and isinstance(relocation.get("supported"), bool):
        projected["relocationSupported"] = relocation["supported"]
    visa = _object(selection.get("visa_sponsorship"))
    if visa and isinstance(visa.get("supported"), bool):
        projected["visaSponsorshipAvailable"] = visa["supported"]
    employer_geographies = selection.get("employer_geographies")
    if isinstance(employer_geographies, list) and employer_geographies:
        projected["employerGeographies"] = list(employer_geographies)
```

Use these strict helpers. `_camelize_fact` maps only the two canonical snake-case fields exposed by the public JSON contract:

```python
def _public_fact(
    value: object,
    *,
    keep: tuple[str, ...] | None = None,
    drop: tuple[str, ...] = (),
) -> JsonObject:
    fact = _object(value)
    if fact is None:
        return {}
    allowed = set(keep) if keep is not None else None
    return {
        key: _project_value(item)
        for key, item in fact.items()
        if key not in drop
        and (allowed is None or key in allowed)
        and not _is_empty(item)
    }


def _camelize_fact(value: JsonObject) -> JsonObject:
    aliases = {
        "raw_text": "rawText",
        "remote_scopes": "remoteScopes",
    }
    return {aliases.get(key, key): item for key, item in value.items()}


def _object(value: object) -> JsonObject | None:
    return value if isinstance(value, dict) else None
```

Update formatter salary rendering to read `result.compensation.minimum`, `maximum`, `currency`, `period`, and `gross`. Update location rendering to prefer structured cities/countries and retain `rawText` as the source display string.

- [ ] **Step 5: Run ranking, projection, graph, and formatter tests**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_runtime_ranking.py \
  tests/v2/test_runtime_graph_pipeline.py \
  tests/v2/test_formatters.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit ranking and projection**

```bash
git add \
  plugins/job-harness/src/job_harness/v2/runtime/ranking.py \
  plugins/job-harness/src/job_harness/v2/runtime/public_projection.py \
  plugins/job-harness/src/job_harness/v2/presentation/formatters.py \
  plugins/job-harness/tests/v2/test_runtime_ranking.py \
  plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py \
  plugins/job-harness/tests/v2/test_formatters.py
git diff --cached --check
git commit -m "refactor: align ranking and public facts"
```

### Task 7: End-To-End Contract Migration And Deterministic Gate

**Files:**
- Modify: `docs/search-system-spec.md`
- Modify: `docs/v2-to-be-scraper-contract-flow.md`
- Modify: `plugins/job-harness/skills/job-search-workflow/SKILL.md`
- Modify: `scripts/v2_live_e2e.py`
- Modify: `scripts/verify_v2.py`
- Modify: `plugins/job-harness/tests/v2/test_postprocessing_pipeline.py`
- Modify: `plugins/job-harness/tests/v2/test_runtime_fact_requirement_planner.py`
- Modify: `plugins/job-harness/tests/v2/test_runtime_independent_source_bundles.py`
- Modify: `plugins/job-harness/tests/v2/test_source_catalog.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_graph_pipeline.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_sources_contract_first.py`
- Test: `plugins/job-harness/tests/v2/test_runtime_skill_contract.py`
- Test: `plugins/job-harness/tests/v2/test_verify_v2.py`

**Interfaces:**
- Consumes: all contracts from Tasks 1-6 through the real graph pipeline.
- Produces: one deterministic proof that CLI input, parser observations, canonical derivation, hard selection, ranking, SQLite output, report output, and filtered-out diagnostics agree.

- [ ] **Step 1: Add one real graph contract regression**

Import `CompensationCriterion`, `CompensationPeriod`, `RemoteScope`, and `SalaryRange`, then add this bundle at module scope:

```python
class _CanonicalSearchBundle(_SearchBundle):
    manifest = replace(
        _SearchBundle.manifest,
        output_facts=(
            "title",
            "location",
            "salary",
            "work_formats",
            "remote_scopes",
            "native_grade",
            "summary",
            "vacancy_url",
        ),
    )

    async def execute(
        self,
        parser_input: SearchListingInput,
        runtime: ParserRuntime,
    ) -> SearchListingResult:
        del parser_input, runtime
        return SearchListingResult(
            outcome=SearchResultOutcome.SUCCESS,
            items=(
                SearchListingOutput(
                    source_id="test",
                    target_provider_id="test",
                    source_listing_id="canonical-1",
                    title="Middle Data Analyst",
                    company=CompanyRef(name="Example"),
                    location=SourceLocation(
                        text="London | Vilnius",
                        cities=("London", "Vilnius"),
                        countries=("GB", "LT"),
                        regions=("EU",),
                    ),
                    salary=SalaryRange(
                        salary_from=300_000,
                        salary_to=400_000,
                        currency="RUR",
                        gross=True,
                        period="month",
                    ),
                    work_formats=("hybrid", "remote"),
                    remote_scopes=(RemoteScope("country", "DE"),),
                    native_grade="senior",
                    posted_at=None,
                    vacancy_url="https://example.com/jobs/canonical-1",
                    apply_url=None,
                    summary="Relocation assistance is available.",
                ),
            ),
            continuations=(),
            collection_units_consumed=1,
        )
```

Insert this method in the existing `GraphSearchPipelineTest` class:

```python
async def test_pipeline_uses_one_canonical_contract_end_to_end(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
        pipeline = GraphSearchPipeline(
            config=GraphSearchPipelineConfig(runs_dir=Path(directory)),
            registry=ParserRegistry((_CanonicalSearchBundle(),)),
            runtime_factory=_RuntimeFactory(),
        )

        execution = await pipeline.run(
            SearchRequest(
                query_variants=("Data Analyst",),
                grades=(Grade.MIDDLE,),
                compensation=CompensationCriterion(
                    250_000,
                    "RUB",
                    CompensationPeriod.MONTH,
                    gross=True,
                ),
                work_formats=(WorkFormat.HYBRID,),
            ),
            run_id="r-canonical-contract",
        )

        self.assertEqual(1, len(execution.final_items))
        result = execution.final_items[0]
        self.assertEqual(["London", "Vilnius"], result["location"]["cities"])
        self.assertEqual(["GB", "LT"], result["location"]["countries"])
        self.assertEqual(
            ["country:DE"],
            result["workplace"]["remoteScopes"],
        )
        self.assertEqual(
            {"resolved": ["middle"], "conflict": True},
            result["grade"],
        )
        self.assertEqual(
            {
                "minimum": 300_000,
                "maximum": 400_000,
                "currency": "RUB",
                "period": "month",
                "gross": True,
            },
            result["compensation"],
        )
        self.assertIs(result["relocationSupported"], True)
        self.assertEqual([], execution.processed_payload["filtered_out_results"])
        report = execution.paths.report_html_path.read_text(encoding="utf-8")
        self.assertIn("London", report)
        self.assertIn("Vilnius", report)
        self.assertIn("country:DE", report)
        self.assertIn("300000", report)
```

- [ ] **Step 2: Run the graph regression and confirm remaining callers fail**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_runtime_graph_pipeline.py::GraphSearchPipelineTest::test_pipeline_uses_one_canonical_contract_end_to_end -q
```

Expected: failure at the first stale scalar compensation, flat fact path, or projection assumption that remains.

- [ ] **Step 3: Migrate every request-level and canonical-path scan result**

Run these scans and update every result as follows:

```bash
rg -n "request\.salary_from|SearchCriterion\.SALARY_FROM|--salary-from" \
  plugins/job-harness/src plugins/job-harness/tests scripts docs
rg -n 'structured-selection-facts.*(grade|salary_min|work_formats|remote_scopes|vacancy_geographies)' \
  plugins/job-harness/src plugins/job-harness/tests scripts docs
```

For search construction, use:

```python
compensation=CompensationCriterion(
    minimum=150_000,
    currency="RUB",
    period=CompensationPeriod.MONTH,
)
```

For canonical paths, use:

```text
grade.resolved
compensation.minimum
workplace.formats
workplace.remote_scopes
location.cities
location.countries
location.regions
relocation.supported
```

Do not alter source API payload keys named `salary_from`; those remain evidence fields local to their source formats.

- [ ] **Step 4: Update user workflow and system contract documentation**

Replace scalar examples with:

```bash
job-harness-v2 search \
  --query "Senior QA Engineer" \
  --salary-minimum 250000 \
  --salary-currency RUB \
  --salary-period month
```

Document these exact semantics:

```text
- Compensation filtering requires minimum, currency, and period together.
- No currency conversion is performed.
- Missing lower bound, currency, or period is insufficient evidence for a hard compensation criterion.
- Unknown hard facts are excluded from final results and appear in filtered-out diagnostics.
- Result location, workplace, grade, compensation, and relocation fields are the same canonical facts used by selection.
```

- [ ] **Step 5: Run focused source, skill, and graph suites**

Run:

```bash
uv --directory plugins/job-harness run pytest \
  tests/v2/test_contracts_search.py \
  tests/v2/test_contracts_criteria.py \
  tests/v2/test_contracts_records_and_scraper.py \
  tests/v2/test_contracts_independent_scrapers.py \
  tests/v2/test_filter_policy.py \
  tests/v2/test_runtime_fact_derivers.py \
  tests/v2/test_runtime_fact_requirement_planner.py \
  tests/v2/test_runtime_role_matching.py \
  tests/v2/test_runtime_ranking.py \
  tests/v2/test_runtime_graph_coordinator.py \
  tests/v2/test_runtime_graph_pipeline.py \
  tests/v2/test_runtime_sources_contract_first.py \
  tests/v2/test_runtime_skill_contract.py \
  tests/v2/test_verify_v2.py -q
```

Expected: PASS.

- [ ] **Step 6: Run static migration scans**

Run:

```bash
rg -n "request\.salary_from|SearchCriterion\.SALARY_FROM|--salary-from|selection-facts\.v4" \
  plugins/job-harness/src plugins/job-harness/tests scripts docs
```

Expected: no matches.

Run:

```bash
rg -n "fuzzy_tokens_match|QUERY_FUZZY_BOUNDS|def _query_score" \
  plugins/job-harness/src/job_harness/v2/postprocessing/filter_policy.py \
  plugins/job-harness/src/job_harness/v2/runtime/ranking.py
```

Expected: no matches.

- [ ] **Step 7: Run deterministic and bounded live verification**

Run:

```bash
python3 scripts/verify_v2.py --skip-live
```

Expected: exit code 0 with lint, type, architecture, and deterministic test gates passing.

Run:

```bash
python3 scripts/verify_v2.py --live-profile light
```

Expected: exit code 0; the bounded HH/company live profile completes without schema, CLI, or projection errors. A source without complete compensation dimensions may report insufficient evidence, but must not silently pass a compensation criterion.

- [ ] **Step 8: Commit the end-to-end migration**

```bash
git add \
  docs/search-system-spec.md \
  plugins/job-harness/skills/job-search-workflow/SKILL.md \
  plugins/job-harness/src \
  plugins/job-harness/tests/v2 \
  scripts/v2_live_e2e.py \
  scripts/verify_v2.py
git diff --cached --check
git commit -m "test: prove canonical search contract end to end"
```

## Completion Evidence

The implementation worker records these outputs in the final handoff:

```text
1. Focused pytest command and pass count.
2. python3 scripts/verify_v2.py --skip-live exit code and elapsed time.
3. python3 scripts/verify_v2.py --live-profile light exit code and elapsed time.
4. The end-to-end run artifact path containing the canonical result.
5. The static scan proving removed request and v4 fact names are absent.
6. Any source whose compensation remains dimensionally unknown, with the exact missing field.
```
