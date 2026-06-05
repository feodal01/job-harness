# Resilient Scraping Architecture

## 1. Goals

`job-harness` is the runtime an AI agent uses to search jobs across many aggregators and employer career sites. The system must:

1. **Be honest.** Every advertised flag (`remote_only`, `country`, `experience`, `has_salary`, `location`, `sources`, `profile`) is verifiably applied per source. If a source cannot apply a flag, the system says so instead of silently ignoring it.
2. **Be cancellable.** Any tool call can be stopped from the agent side and returns control immediately. A hung site must never block the agent's turn.
3. **Be durable.** Every listing the engine has parsed is on disk before the engine moves on. `kill -9` loses at most one in-flight record.
4. **Be fail-open.** One slow or broken source must not block, slow, or discard results from any other source.
5. **Be observable.** Every source carries a closed-enum failure mode and a per-flag enforcement marker in the result. An agent can see, without guessing, what was honestly applied and what wasn't.

These goals are non-negotiable acceptance criteria — see §15.

---

## 2. Repository Layout (reference)

The installable runtime is `plugins/job-harness/`. Paths in this plan are relative to the repo root.

```
plugins/job-harness/
  scripts/mcp-server.py
  src/job_harness/
    base.py                     # Aggregator scraper ABC + capability declaration
    browser.py                  # Browser factory (rebrowser-playwright)
    cli.py
    company_career_batch.py     # CLI-only batch runner (async Playwright)
    company_career_search.py    # MCP path for live company-career pages
    company_directory.py
    countries.py
    employer_cache.py
    employer_resolver.py
    filters.py
    formatters.py
    models.py                   # SearchRequest, SearchParams, JobListing, SearchResults
    registry.py                 # @register_scraper, single capability-aware registry
    run_journal.py              # On-disk JSONL run journal (§13)
    run_registry.py             # Run lifecycle, MAX_CONCURRENT_RUNS, GC (§12)
    search_engine.py            # Orchestrator (§5)
    browser_pool.py             # Async BrowserPool over async Playwright (§6)
    http_runner.py              # Bounded HTTP source runner (§7)
    scrapers/
      __init__.py
      cis_sources.py            # HTTP scrapers
      company_directory.py      # "company_directory" pseudo-source
      habr_career.py
      hh_ru.py                  # hh_ru, hh_kz, hh_uz, rabota_by, headhunter_kg
      http_common.py            # fetch_text, fetch_json (deadline-aware)
      career/                   # Per-company career scrapers, unified base
        vk.py
        ibs.py
  tests/                        # See §11
  data/
    company-directory.json
    company-careers.json
    company-careers-public.json
    source_baselines.json       # Sanity-check baselines (§9.3)
    .runs/<run_id>/             # Run journals (§13)
```

Verification gate: `python scripts/verify_repo.py full`.

---

## 3. Honest Flag Enforcement

### 3.1 Capability matrix

Every scraper declares, as class data, what filter support it can prove:

```python
class FilterSupport(StrEnum):
    SERVER       = "server"        # Sent in the URL/API call; site filters before returning
    CLIENT       = "client"        # We can reliably read the attribute from the response
    BEST_EFFORT  = "best_effort"   # We sniff free-form text; false positives possible
    UNSUPPORTED  = "unsupported"   # Scraper cannot enforce this flag

class ScraperCapabilities(TypedDict):
    remote_only: FilterSupport
    country:     FilterSupport
    experience:  FilterSupport
    location:    FilterSupport
    has_salary:  FilterSupport
    query_match: FilterSupport
```

Declared via `capabilities: ClassVar[ScraperCapabilities]`. A test (§11.3) walks the registry at import time and fails CI if any scraper omits a key — the matrix cannot drift silently.

### 3.2 Engine policy per flag

For each requested filter `F`:

1. Partition candidate scrapers into `{server, client, best_effort, unsupported}`.
2. **Unsupported scrapers are not silently included.**
   * `strict_flags=True` (the default): drop them with `SourceState.SKIPPED_UNSUPPORTED_FLAG`.
   * `strict_flags=False`: include them; mark each returned listing with `raw["filter_uncertain"][F] = True`; downrank them in the dedupe quality tuple.
3. **Best-effort enforcement is double-checked.** After parsing, the engine reapplies the heuristic and records `raw["filter_decision"][F] ∈ {"kept","dropped","unknown"}` per listing.
4. **Client and server enforcement are trusted but verified** by the capability tests in §11.3.

### 3.3 Result-level summary

Every `SearchResults.summary` carries `flag_enforcement`:

```jsonc
"flag_enforcement": {
  "remote_only": {
    "requested": true,
    "policy": "strict",
    "by_source": {
      "hh_ru":      {"support": "server",      "applied": true},
      "hirify":     {"support": "client",      "applied": true},
      "hirehi":     {"support": "best_effort", "applied": true},
      "vk":         {"support": "unsupported", "applied": false, "action": "skipped"}
    }
  },
  "country":    { ... },
  "experience": { ... }
}
```

This is the agent's read-out of "did the flag honestly do what it said".

### 3.4 Aggregator scraper capabilities

| Source | `remote_only` | `country` | `experience` | `query` | Notes |
|--------|---------------|-----------|--------------|---------|-------|
| `hh_ru` family | server (`schedule=remote`) | server | server (junior/middle/senior → noExperience/between1And3/between3And6) | server (`text=`) | Anti-bot risk on hh.ru; see §8 |
| `habr_career` | server (`remote=true`) | client (RU only) | server (`qualification=`) | server (`q=`) | Remote detected by RU phrase substring; verified server-side too |
| `hirehi` | best_effort | client (RU only) | best_effort | server (`query=`) | Anchor-text parsing |
| `hirify` | client (`work_format`) | client (`country`) | best_effort | server (`search=`) | JSON API |
| `staff_am` | client (`is_remote`) | client (AM only) | best_effort | partial (URL-category map) | __NEXT_DATA__ |
| `geekjob` | best_effort | best_effort | best_effort | client | HTML anchors |
| `talento` | best_effort | best_effort | best_effort | best_effort | aria-label parsing |
| `finder_work` | client (`distant_work`) | client (locations) | client (`experience`) | server | JSON API |
| `it_jobs_uz` | client (`workType`) | client (UZ) | client (`experienceLevel`) | server | JSON API |
| `jobturbo` | best_effort | best_effort | unsupported | server | ld+json |
| `getmatch` | client (`location_requirements.format`) | client | best_effort | server (specialization) | Multi-step (specializations + offers) |
| `company_directory` | client | client | unsupported | client | No per-vacancy data |
| `career:vk` | unsupported | unsupported | unsupported | partial (specialty map) | Per-company scraper |
| `career:ibs` | unsupported | unsupported | unsupported | partial | Per-company scraper |

`country` is enforced by registry-side `supports_country` lookup before dispatch; a scraper whose `countries` tuple excludes the requested country is `SKIPPED` with `failure_mode=not_in_country`.

---

## 4. Layer Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ MCP Server (scripts/mcp-server.py)                                 │
│  Sync surface:    search, resolve, resolve_company,                │
│                   search_company_careers (all hard-capped 25 s)    │
│  Async surface:   search_start, search_status, search_results,     │
│                   search_refine, search_cancel, list_active_runs    │
│  Read-only:       list_sources, search_company_jobs,               │
│                   cache_get, cache_upsert, cache_stats             │
└────────────────────────┬───────────────────────────────────────────┘
                         │ async def tool, MCP cancel → asyncio.CancelledError
┌────────────────────────▼───────────────────────────────────────────┐
│ RunRegistry (run_registry.py)                                      │
│  • run_id → {asyncio.Task, run_dir, started_at, last_poll_at}     │
│  • MAX_CONCURRENT_RUNS=4, RUN_DISK_CAP_MB=500, idle GC sweep      │
└────────────────────────┬───────────────────────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────────────────────┐
│ SearchEngine (search_engine.py)                                    │
│  • Resolve sources × country × profile × capability policy         │
│  • Concurrent dispatch: HTTP runner + async BrowserPool            │
│  • Per-source hard deadline; per-listing journal append+fsync     │
│  • Honest filter enforcement + summary                             │
│  • Cancel-aware: CancelledError propagates from MCP                │
└────────┬───────────────────────────────────────┬───────────────────┘
         │                                       │
┌────────▼────────────────────┐ ┌────────────────▼──────────────────┐
│ HttpRunner (http_runner.py)  │ │ BrowserPool (browser_pool.py)     │
│ • asyncio.to_thread per      │ │ • async Playwright, one Browser   │
│   blocking urlopen           │ │ • asyncio.Semaphore(max_contexts) │
│ • Per-source deadline incl.  │ │ • Per-call hard deadline via      │
│   retries/backoff            │ │   asyncio.wait_for                │
│ • Global cool-down counter   │ │ • Anti-bot probe after every goto │
└────────┬─────────────────────┘ └────────────────┬──────────────────┘
         │                                        │
┌────────▼─────────────────────┐ ┌────────────────▼──────────────────┐
│ HTTP scrapers (transport=    │ │ Browser scrapers (transport=      │
│ HTTP): cis_sources,          │ │ BROWSER): hh_ru family,           │
│ habr_career,                 │ │ career/vk, career/ibs             │
│ company_directory            │ │                                   │
└──────────────────────────────┘ └───────────────────────────────────┘
                         │
                         ▼
                 RunJournal (run_journal.py)
                 • append-only JSONL + fsync per record
                 • atomic summary.json via tmp+rename
                 • readable by any reader after kill -9
```

Design properties this layout enforces:

* **MCP cancellation is automatic.** A `notifications/cancelled` from the client cancels the anyio scope inside the MCP SDK, which raises `asyncio.CancelledError` in the awaiting tool coroutine. Async Playwright's `await page.goto(...)` honours `CancelledError` instantly; pages close cleanly and contexts are reusable. No CancelToken plumbing is required for the browser path; the existing asyncio cancellation model is sufficient.
* **HTTP threads cannot be cancelled.** `asyncio.to_thread` cancellation cancels the coroutine only — the underlying blocking `urlopen()` thread runs to completion. The runner relies on `urlopen(timeout=N)` to bound each thread's lifetime; the engine's per-source budget is set to leave enough headroom for the longest single attempt + cleanup.
* **Browser concurrency uses async Playwright, not threads.** `sync_playwright` is built on greenlets + a single asyncio loop and is thread-bound — multi-thread sync use corrupts state. Async Playwright supports many concurrent contexts/pages in one event loop and integrates with asyncio cancellation cleanly. (`company_career_batch.py` already runs this way.)
* **Journal-first persistence.** Every listing is appended to disk and `fsync`'d before the engine accepts the next listing. Status and results tools read the journal — they never read engine memory.

---

## 5. SearchEngine

```python
class SearchEngine:
    def __init__(
        self,
        *,
        browser_pool: BrowserPool,
        http_runner: HttpRunner,
        cache_factory: Callable[[], EmployerCache | None],
        company_directory_path: Path,
        journal_root: Path,
    ): ...

    async def execute(
        self,
        request: SearchRequest,
        *,
        journal: RunJournal,
        progress: ProgressSink | None = None,
    ) -> SearchResults: ...
```

`SearchRequest` is an immutable dataclass holding the validated, normalised request (query, country code, profile, flags, deadlines).

Execution:

1. **Validate** — non-empty query, `max_results >= 1`, known profile, country normalised via `countries.normalize_country_code`.
2. **Resolve sources** — registry × country × profile × `strict_flags` policy from §3.2. Drop ineligible sources; record reason in journal.
3. **Dispatch concurrently** — HTTP and browser sources run in parallel from the moment of dispatch. There is no "HTTP phase then browser phase".
4. **Aggregate** — `as_completed` over both groups; each listing is journaled immediately, then aggregated in memory for dedup/filter purposes.
5. **Cancel-aware** — `asyncio.CancelledError` from any source or from the run's cancellation propagates. The engine catches it at the run level, writes a final `state=cancelled` summary, and returns the partial `SearchResults`.
6. **Final merge** — apply the filter plan (§3.2), dedupe (quality-tuple ordering, hh vacancy-id + canonical URL + `(title,company)` keys), truncate to `max_results`, attach `summary.flag_enforcement` and `summary.result_sanity`.
7. **Optional resolve phase** — when `request.resolve` is true, the engine fans out per-company `resolve_company_careers` calls under the BrowserPool with a concurrency cap of `min(max_contexts, 3)` and a per-company deadline of `resolve_timeout_ms_per_company` (default 8 s). Each resolution is journaled; results are merged into the listings.

Per-call deadlines:

| Knob | Default | Scope |
|------|---------|-------|
| `inline_timeout_ms` | 25 000 | Hard ceiling on sync MCP tools (`search`, `resolve`, ...). Enforced by `@mcp.tool(timeout=...)` via `anyio.fail_after`. |
| `source_timeout_ms` | 30 000 | One scraper call including retries |
| `total_timeout_ms` | 90 000 | One non-blocking run end-to-end |
| `BrowserPool.page_timeout_ms` | 30 000 | One `run_with_page` operation |
| `BrowserPool.acquire_timeout_ms` | 5 000 | Waiting for a free context |
| `resolve_timeout_ms_per_company` | 8 000 | One resolve attempt |
| `RUN_IDLE_TIMEOUT_S` | 600 | A run with no status poll for this long is self-cancelled |

`total_timeout_ms` is a hard ceiling on wall-clock for a run. Any source still running when it fires is marked `cancelled / failure_mode=total_timeout`.

---

## 6. BrowserPool

Async, asyncio-native. Backed by one shared async `Browser` instance, with a `Semaphore` capping concurrent contexts.

```python
class BrowserPool:
    def __init__(
        self,
        *,
        max_contexts: int = 2,
        page_timeout_ms: int = 30_000,
        acquire_timeout_ms: int = 5_000,
    ): ...

    async def run_with_page(
        self,
        func: Callable[[Page], Awaitable[T]],
        *,
        timeout_ms: int,
    ) -> T: ...

    async def health(self) -> PoolHealth: ...
    async def shutdown(self) -> None: ...
```

`run_with_page` flow:

1. Acquire the context semaphore (with `acquire_timeout_ms` ceiling via `asyncio.wait_for`).
2. Create a fresh `BrowserContext` if the pool is empty; otherwise reuse an existing context. Context construction sets `accept_downloads=False`, `record_video=False`, `record_har_path=None`, and the stealth init scripts already in `browser.py`.
3. `page = await context.new_page()`; `page.set_default_timeout(page_timeout_ms)`.
4. Run `result = await asyncio.wait_for(func(page), timeout=timeout_ms / 1000)`. On `asyncio.TimeoutError` or `CancelledError`, propagate.
5. After `func` returns (or before raising), call `await is_blocked(page)` — see §8 — and replace the result with `BlockedResult(...)` if a block is detected.
6. In `finally`, `await page.close()` (best-effort; if it raises, the context is discarded instead of returned to the pool).

Health and restart:

* If `browser.is_connected()` becomes false at acquire time, the pool tears down and rebuilds the `Browser` instance. In-flight callers receive `BrowserDisconnected` when their pending acquire completes.
* If `run_with_page` raises `asyncio.TimeoutError` `recycle_after_consecutive_hangs` times in a row (default 2), the pool tears down and rebuilds the full `Browser`. Sources that were running at recycle time get attributed `failure_mode=pool_recycled`.

Cancellation:

* The engine never invents a CancelToken for the browser path. `await run_with_page(...)` participates in standard asyncio cancellation. When the calling task is cancelled, the `await asyncio.wait_for(func(page), ...)` re-raises `CancelledError` into `page.goto`/`page.locator`/etc., and Playwright cleans up the in-flight call.
* `page.close()` is wrapped in `await asyncio.wait_for(..., timeout=3.0)` to bound finalisation.

---

## 7. HttpRunner

Async dispatcher over a `ThreadPoolExecutor`. Maintains a global cool-down counter to short-circuit a network outage.

```python
class HttpRunner:
    def __init__(
        self,
        *,
        max_workers: int = 8,
        cooldown_threshold: int = 4,
        cooldown_window_s: float = 10.0,
    ): ...

    async def run_source(
        self,
        scraper: BaseScraper,
        params: SearchParams,
        *,
        deadline_ms: int,
    ) -> SourceOutcome: ...
```

Per-source budget:

* The runner submits one blocking `scraper.search(params, deadline_ms=...)` job to the executor and awaits it via `asyncio.to_thread`.
* `scraper.search` calls `fetch_text`/`fetch_json` with the same `deadline_ms`; the HTTP layer subdivides it across retries (next subsection).
* `asyncio.CancelledError` cancels the awaiting coroutine; the thread keeps running until the in-flight `urlopen` completes within its socket timeout. Subsequent sources are not waiting on this thread because there are `max_workers` worker threads; the engine treats the source as `cancelled` immediately.

Global cool-down:

* Each transient HTTP failure (`URLError`, OS-level `TimeoutError`, DNS failure) increments a shared counter.
* If the counter exceeds `cooldown_threshold` within `cooldown_window_s`, the runner short-circuits remaining unstarted sources to `(error, global_network_outage)` and stops dispatching new HTTP work for the rest of the run. The counter resets on the next successful HTTP response.

### 7.1 `fetch_text` / `fetch_json` semantics

```python
def fetch_text(
    url: str,
    *,
    deadline_ms: int,
    verify_ssl: bool = True,
    retries: int = 2,
) -> str: ...
```

Rules:

* `deadline_ms` is the total budget for this call including all retries. Each attempt's `urlopen(timeout=...)` is `max(1.0, min(remaining_seconds / attempts_left, 10.0))`.
* Status-code handling (`urllib.request.urlopen` raises `HTTPError` for all 4xx and 5xx — verified):
  * `200` → return body.
  * `429` or `503` with `Retry-After`: parse seconds (numeric, verified) or HTTP-date. Sleep `min(retry_after, remaining_deadline)`; retry. If `retry_after` exceeds the remaining deadline, raise `RateLimited` immediately without sleeping.
  * `5xx` without `Retry-After`: one retry inside the budget, then `HttpServerError`.
  * `403` with anti-bot body markers (Cloudflare ray-id, Distil, Akamai bot manager) → `AntiBotBlocked` immediately.
  * `30x` redirect to a path matching `/login|/auth|/sign-in|/signin|/users/sign_in` → `LoginRequired`.
  * Other `4xx` → `HttpClientError`, no retry.
* Linear backoff between retries, capped by the remaining budget.
* `URLError` / `OSError` / `socket.timeout` → retry within budget, then `NetworkError`. Each failure also feeds the runner-level cool-down counter.

`fetch_json` mirrors `fetch_text` and additionally classifies `JSONDecodeError` as `ParseError` (no retry — the body is likely a WAF or anti-bot HTML).

---

## 8. Anti-bot, captcha, and login detection

A pre-flight probe runs inside `BrowserPool.run_with_page` after every `page.goto()` (browser sources) and inside `fetch_text` (HTTP sources). The probe returns a `BlockReason` enum:

```python
class BlockReason(StrEnum):
    ANTI_BOT_PAGE  = "anti_bot_page"
    CAPTCHA_PAGE   = "captcha_page"
    LOGIN_REDIRECT = "login_redirect"
```

Browser probe:

* `page.title()` against case-insensitive regex: `Доступ ограничен`, `Just a moment`, `Verify you are human`, `Подтвердите, что вы человек`, `Attention Required`, `Checking your browser`.
* Selectors: `iframe[src*="recaptcha"]`, `iframe[src*="hcaptcha"]`, `iframe[src*="cf-chl"]`, `iframe[src*="distil"]`.
* `page.url` final path in `{/login, /auth, /sign-in, /signin, /users/sign_in}` while no auth cookies present → `LOGIN_REDIRECT`.

HTTP probe:

* `403` + body contains `Cloudflare` / `__cf_chl_` / `Distil` markers → `ANTI_BOT_PAGE`.
* `30x` final location matches login paths → `LOGIN_REDIRECT`.

When the probe trips, the engine maps the source to `state=BLOCKED` with the corresponding `failure_mode`. The original probe signal (matched title, iframe src, or URL) is recorded in `SourceStatus.anti_bot_signal`. No retry is attempted inside the same run for `BLOCKED` sources.

---

## 9. Observability — closed-enum failure taxonomy

### 9.1 Source state and failure mode

```python
class SourceState(StrEnum):
    OK                       = "ok"
    PARTIAL                  = "partial"
    TIMEOUT                  = "timeout"
    ERROR                    = "error"
    RATE_LIMITED             = "rate_limited"
    BLOCKED                  = "blocked"
    CANCELLED                = "cancelled"
    SKIPPED                  = "skipped"
    SKIPPED_UNSUPPORTED_FLAG = "skipped_unsupported_flag"

class FailureMode(StrEnum):
    # PARTIAL
    SLOW_PAGINATION         = "slow_pagination"
    MULTI_STEP_PARTIAL      = "multi_step_partial"
    # TIMEOUT
    GOTO_TIMEOUT            = "goto_timeout"
    HTTP_TIMEOUT            = "http_timeout"
    POOL_ACQUIRE_TIMEOUT    = "pool_acquire_timeout"
    # ERROR
    POOL_RECYCLED           = "pool_recycled"
    PARSE_ERROR             = "parse_error"
    HTTP_4XX                = "http_4xx"
    HTTP_5XX                = "http_5xx"
    NETWORK_ERROR           = "network_error"
    GLOBAL_NETWORK_OUTAGE   = "global_network_outage"
    # RATE_LIMITED
    HTTP_429                = "http_429"
    HTTP_503_RETRY_AFTER    = "http_503_retry_after"
    # BLOCKED
    ANTI_BOT_PAGE           = "anti_bot_page"
    CAPTCHA_PAGE            = "captcha_page"
    LOGIN_REDIRECT          = "login_redirect"
    # CANCELLED
    USER_CANCELLED          = "user_cancelled"
    TOTAL_TIMEOUT           = "total_timeout"
    IDLE_TIMEOUT            = "idle_timeout"
    # SKIPPED
    NOT_IN_COUNTRY          = "not_in_country"
    NOT_IN_PROFILE          = "not_in_profile"
    UNSUPPORTED_FLAG        = "unsupported_flag"
```

`SourceStatus`:

```python
@dataclass(frozen=True)
class SourceStatus:
    source: str
    display_name: str
    transport: Transport             # HTTP or BROWSER
    state: SourceState
    failure_mode: FailureMode | None # populated when state != OK
    duration_ms: int
    raw_count: int
    after_filter_count: int
    after_dedupe_count: int
    company_missing_count: int
    retries: int
    flag_enforcement: dict[str, FilterSupport]
    anti_bot_signal: str | None
    error_class: str | None
    error_message: str | None
```

The enum is closed: a CI test (§11.7) walks every `SourceStatus` produced by the test suite and asserts `(state != OK) ⇒ failure_mode is not None and failure_mode in FailureMode`.

### 9.2 Result-level summary

```jsonc
{
  "params": { ... },
  "timestamp": "...",
  "total": 17,
  "listings": [ ... ],
  "errors": [ "habr_career: rate limited", ... ],
  "summary": {
    "sources": { "<name>": SourceStatus, ... },
    "flag_enforcement": { ... §3.3 ... },
    "result_sanity": { ... §9.3 ... },
    "filters": {"enabled": [...], "before": N, "after": M, "removed": K},
    "dedupe":  {"enabled": true, "before": N, "after": M, "removed": K},
    "max_results": {"requested": 20, "returned": 17}
  }
}
```

### 9.3 Silent-regression guard

`data/source_baselines.json` is a hand-maintained map `(source, query_token, country) → min_expected_count`. The engine produces a `result_sanity` block:

```jsonc
"result_sanity": {
  "hh_ru":       {"raw_count": 12, "baseline_min": 5,  "verdict": "plausible"},
  "habr_career": {"raw_count": 0,  "baseline_min": 3,  "verdict": "suspicious",
                  "note": "0 results for popular query — possible parser regression"}
}
```

`verdict` does not modify listings. It surfaces a likely parser breakage so an agent or human can investigate. The file can be updated without code changes.

---

## 10. MCP Tool Surface

### 10.1 Why two surfaces

When the agent calls an MCP tool, its turn cannot progress until the tool returns. A synchronous tool that takes 60+ s would freeze the agent. The split is:

* **Synchronous tools** — bounded by `inline_timeout_ms` (default 25 s). For short queries, narrow source lists, and single-company resolves. The hard ceiling is enforced by `@mcp.tool(timeout=...)` via `anyio.fail_after`; a timed-out tool returns an MCP `ErrorData` and the run state is `cancelled / total_timeout`.
* **Non-blocking tools** — `search_start` kicks off a background `asyncio.Task` and returns a `run_id` in under 100 ms. The agent polls `search_status` / `search_results`, can `search_cancel`, can `search_refine` an old run. The run survives MCP transport cancellation and server restart via the journal.

### 10.2 Synchronous tools (hard-capped)

| Tool | Purpose | Inline ceiling |
|------|---------|---------------|
| `search` | Short, narrow-scope listing search | 25 s |
| `resolve` | Resolve a small list of listings to employer pages | 20 s |
| `resolve_company` | Resolve one company | 15 s |
| `search_company_careers` | Live-probe career pages, capped at 20 companies (`max_companies > 20` requires `force_large=True`; >50 always routes through the CLI batch) | 25 s |
| `list_sources`, `cache_get`, `cache_upsert`, `cache_stats`, `search_company_jobs` | Read-only / sub-50 ms | n/a |

Cancellation: `notifications/cancelled` from the client is propagated by the MCP SDK to the tool's anyio cancel scope, which surfaces as `asyncio.CancelledError` in the awaiting tool coroutine. The engine catches it, writes a partial result, and returns. No custom cancel plumbing is needed.

### 10.3 Non-blocking tools

```python
@mcp.tool
async def search_start(query: str, **flags) -> dict:
    """Kick off a search in the background. Returns immediately.
    Result: {"run_id": str, "run_dir": str, "started_at": iso8601}.
    Errors: {"error": "max_concurrent_runs_reached", "active_runs": [...]}."""

@mcp.tool
async def search_status(run_id: str) -> dict:
    """Cheap poll (<50 ms). Reads run journal. Returns:
    {"run_id": ..., "state": "running"|"completed"|"cancelled"|"failed",
     "started_at": ..., "elapsed_ms": ...,
     "sources": {<name>: SourceStatus, ...},
     "listings_count": int,
     "flag_enforcement": {...},
     "result_sanity": {...},
     "errors": [...] }."""

@mcp.tool
async def search_results(run_id: str, max_results: int = 20,
                         include_partial: bool = True) -> dict:
    """Returns the SearchResults snapshot derived from the journal.
    Works on running, completed, cancelled, and failed runs."""

@mcp.tool
async def search_cancel(run_id: str) -> dict:
    """Cancel the run. Returns immediately {"state": "cancelling"}.
    Idempotent."""

@mcp.tool
async def search_refine(
    run_id: str, *,
    experience: str | None = None,
    has_salary: bool = False,
    remote_only: bool = False,
    exclude_companies: str | None = None,
    exclude_keywords: str | None = None,
    exclude_keywords_context: str | None = None,
    location: str | None = None,
    max_results: int = 20,
    strict_refine: bool = False,
) -> dict:
    """Re-filter the journal of a finished run without re-scraping.
    Returns a SearchResults snapshot. Honours the same flag_enforcement
    semantics; listings whose source declared a refine filter as
    'unsupported' are tagged raw['filter_uncertain'][flag]=True unless
    strict_refine=True (in which case they are dropped)."""

@mcp.tool
async def list_active_runs(limit: int = 20) -> dict:
    """Returns recent runs from data/.runs/. Useful after server restart."""
```

### 10.4 Agent decision rule (sync vs async)

The `job-searcher` agent's system prompt and the `aggregator-scrapers` skill carry this verbatim — without it the agent will default to sync and re-create the freeze problem:

```
Definitions:
  broad := sources in {"all", null} OR len(sources_list) > 3 OR resolve=True OR profile != "fast"
  narrow := profile == "fast" OR (sources_list given and len(sources_list) <= 3)

User intent hints:
  "не торопись" / "тщательно" / "подробно" / "полный обзор"  →  force async
  "быстро" / "только посмотреть" / "первые несколько"        →  prefer sync (fast profile)

Tool choice:
  broad OR async-hint                                        →  search_start + poll
  narrow AND not resolve AND no async-hint                   →  search (sync)
```

### 10.5 Polling cadence

The agent skill teaches this loop:

```
delays = [1.5, 3, 5, 8, 12, 20, 30, 30, 30]   # seconds, capped at 30
elapsed = 0
for d in delays:
    wait(d)                          # the agent's turn can interleave other work
    elapsed += d
    s = search_status(run_id)
    if s.state != "running": break
    if s.listings_count >= target_min and elapsed >= 15:
        search_cancel(run_id); break
    if elapsed > 120:
        search_cancel(run_id); break
final = search_results(run_id)
```

Each `search_status` call is under 50 ms (reads the journal), so total polling overhead is well under 1 s.

### 10.6 Cancellation semantics

`search_cancel(run_id)`:

1. Calls `Task.cancel()` on the run's asyncio task.
2. `asyncio.CancelledError` raises into whatever the engine is awaiting — `await asyncio.gather(...)` of source coroutines, or `await asyncio.wait_for(...)` inside a `BrowserPool.run_with_page`. For browser sources, this propagates into `await page.goto(...)` and Playwright cleans up the in-flight call within tens of milliseconds (verified empirically). For HTTP sources, the awaiting coroutine is cancelled; the underlying thread keeps running until its `urlopen` socket times out, but the engine no longer waits on it.
3. The engine's outer `try/except CancelledError` block writes a final `state=cancelled` summary and a `run_finished` journal record, then re-raises.
4. The MCP tool returns `{"state": "cancelling"}` immediately. The agent's next `search_results` call reads the journal — if the cancel cleanup is still in progress, the state will be `cancelling`; otherwise `cancelled`.

---

## 11. Test Strategy

Test files live under `plugins/job-harness/tests/`. The suite uses:

* `tests/_support/fake_browser.py` — Playwright Page/Context/Browser test doubles with controllable hang/error injection.
* `tests/_support/clock.py` — freezable monotonic clock; engine code takes a `time_provider` injection.
* `tests/_support/recording_http.py` — records every `(url, headers, timeout)` passed to `fetch_text` / `fetch_json` for capability assertions.

### 11.1 BrowserPool — `test_browser_pool.py`

1. First `run_with_page` lazily creates a browser and a context.
2. Releasing a page returns the context to the pool; the next `run_with_page` reuses it.
3. `asyncio.TimeoutError` from `wait_for(func(page))` raises out of `run_with_page` within `timeout_ms + 200 ms` slack.
4. After timeout, the next `run_with_page` succeeds — no context leak.
5. `recycle_after_consecutive_hangs` consecutive timeouts trigger a browser rebuild; a counter on the mock `Browser` factory increments exactly once.
6. Cancelling the outer task during `run_with_page` propagates `CancelledError`; the page is closed and the context returned.
7. `Semaphore(max_contexts)` is honoured: `max_contexts + 1` concurrent calls show one waiting until a slot frees.
8. `browser.is_connected()=False` between calls forces rebuild before next acquire.
9. `accept_downloads=False` is set on every context; a fixture page that triggers a download does not stall the pool.
10. Anti-bot probe (§8) maps detection to `BlockedResult` with the matched signal.
11. `shutdown()` closes all contexts and the browser cleanly.

### 11.2 SearchEngine — `test_search_engine.py`

1. Mixed HTTP+browser sources dispatched concurrently; wall-clock ≈ max-source-duration, not sum.
2. A 60 s mock source with `source_timeout_ms=500` ms is cancelled; other sources complete.
3. `total_timeout_ms` ceiling cuts off remaining sources; partial result emitted; in-flight sources tagged `cancelled / total_timeout`.
4. Cancellation mid-flight produces a partial `SearchResults` with `errors=["cancelled"]`.
5. Strict-flag policy drops `unsupported` scrapers with `SKIPPED_UNSUPPORTED_FLAG`.
6. Lenient-flag policy includes them; listings carry `filter_uncertain[F]=True`.
7. Validation: empty query → `ValueError`; `max_results=0` → `ValueError`; unknown country → `ValueError`.
8. Dedupe prefers the richer listing across `(hh-id, url, title+company)` keys.
9. Zero results across all sources still returns a valid `SearchResults`.
10. Progress sink receives `started` / `completed` / `cancelled` events in order.
11. Resolve phase fans out under the pool with `min(max_contexts, 3)` concurrency.
12. Two concurrent `engine.execute()` calls do not share mutable state beyond the pool/runner.

### 11.3 Flag enforcement — `test_flag_enforcement.py`

Driven by a parametrised fixture covering every `(source, flag)` pair from §3.4.

* **server**: assert the URL/JSON request sent by the scraper includes the expected parameter when the flag is requested.
* **client**: feed a fixture listing with the attribute set/unset; assert engine accepts/rejects correctly.
* **best_effort**: feed text triggering the heuristic in either direction; assert `filter_decision[F]` is set.
* **unsupported (strict)**: scraper is `SKIPPED_UNSUPPORTED_FLAG`.
* **unsupported (lenient)**: listings carry `filter_uncertain[F]=True`.
* **summary**: top-level `flag_enforcement` reports the right `support` and `applied` per source.
* **matrix completeness**: walks the registry, asserts every scraper declares `capabilities` for every flag key. Fails CI if a new scraper is added without a capability declaration.

### 11.4 HTTP transport — `test_http_common.py`

1. `fetch_text` succeeds on first attempt — no retry.
2. Retries on `URLError` up to `retries`, then raises.
3. Per-attempt timeout shrinks as deadline decreases.
4. `deadline_ms=0` raises immediately without opening a socket.
5. `User-Agent` header is sent.
6. `429 + Retry-After: 2` retries after sleeping; if `Retry-After` exceeds remaining deadline, raises `RateLimited` immediately (no sleep).
7. `429 + Retry-After: <HTTP-date>` parsed.
8. `503 + Retry-After` → same path as 429.
9. `500` without `Retry-After` → one retry, then `HttpServerError`.
10. `403` body with `Cloudflare` marker → `AntiBotBlocked` immediately, no retry.
11. `404` → `HttpClientError`, no retry.
12. `30x` redirect to `/login` → `LoginRequired`.
13. `JSONDecodeError` in `fetch_json` → `ParseError`, no retry.
14. Cancellation between attempts short-circuits (verified by setting a cooperative cancel flag the runner exposes).

### 11.5 Resilience / failure-mode tests — `test_resilience.py`

Each test asserts the `(state, failure_mode)` pair, locking down the §9.1 taxonomy:

1. Hang in `page.goto` → `(timeout, goto_timeout)`; other sources unaffected.
2. Hang in `page.wait_for_timeout` (anti-bot pause) → `(timeout, goto_timeout)`.
3. Hang in HTTP `urlopen` → `(timeout, http_timeout)`.
4. Two concurrent hangs — wall-clock bounded by `total_timeout_ms`.
5. Crash in scraper.parse → `(error, parse_error)`.
6. hh.ru "Доступ ограничен" → `(blocked, anti_bot_page)`, `anti_bot_signal` populated.
7. Cloudflare interstitial → `(blocked, anti_bot_page)`.
8. reCAPTCHA iframe → `(blocked, captcha_page)`.
9. Final URL `/login` → `(blocked, login_redirect)`.
10. `429 + Retry-After: 2` inside budget → `ok` after sleep.
11. `503 + Retry-After: 60`, budget 5 s → `(rate_limited, http_503_retry_after)` immediately.
12. `500` no `Retry-After` → `(error, http_5xx)` after one retry.
13. `404` → `(error, http_4xx)`.
14. DNS failure on one source → `(error, network_error)`.
15. 4 sources hit `network_error` within 10 s → cool-down trips; remaining sources `(error, global_network_outage)`.
16. Browser disconnect mid-run → `(error, "BrowserDisconnected")`; pool rebuilds.
17. `recycle_after_consecutive_hangs` trips → `(error, pool_recycled)` on in-flight source.
18. Cancellation at t=100 ms → in-flight `(cancelled, user_cancelled)`.
19. `RUN_IDLE_TIMEOUT_S=0.5` → no polling → `(cancelled, idle_timeout)` within 1 s.
20. Multi-step source (`getmatch`: specializations OK, offers fail) → `(partial, multi_step_partial)`.
21. Browser fixture page triggers `<a download>` click; context with `accept_downloads=False` does not stall.
22. Resolver hang — one `resolve_company` hangs; others complete; final listings still returned.
23. `max_results=0` after filters → valid empty `SearchResults` (not error).
24. All sources fail → empty result, every status in summary, `errors` populated, no exception.
25. Silent regression alarm: hh.ru returns 0 for "python" / RU (baseline_min=10) → `result_sanity.hh_ru.verdict="suspicious"`.
26. `search_refine` applied to a finished run drops listings whose source declared the refine filter as `unsupported` (strict) or marks them `filter_uncertain` (lenient).

### 11.6 MCP surface — `test_mcp_server.py`, `test_mcp_async_surface.py`

Synchronous (`test_mcp_server.py`):

1. `search` returns within `inline_timeout_ms` even with a hung mock source.
2. `notifications/cancelled` during `search` propagates as `CancelledError`; tool returns a partial result.
3. `search` for HTTP-only sources never touches the browser pool (spy on `BrowserPool.run_with_page`).
4. `resolve` and `resolve_company` run companies in parallel under the pool.
5. `search_company_careers` honours per-company timeout.
6. `search_company_careers` with `max_companies > 50` and `force_large=False` returns a structured refusal pointing to the CLI.
7. `max_contexts=0` is rejected at startup.
8. Engine and pool are initialised once per process; tools share them.
9. Two concurrent `search` calls do not serialise; wall-clock ≤ 1.2 × single.

Non-blocking (`test_mcp_async_surface.py`):

1. `search_start` returns within 100 ms even if the engine task takes 30 s.
2. `search_status` reflects in-progress source statuses; ≤ 50 ms.
3. `search_results` on a running run returns a partial snapshot; on a completed run, the final result.
4. `search_cancel` returns `cancelling` immediately; `summary.json` reaches `cancelled` within 2 s.
5. `search_cancel` is idempotent.
6. Two concurrent `search_start` calls produce distinct `run_id`s and disjoint journals.
7. After `SessionEnd`, all active runs are cancelled and summaries flushed.
8. Server restart: a pre-restart `run_id` still works for `search_results`; `search_cancel` on it returns the failed-state journal.
9. `MAX_CONCURRENT_RUNS + 1`-th `search_start` returns the structured cap error.
10. `list_active_runs` returns runs after server restart (including `failed (server_restart)`).
11. Polling cadence test: a 1.6 s mock run completes after ≤ 2 polls.
12. `RUN_IDLE_TIMEOUT_S=0.5` self-cancels a run that nobody polls within 1 s.
13. `search_refine` returns within 200 ms on a journal of 100 listings; never invokes a scraper.
14. `search_refine` `strict_refine` vs lenient behaviour for unsupported flags.
15. Agent decision rule: a unit test asserts the `job-searcher` skill carries the §10.4 heuristic verbatim.

### 11.7 Run journal — `test_run_journal.py`

1. `engine.execute` writes `run_started` before any listing.
2. Each scraper output produces a `listing` record on disk before the engine returns control. Verified with `os.fsync` spy.
3. `summary.json` rewritten atomically: `kill -9` between write and rename (subprocess test) leaves the previous version intact.
4. `RunJournal.to_search_results` reproduces engine output exactly for a completed run.
5. `RunJournal.to_search_results` returns a coherent partial snapshot when the journal ends mid-source.
6. Crash recovery: a run with `state=running` and no live task is flagged `(failed, server_restart)`.
7. Disk-full simulation: `write` raises `OSError` → run state `(failed, disk_full)`; journal still readable.
8. `RUN_DISK_CAP_MB` GC removes oldest runs first; never deletes an active run.
9. `MAX_CONCURRENT_RUNS` enforced; `search_start` past the cap returns the structured error.
10. Closed-enum check: walks every `SourceStatus` produced by tests above and asserts `(state != OK) ⇒ failure_mode in FailureMode`.

### 11.8 Smoke and scraper tests

* Extend `test_search_smoke.py`:
  * Time-bounded smoke: 9 HTTP sources with 100 ms `time.sleep` each — wall-clock ≤ 400 ms.
  * Mixed smoke: 9 HTTP + 1 mock browser source — total ≤ 1 s.
  * Cancel smoke: cancel at 250 ms; ≥ 1 source completed, the rest `cancelled`.
* `test_cis_sources.py`: parametrised capability assertion per source.
* `test_habr_career.py`: budget-aware retry test.
* `test_company_career_search.py`: parallel-companies, hang, cancel tests.

---

## 12. Run Registry and Lifecycle

```python
class RunRegistry:
    def __init__(self, runs_root: Path, *,
                 max_concurrent_runs: int = 4,
                 run_disk_cap_mb: int = 500,
                 run_retention_hours: int = 24,
                 run_idle_timeout_s: int = 600): ...

    async def create(self, request: SearchRequest) -> Run: ...
    def get(self, run_id: str) -> Run | None: ...
    def list_recent(self, limit: int = 20) -> list[RunSummary]: ...
    async def gc(self) -> None: ...
    async def shutdown(self) -> None: ...

@dataclass
class Run:
    run_id: str                    # r-YYYYMMDD-HHMMSS-<6-hex>
    run_dir: Path                  # data/.runs/<run_id>/
    request: SearchRequest
    task: asyncio.Task | None      # None after server restart
    started_at: datetime
    last_poll_at: datetime         # bumped by every search_status / search_results
    state: RunState
```

* `run_id` format: `r-YYYYMMDD-HHMMSS-<6-hex>`. Deterministic prefix for easy tailing.
* Crash recovery: at server start, the registry scans `data/.runs/`. Any run whose `summary.json` says `state=running` but has no live task is flagged `(failed, server_restart)`. Journal stays — `search_results` still works.
* Concurrency cap: `MAX_CONCURRENT_RUNS` (default 4). `search_start` past the cap returns `{"error": "max_concurrent_runs_reached", "active_runs": [{"run_id": ..., "query": ..., "started_at": ...}, ...]}` without raising.
* Disk cap: `RUN_DISK_CAP_MB` (default 500). On startup and periodically, oldest non-active runs are deleted oldest-first until total size is under the cap. Active runs are never touched.
* Retention: `RUN_RETENTION_HOURS` (default 24). Runs older than the retention horizon are deleted on the same sweep.
* **Idle self-cancel**: a background sweep every 30 s checks `now - last_poll_at` for active runs. If it exceeds `RUN_IDLE_TIMEOUT_S` (default 600), the run's task is cancelled and the journal records `(cancelled, idle_timeout)`. Protects the browser pool from agents that walked away.
* `SessionEnd`: all active runs are cancelled and their summaries flushed before shutdown.

---

## 13. Run Journal (durability contract)

### 13.1 Layout

```
plugins/job-harness/data/.runs/<run_id>/
  request.json     # frozen SearchRequest, written at run start
  raw.jsonl        # append-only event log
  summary.json     # rewritten atomically every 250 ms and at terminal events
  run.lock         # advisory lock
```

### 13.2 `raw.jsonl` event types

```jsonc
{"type":"run_started",   "ts":"...","run_id":"...","request":{...}}
{"type":"source_started","ts":"...","source":"hh_ru","transport":"browser","deadline_ms":30000}
{"type":"source_progress","ts":"...","source":"hh_ru","raw_count":7,"note":"paginated page 2"}
{"type":"listing",       "ts":"...","source":"hh_ru","listing":{...}}
{"type":"filter_decision","ts":"...","listing_url":"...","kept":true,"reason":"remote_only"}
{"type":"dedupe_decision","ts":"...","kept":"url-A","dropped":["url-B"]}
{"type":"engine_progress","ts":"...","sources_done":4,"sources_total":9}
{"type":"source_status", "ts":"...","source":"hh_ru","status":"ok","failure_mode":null,
   "duration_ms":...,"raw_count":...,"after_filter_count":...,
   "after_dedupe_count":...,"company_missing_count":...,
   "retries":...,"flag_enforcement":{...},"anti_bot_signal":null,
   "error_class":null,"error_message":null}
{"type":"run_finished","ts":"...","state":"completed","final_listings_count":...,"errors":[...]}
```

Properties:

* Each record is a single line, UTF-8, `\n`-terminated. `os.write(fd, line)` then `os.fsync(fd)` before the engine moves on. Per-record fsync cost is in microseconds on local SSDs — measured.
* The `listing` record is written the moment the scraper returns the listing; not at source completion.
* The journal is the canonical history. `summary.json` is a derived index.

### 13.3 `summary.json`

```jsonc
{
  "run_id": "...",
  "state": "running"|"completed"|"cancelled"|"failed",
  "started_at": "...","ended_at": null|"...",
  "elapsed_ms": ...,
  "request": {...},
  "sources": { "<name>": SourceStatus, ... },
  "listings_count": ...,
  "flag_enforcement": {...},
  "result_sanity": {...},
  "errors": [...]
}
```

Atomic rewrite: write `summary.json.tmp`, fsync, `os.replace(tmp, summary.json)`. The reader sees either the old version or the new one — never a partial write.

### 13.4 Failure semantics

| Failure | Behaviour |
|---------|-----------|
| Process crash mid-listing-write | `fsync` after each record means at most one record is lost. All prior listings recoverable. |
| Process crash mid-summary-write | Atomic `os.replace` means the file is whole. |
| Server restart with active run | `state=running` becomes `(failed, server_restart)` on next startup. Journal stays intact. |
| `kill -9` during run | Same as crash. Acceptance test §15.8. |
| Disk full | Engine catches `OSError`; sets run `(failed, disk_full)`; attempts a final flush; exits cleanly. |
| Concurrent writer | `run.lock` prevents two writers on the same `run_id`. |

### 13.5 Reader API

```python
class RunJournal:
    @classmethod
    def open(cls, run_dir: Path) -> "RunJournal": ...

    def listings(self) -> Iterator[JobListing]: ...
    def source_statuses(self) -> dict[str, SourceStatus]: ...
    def state(self) -> RunState: ...
    def to_search_results(self, *, max_results: int) -> SearchResults: ...
    def tail_since(self, byte_offset: int) -> Iterator[dict]: ...
```

`search_status` and `search_results` are thin adapters over `RunJournal`. They do not depend on engine state. This means polling works during, after, across-cancel, and post-restart equally.

### 13.6 Compatibility

The existing CLI `--raw-jsonl=<path>` flag becomes a sugar for "also copy the journal to this path on completion". The canonical journal at `data/.runs/<run_id>/` is always written.

---

## 14. End-to-End Walk-Throughs

Each scenario traces user intent → agent decision → tool call → engine path → real-site forks. The behaviour described is what the system must produce.

### 14.1 "Найди remote QA вакансии в Армении"

Agent applies §10.4: query is broad-ish (country + flag) but `sources` defaults to `"all"`. Tool: `search_start(query="QA engineer", country="AM", remote_only=True)`.

Engine: source resolution drops sources not supporting `country=AM`; with strict flags, drops `vk`/`ibs` (`remote_only=unsupported`). Eligible: `hirify`, `staff_am`, `getmatch`, `company_directory`, plus universal scrapers. Dispatch concurrently.

Agent polls per §10.5 — typically completes after two polls.

Real-site forks and required behaviour:

* `staff.am` returns valid `__NEXT_DATA__` with 0 jobs → `(ok, raw_count=0)`. No error.
* `staff.am` SSR layout changes → `extract_next_data` raises → `(error, parse_error)`. If `staff_am` is the only AM source and `result_sanity` baseline says it should return ≥1 for the query → `result_sanity.staff_am.verdict="suspicious"`.
* `getmatch` returns `429 + Retry-After: 5`, budget 30 s → engine sleeps 5 s, retries, returns `ok`.
* `hirify` returns `200` with HTML (WAF page) → `fetch_json` raises `JSONDecodeError` → `(error, parse_error)`.
* Network drops mid-source → retries within budget; if exhausted, `(error, network_error)`. Outage triggers cool-down if it spreads to other sources.

### 14.2 "Прогон по всем источникам, без фильтров"

Tool: `search_start(query="QA engineer", sources="all")`.

Engine dispatches 16 sources concurrently; HTTP runner handles 11 of them eight at a time, BrowserPool handles 5 hh-family in two-at-a-time waves.

Real-site forks:

* hh.ru "Доступ ограничен" → `(blocked, anti_bot_page)`, `anti_bot_signal="Доступ ограничен"`.
* hh.ru Cloudflare interstitial (`Just a moment…`) → `(blocked, anti_bot_page)`.
* hh.ru reCAPTCHA iframe → `(blocked, captcha_page)`.
* rabota.by redirects to `/login` → `(blocked, login_redirect)`.
* habr_career rate-limits page 3 with 503 → `(rate_limited, http_503_retry_after)`; pages already collected are kept; status is `partial` if some pages succeeded.
* `it_jobs_uz` returns `{"data": null}` → parser fails → `(error, parse_error)`.
* `getmatch` two-step API: specs OK, offers fail → `(partial, multi_step_partial)`.
* Network jitter — 4 sources hit `network_error` in 10 s → cool-down trips → remaining HTTP sources `(error, global_network_outage)`.

Pool forks:

* hh.ru hangs in `page.goto` → `wait_for(timeout=30s)` raises → `(timeout, goto_timeout)`; context closed; other browser slot continues.
* Both contexts hang simultaneously → other browser sources queue on `Semaphore` → wall-clock = `page_timeout_ms + remaining_budget`.
* Three consecutive timeouts → pool recycle; affected source `(error, pool_recycled)`.
* `browser.is_connected()` flips False → next acquire rebuilds; in-flight source `(error, "BrowserDisconnected")`.

Cancellation:

* User presses Esc on the sync `search` (if used) → MCP `notifications/cancelled` → `CancelledError` in tool → partial result returned with `errors=["cancelled"]`.
* On a `search_start` run, the agent calls `search_cancel(run_id)` → engine task cancelled → `await page.goto` raises `CancelledError` immediately → page closes → run state `cancelled` within 2 s.
* Agent stops polling → idle sweep self-cancels after `RUN_IDLE_TIMEOUT_S` → `(cancelled, idle_timeout)`.
* `SessionEnd` → all runs cancelled and flushed.

### 14.3 "Теперь только junior"

After a completed run, the agent calls `search_refine(run_id, experience="junior", strict_refine=False)`. The engine reads the journal, applies the filter, returns a new `SearchResults` snapshot. Listings whose source declared `experience=unsupported` are tagged `filter_uncertain[experience]=True`. No scrapers are invoked. Returns in <200 ms.

### 14.4 "Найди и отдай прямые ссылки на работодателей"

Tool: `search_start(query="QA", country="AM", resolve=True)`. After aggregation, the engine fans out per-company `resolve_company_careers` calls under the BrowserPool with concurrency `min(max_contexts, 3)` and per-company deadline 8 s.

Forks:

* Google returns CAPTCHA → resolver records `error="Search failed: anti_bot_page"`; listing stays without a `direct_vacancy_url`. Search results unaffected.
* Cache hit (resolved 12 h ago, no roles) → returns cached result instantly.
* `vk` company → unified-registry lookup of `career:vk` scraper; opens page on the pool.
* Concurrent writes to `data/company-careers.json` are serialised under an `fcntl.flock` advisory lock; persistence uses `tmp + os.replace` for atomicity.

### 14.5 `search_company_careers(query="Python", country="Armenia")`

Engine partitions companies into ATS-API (HTTP) and browser companies, runs both groups concurrently. Per-company timeout enforced via `BrowserPool.run_with_page(timeout_ms=8000)`.

Forks:

* SPA career page → HTML fallback returns 0 → status `ok`, hits=0.
* LinkedIn jobs URL returns 403/999 → `(error, http_4xx)`. No retry that wastes budget.
* `max_companies > 20` requires `force_large=True`; `max_companies > 50` always refuses with a structured response pointing to `job-harness company-live-batch` (CLI). The MCP tool returns `{"error": "too_many_companies", "limit": 50, "use_cli": "job-harness company-live-batch ..."}`.

---

## 15. Acceptance Criteria

1. Synchronous `search` returns within `inline_timeout_ms` regardless of which sources hang. The agent's turn is never blocked longer than that ceiling.
2. `search_start` returns within 100 ms regardless of query breadth. `search_status` and `search_results` execute in under 50 ms each.
3. `search_cancel` causes `summary.json` to reach `state=cancelled` within 2 s. Over 100 cancel cycles, no page or thread leak.
4. Every `(source, flag)` pair in §3.4 is enforced according to its declared `FilterSupport`, verified by §11.3.
5. After 100 consecutive runs (soak test), browser memory growth ≤ 10 % of the first-run baseline.
6. With `strict_flags=True` and `remote_only=True`, no listing in the response has `remote=False` or `filter_uncertain.remote_only=True`.
7. Every response carries the full `flag_enforcement` map (§3.3) and the `result_sanity` block (§9.3).
8. `kill -9` during a run; restart; `search_results(run_id)` returns the listings that had been written to the journal before the kill (§13.4).
9. The journal is the source of truth: `search_status` and `search_results` work after the engine task is killed, restarted, or evicted.
10. Failure-mode taxonomy is closed: every test asserts a `(state, failure_mode)` pair from the §9.1 enums. The CI check in §11.7 fails if any code path produces a status without a matching `failure_mode`.
11. Anti-bot, captcha, and login-redirect detection each produce `state=blocked` with the correct `failure_mode` (§14.2).
12. The agent's `job-searcher` skill carries the §10.4 decision rule verbatim; verified by §11.6.
13. A run with no status poll for `RUN_IDLE_TIMEOUT_S` is self-cancelled with `(cancelled, idle_timeout)`.
14. `search_refine` returns within 200 ms on a 100-listing journal and never invokes a scraper.
15. `result_sanity` flags an all-zero result for a popular query/country/source as `suspicious`.
16. `python scripts/verify_repo.py full` is green.

---

## 16. Out of Scope

* Asyncio-native rewrite of HTTP scrapers — couples them to a specific event loop; the runner already provides parallelism via threads.
* Subprocess isolation for browser scrapers — unnecessary given async Playwright cancellation. Re-evaluated only if production-observed hangs survive `asyncio.CancelledError`.
* `company_career_batch.py` rewrite — it already runs concurrently in async Playwright. The MCP server's `search_company_careers` shares the same `BrowserPool` for in-band probes; the CLI batch stays as the operator-tool for full-directory sweeps.
